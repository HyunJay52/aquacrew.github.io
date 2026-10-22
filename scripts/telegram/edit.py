"""검수 중이거나 이미 게시된 포스트를 Telegram 답장으로 수정한다 (repository_dispatch: telegram-edit).

수정 대상
  pr    봇 메시지의 'PR #번호' — 열린 PR 이면 그 브랜치에, Merge 된 PR 이면 게시된 글로 취급
  post  봇 메시지의 '[post:폴더]' — 게시된 글. edit/<폴더> 브랜치에 커밋하고 수정 PR 을 연다

게시된 글도 main 에 바로 반영하지 않고 수정 PR 을 거쳐 검수 후 Merge 한다.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import handle as h
import notify_deploy
import tg

FOLDER = re.compile(r"[A-Za-z0-9_-]+")

EDIT_MODE = """

## 수정 모드 (Telegram → PR)

지금은 사용자와 대화할 수 없는 자동 실행이다. 기존 index.md 를 수정 요청에 맞게 고친다.
- 요청과 관계없는 부분은 글자 하나도 바꾸지 않는다. 형식·들여쓰기·이모지도 그대로 둔다.
- 날짜를 바꾸면 front matter 의 date 와 제목·본문 머리글에 들어간 날짜를 함께 바꾼다.
- front matter 의 image, aliases 항목과 본문의 이미지(![](...))는 그대로 둔다. 사진은 별도 명령으로 처리된다.
- 거리·횟수가 바뀌면 요약표와 합계도 맞춘다. sets 에는 수정 후 기준의 세트 전체를 적고 stated_total_m 은 0.
- 훈련일지가 아닌 글(대회·이벤트 등)이면 sets 는 빈 배열, stated_total_m 은 0.
- date 에는 수정 후 front matter 의 날짜(YYYY-MM-DD)를 적는다.
- 요청을 이해할 수 없거나 적용할 수 없으면 markdown 을 원본 그대로 두고 notes 에 이유를 적는다.
- HTML 태그는 <br>, <sub>...</sub> 외에는 쓰지 않는다.
"""


@dataclass
class Target:
    folder: str
    branch: str
    base: str  # 작업을 시작할 원격 브랜치 (main 또는 branch 자신)
    published: bool  # 운영 사이트에 게시된 글인지 (제목 변경 시 이전 주소 연결용)
    pr: str | None = None
    url: str | None = None

    @property
    def path(self):
        return h.POSTS / self.folder

    @property
    def ref(self):
        """봇 메시지에 넣어 다음 답장의 대상을 찾게 하는 표식."""
        return f"PR #{self.pr}" if self.pr else f"[post:{self.folder}]"


# ── 대상 찾기 ──────────────────────────────────────────────


def open_edit_pr(branch):
    out = h.gh("pr", "list", "--head", branch, "--state", "open", "--json", "number,url", "-q", ".[0] | [.number, .url] | @tsv")
    return out.split("\t") if out else (None, None)


def published_target(folder):
    if not FOLDER.fullmatch(folder) or not (h.POSTS / folder / "index.md").exists():
        raise RuntimeError(f"게시된 글을 찾지 못했어요: {folder}")
    branch = f"edit/{folder}"
    pr, url = open_edit_pr(branch)
    return Target(folder, branch, branch if pr else "main", True, pr, url)


def resolve(p):
    if p.get("post"):
        return published_target(p["post"])

    number = str(int(p["pr"]))
    info = json.loads(h.gh("pr", "view", number, "--json", "headRefName,isCrossRepository,state,files,url"))
    index = next((f["path"] for f in info["files"] if re.fullmatch(r"content/post/[^/]+/index\.md", f["path"])), None)
    if not index:
        raise RuntimeError(f"PR #{number} 에서 포스트(index.md)를 찾지 못했어요.")
    folder = Path(index).parent.name

    if info["state"] == "MERGED":
        return published_target(folder)
    if info["state"] != "OPEN":
        raise RuntimeError(f"PR #{number} 은 닫힌 상태라 수정할 수 없어요.")
    # 봇이 만든 PR(이 저장소의 post/, edit/ 브랜치)에만 커밋한다
    branch = info["headRefName"]
    if info["isCrossRepository"] or not branch.startswith(("post/", "edit/")):
        raise RuntimeError(f"PR #{number} 은 봇이 만든 PR 이 아니라 수정할 수 없어요.")
    return Target(folder, branch, branch, branch.startswith("edit/"), number, info["url"])


# ── 브랜치 작업 ────────────────────────────────────────────


def checkout(target):
    h.git("fetch", "origin", target.base)
    h.git("checkout", "-B", target.branch, f"origin/{target.base}")


def commit(target, mutate):
    """target 브랜치 최신 상태에서 mutate() 를 적용해 커밋·push 한다. 변경이 없으면 None."""
    # 앨범 사진처럼 같은 브랜치에 동시에 커밋하면 push 가 충돌할 수 있어 최신 상태에서 다시 적용
    for attempt in range(5):
        checkout(target)
        message, summary = mutate()
        h.git("add", "-A", str(target.path))
        if not h.git("status", "--porcelain", str(target.path)):
            return None
        h.git("commit", "-m", message)
        try:
            h.git("push", "-u", "origin", target.branch)
            break
        except subprocess.CalledProcessError:
            if attempt == 4:
                raise
            # 다른 실행이 먼저 수정 브랜치를 만들었다면 그 위에서 다시 적용
            if h.git("ls-remote", "--heads", "origin", target.branch):
                target.base = target.branch
    if not target.pr:
        open_pr(target)
    return summary


def open_pr(target):
    pr, url = open_edit_pr(target.branch)
    if not pr:
        title = h.front_matter_value((target.path / "index.md").read_text(), "title")
        body = (
            f"게시된 글 `{target.folder}` 을 Telegram 으로 수정한 PR 입니다.\n"
            f"봇 메시지에 답장하면 이 PR 에 계속 수정이 쌓입니다. 검수 후 Merge 하면 운영 페이지에 반영됩니다.\n\n"
            f"🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        )
        try:
            url = h.gh("pr", "create", "--base", "main", "--head", target.branch, "--title", f"fix: {title} 수정", "--body", body)
            pr = url.rstrip("/").rsplit("/", 1)[-1]
        except subprocess.CalledProcessError:
            pr, url = open_edit_pr(target.branch)  # 동시에 다른 실행이 먼저 만든 경우
    target.pr, target.url = pr, url


# ── 마크다운 · 사진 도우미 ──────────────────────────────────


def split(markdown):
    _, fm, body = markdown.split("---\n", 2)
    return fm, body


def images(folder):
    return sorted((p for p in folder.iterdir() if p.suffix.lower() in h.IMAGE_EXTS), key=lambda p: p.name)


def thumbnail(markdown):
    return h.front_matter_value(split(markdown)[0], "image")


def body_ref(name):
    return re.compile(rf"!\[[^\]]*\]\(\s*(?:\./)?{re.escape(name)}\s*\)")


def remove_refs(markdown, name):
    fm, body = split(markdown)
    if h.front_matter_value(fm, "image") == name:
        fm = re.sub(r"^image:.*\n", "", fm, flags=re.M)
    body = body_ref(name).sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).rstrip() + "\n"
    return f"---\n{fm}---\n{body}"


def replace_refs(markdown, old, new):
    fm, body = split(markdown)
    if h.front_matter_value(fm, "image") == old:
        fm = re.sub(r"^image:.*$", f"image: {new}", fm, flags=re.M)
    body = body_ref(old).sub(f"![]({new})", body)
    return f"---\n{fm}---\n{body}"


def add_alias(markdown, path):
    fm, body = split(markdown)
    if re.search(rf"^\s*-\s*['\"]?{re.escape(path)}['\"]?\s*$", fm, re.M):
        return markdown
    if re.search(r"^aliases:", fm, re.M):
        fm = re.sub(r"^aliases:.*\n", lambda m: m.group(0) + f"  - {path}\n", fm, count=1, flags=re.M)
    else:
        fm += f"aliases:\n  - {path}\n"
    return f"---\n{fm}---\n{body}"


def post_date(markdown):
    return h.front_matter_value(markdown, "date")[:10] or datetime.date.today().isoformat()


def pick(folder, numbers):
    files = images(folder)
    bad = [n for n in numbers if not 1 <= n <= len(files)]
    if bad:
        raise RuntimeError(f"사진 번호 {bad} 가 없어요. /사진 으로 번호를 확인해주세요.")
    return [files[n - 1] for n in numbers]


def photo_list(target):
    markdown = (target.path / "index.md").read_text()
    thumb = thumbnail(markdown)
    lines = []
    for n, f in enumerate(images(target.path), 1):
        used = "썸네일" if f.name == thumb else "본문" if body_ref(f.name).search(markdown) else "미사용"
        lines.append(f"{n}. {f.name} ({used})")
    return "\n".join(lines) or "사진 없음"


# ── 수정 작업 ──────────────────────────────────────────────


def edit_text(target, instruction):
    old = (target.path / "index.md").read_text()
    result = h.ask(
        h.SKILL.read_text() + EDIT_MODE,
        f"<current_post>\n{old}\n</current_post>\n\n<edit_request>\n{instruction}\n</edit_request>\n\n"
        "edit_request 대로 current_post 를 수정한 index.md 전체를 돌려줘.",
    )
    new, warnings = h.neutralize_html(result["markdown"].rstrip() + "\n")
    datetime.date.fromisoformat(post_date(new))  # 날짜 형식 검증

    # 사진은 텍스트 수정으로 바뀌지 않게 보호
    if thumbnail(old) and thumbnail(new) != thumbnail(old):
        new = h.set_thumbnail(new, thumbnail(old))
        warnings.append("⚠️ 텍스트 수정 중 썸네일이 바뀌어 원래대로 되돌림")
    lost = [f.name for f in images(target.path) if body_ref(f.name).search(old) and not body_ref(f.name).search(new)]
    if lost:
        warnings.append(f"⚠️ 본문에서 사라진 사진 확인 필요: {', '.join(lost)}")

    changes = []
    for key in ("title", "date"):
        before, after = h.front_matter_value(old, key), h.front_matter_value(new, key)
        if before != after:
            changes.append(f"{key}: {before} → {after}")

    # 게시된 글의 제목이 바뀌면 주소(/p/제목/)도 바뀌므로 이전 주소를 aliases 로 연결
    if target.published and h.front_matter_value(old, "title") != h.front_matter_value(new, "title"):
        live = notify_deploy.find_live_url(h.front_matter_value(old, "title"))
        if live:
            new = add_alias(new, urllib.parse.unquote(urllib.parse.urlparse(live).path))
            changes.append("이전 주소는 새 주소로 자동 연결 (aliases)")
        else:
            warnings.append("⚠️ 제목 변경으로 주소가 바뀌는데 이전 주소를 찾지 못해 연결하지 못함")

    if h.total_in_markdown(new) is not None and result["sets"]:
        total_warnings, total = h.check_total({**result, "markdown": new})
        warnings += total_warnings
        changes.append(total)

    (target.path / "index.md").write_text(new)
    summary = "\n".join(f"- {line}" for line in [*warnings, *changes, *result["notes"]]) or "- 본문 수정"
    return f"fix: {h.front_matter_value(new, 'title')} 내용 수정", summary


def add_photo(target, data, ext, as_thumbnail):
    markdown = (target.path / "index.md").read_text()
    name = h.image_name(target.path, post_date(markdown), ext)
    (target.path / name).write_bytes(data)
    old_thumb = thumbnail(markdown)
    if as_thumbnail or not old_thumb:
        markdown = h.set_thumbnail(markdown, name)
        summary = f"썸네일 지정: {name}"
        # 이전 썸네일이 본문에도 없으면 더 이상 쓰이지 않으므로 삭제
        if old_thumb and (target.path / old_thumb).exists() and not body_ref(old_thumb).search(markdown):
            (target.path / old_thumb).unlink()
            summary += f" (이전 썸네일 {old_thumb} 삭제)"
    else:
        markdown = markdown.rstrip() + f"\n\n![]({name})\n"
        summary = f"본문 사진 추가: {name}"
    (target.path / "index.md").write_text(markdown)
    return f"feat: {summary}", summary


def replace_photo(target, number, data, ext):
    [old] = pick(target.path, [number])
    markdown = (target.path / "index.md").read_text()
    new = h.image_name(target.path, post_date(markdown), ext)
    (target.path / new).write_bytes(data)
    old.unlink()
    (target.path / "index.md").write_text(replace_refs(markdown, old.name, new))
    summary = f"{number}번 사진 교체: {old.name} → {new}"
    return f"fix: {summary}", summary


def set_existing_thumbnail(target, number):
    [file] = pick(target.path, [number])
    markdown = (target.path / "index.md").read_text()
    old_thumb = thumbnail(markdown)
    markdown = h.set_thumbnail(markdown, file.name)
    summary = f"썸네일 변경: {file.name}"
    if old_thumb and old_thumb != file.name and (target.path / old_thumb).exists() and not body_ref(old_thumb).search(markdown):
        (target.path / old_thumb).unlink()
        summary += f" (이전 썸네일 {old_thumb} 삭제)"
    (target.path / "index.md").write_text(markdown)
    return f"fix: {summary}", summary


def delete_photos(target, numbers):
    files = pick(target.path, numbers)
    markdown = (target.path / "index.md").read_text()
    for f in files:
        markdown = remove_refs(markdown, f.name)
        f.unlink()
    (target.path / "index.md").write_text(markdown)
    summary = "사진 삭제: " + ", ".join(f.name for f in files)
    if not thumbnail(markdown):
        summary += "\n⚠️ 썸네일이 없어졌어요. 사진에 캡션 '썸네일' 로 답장하면 새로 지정돼요."
    return f"fix: {summary.splitlines()[0]}", summary


# ── 진입점 ────────────────────────────────────────────────


def find_posts(query):
    """날짜(2026-10-18, 20261018) 또는 제목 일부로 게시된 글을 찾는다."""
    digits = re.sub(r"\D", "", query)
    matches = []
    for index in sorted(h.POSTS.glob("*/index.md"), reverse=True):
        markdown = index.read_text()
        title = h.front_matter_value(markdown, "title")
        date = h.front_matter_value(markdown, "date")[:10]
        if query in title or (len(digits) >= 4 and digits in date.replace("-", "") + index.parent.name):
            matches.append((index.parent.name, title, date))
    return matches[:5]


def run(p):
    chat, msg = p["chat_id"], p["message_id"]

    if p["op"] == "find":
        found = find_posts(p["text"].strip())
        if not found:
            tg.send(chat, f"'{p['text']}' 에 해당하는 글이 없어요. 날짜(2026-10-18)나 제목 일부로 찾아주세요.", reply_to=msg)
        for folder, title, date in found:
            tg.send(chat, f"📄 {title} ({date})\n[post:{folder}]\n✏️ 이 메시지에 답장으로 수정 요청 (/help 참고)", reply_to=msg)
        return

    target = resolve(p)

    if p["op"] == "list":
        checkout(target)
        tg.send(chat, f"🖼 사진 목록 — {target.ref}\n{photo_list(target)}\n\n/삭제 번호 · /썸네일 번호 · 사진+캡션 '/교체 번호'", reply_to=msg)
        return

    data = tg.download(p["file_id"]) if p.get("file_id") else None
    ext = h.image_ext(p) if data else None
    numbers = p.get("indexes") or []
    ops = {
        "text": lambda: edit_text(target, p["text"]),
        "add": lambda: add_photo(target, data, ext, False),
        "thumbnail": lambda: add_photo(target, data, ext, True),
        "replace": lambda: replace_photo(target, numbers[0], data, ext),
        "thumbnail_index": lambda: set_existing_thumbnail(target, numbers[0]),
        "delete": lambda: delete_photos(target, numbers),
    }
    summary = commit(target, ops[p["op"]])
    if summary is None:
        tg.send(chat, f"변경된 내용이 없어요. — {target.ref}", reply_to=msg)
        return
    tg.send(
        chat,
        f"✏️ PR #{target.pr} 에 반영했어요.\n{target.url}\n\n{summary}\n\n"
        f"계속 수정하려면 이 메시지에 답장하세요.\n🔀 검수 후 Merge 하면 운영 페이지에 반영됩니다.",
        reply_to=msg,
    )
