"""Telegram 으로 받은 훈련일지를 Hugo 포스트로 변환해 PR 을 만들거나, 기존 PR 에 사진을 추가한다.

repository_dispatch 이벤트(telegram-post / telegram-image)로 실행된다.
"""

import datetime
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import anthropic

import tg

ROOT = Path(__file__).resolve().parents[2]
POSTS = ROOT / "content/post"
SKILL = ROOT / ".claude/skills/new-post/SKILL.md"
MODEL = "claude-haiku-5-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "훈련 날짜 YYYY-MM-DD"},
        "markdown": {"type": "string", "description": "front matter 를 포함한 index.md 전체 (image 항목 제외)"},
        "sets": {
            "type": "array",
            "description": "원문의 거리 단위 세트 전체 (웜업~쿨다운). 합계는 스크립트가 이 값으로 다시 계산한다",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "예: 메인 ① 자유형 100m"},
                    "distance_m": {"type": "integer", "description": "1회 거리(m)"},
                    "reps": {"type": "integer", "description": "반복 횟수"},
                },
                "required": ["label", "distance_m", "reps"],
                "additionalProperties": False,
            },
        },
        "stated_total_m": {"type": "integer", "description": "원문에 직접 적힌 총 거리(m). 적혀 있지 않으면 0"},
        "notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "검수자가 확인해야 할 추측·불확실한 부분. 없으면 빈 배열",
        },
    },
    "required": ["date", "markdown", "sets", "stated_total_m", "notes"],
    "additionalProperties": False,
}

AUTO_MODE = """

## 자동 모드 (Telegram → PR)

지금은 사용자와 대화할 수 없는 자동 실행이다. 위 지침 중 "사용자에게 묻는다/확인한다" 는 모두
가장 합리적인 값으로 결정하고, 그 결정과 이유를 notes 에 한 줄씩 남긴다.
폴더 생성·이미지 복사·빌드·커밋은 스크립트가 처리하므로 index.md 내용만 만든다.
front matter 에 image 항목은 넣지 않는다(스크립트가 추가). 본문에 이미지 문법도 넣지 않는다.
sets 에는 원문에 나온 거리 단위 세트를 빠짐없이, 원문 그대로의 거리·횟수로 적는다.
(예: "50m × 8 두 세트" → 50m × 8 항목 2개. 시간만 있고 거리가 없는 세트는 제외하고 notes 에 적는다.)
"""


def total_in_markdown(markdown):
    """요약표 합계 행에 적힌 총 거리(m)."""
    row = next((line for line in markdown.splitlines() if line.startswith("|") and "합계" in line), "")
    match = re.findall(r"([\d,]+)\s*m", row)
    return int(match[-1].replace(",", "")) if match else None


def check_total(result):
    """세트 목록으로 합계를 다시 계산해 요약표·원문 합계와 다르면 경고 문구를 돌려준다."""
    computed = sum(s["distance_m"] * s["reps"] for s in result["sets"])
    warnings = []
    table = total_in_markdown(result["markdown"])
    if table is None:
        warnings.append(f"⚠️ 요약표에서 합계를 찾지 못함 (세트 합산 {computed:,}m)")
    elif table != computed:
        warnings.append(f"⚠️ 합계 불일치: 요약표 {table:,}m ≠ 세트 합산 {computed:,}m")
    stated = result["stated_total_m"]
    if stated and stated != computed:
        warnings.append(f"⚠️ 합계 불일치: 원문 {stated:,}m ≠ 세트 합산 {computed:,}m")
    breakdown = " + ".join(f"{s['distance_m']}×{s['reps']}" for s in result["sets"])
    return warnings, f"세트 합산 {computed:,}m ({breakdown})"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def gh(*args):
    return subprocess.run(["gh", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def latest_example():
    paths = sorted(glob.glob(str(POSTS / "training_*/index.md")))
    return Path(paths[-1]).read_text() if paths else ""


def convert(text):
    client = anthropic.Anthropic()
    today = datetime.date.today().isoformat()
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SKILL.read_text() + AUTO_MODE,
        messages=[{
            "role": "user",
            "content": (
                f"오늘 날짜: {today}\n\n"
                f"<latest_post>\n{latest_example()}\n</latest_post>\n\n"
                f"<training_log>\n{text}\n</training_log>\n\n"
                "위 훈련일지를 latest_post 와 같은 형식의 index.md 로 변환해줘."
            ),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude 가 변환을 거절했습니다.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("변환 결과가 너무 길어 잘렸습니다.")
    body = next(block.text for block in response.content if block.type == "text")
    return json.loads(body)


def front_matter_value(markdown, key):
    match = re.search(rf"^{key}:\s*(.+)$", markdown, re.M)
    return match.group(1).strip().strip("'\"") if match else ""


def set_thumbnail(markdown, filename):
    """front matter 의 image 를 filename 으로 지정 (없으면 tags 앞에 추가)."""
    _, fm, body = markdown.split("---\n", 2)
    if re.search(r"^image:.*$", fm, re.M):
        fm = re.sub(r"^image:.*$", f"image: {filename}", fm, flags=re.M)
    elif re.search(r"^tags:", fm, re.M):
        fm = re.sub(r"^tags:", f"image: {filename}\ntags:", fm, count=1, flags=re.M)
    else:
        fm += f"image: {filename}\n"
    return f"---\n{fm}---\n{body}"


def has_thumbnail(markdown):
    return bool(re.search(r"^image:[ \t]*\S", markdown.split("---\n", 2)[1], re.M))


def image_name(folder, date, ext):
    count = len([p for p in folder.iterdir() if p.name != "index.md"])
    return f"{date}-{count + 1:02d}{ext}"


def new_post(p):
    result = convert(p["text"])
    date = result["date"]
    datetime.date.fromisoformat(date)  # 형식 검증

    slug = f"training_{date.replace('-', '')}"
    folder = POSTS / slug
    n = 2
    while folder.exists():
        folder = POSTS / f"{slug}_{n}"
        n += 1
    folder.mkdir(parents=True)

    markdown = result["markdown"].rstrip() + "\n"
    if p.get("file_id"):
        name = image_name(folder, date, p["ext"])
        (folder / name).write_bytes(tg.download(p["file_id"]))
        markdown = set_thumbnail(markdown, name)
    (folder / "index.md").write_text(markdown)

    title = front_matter_value(markdown, "title") or f"{date} 훈련일지"
    branch = f"post/{folder.name}-{p['message_id']}"
    git("checkout", "-b", branch)
    git("add", str(folder))
    git("commit", "-m", f"feat: {title}")
    git("push", "-u", "origin", branch)

    warnings, total = check_total(result)
    notes = "\n".join(f"- {note}" for note in [*warnings, total, *result["notes"]])
    pr_body = (
        f"Telegram 으로 받은 훈련일지를 자동 변환한 포스트입니다.\n\n"
        f"### 검수 포인트\n{notes}\n\n"
        f"<details><summary>원문</summary>\n\n```\n{p['text']}\n```\n</details>\n\n"
        f"🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    )
    url = gh("pr", "create", "--base", "main", "--head", branch, "--title", f"feat: {title}", "--body", pr_body)
    number = url.rstrip("/").rsplit("/", 1)[-1]

    tg.send(
        p["chat_id"],
        f"{'⚠️' if warnings else '✅'} PR #{number} 생성: {title}\n{url}\n\n"
        f"검수 포인트\n{notes}\n\n"
        f"📷 사진 추가: 이 메시지에 답장으로 사진 전송 (캡션 '썸네일' → 썸네일 지정)\n"
        f"🔀 검수 후 GitHub 에서 Merge 하면 배포됩니다.",
        reply_to=p["message_id"],
    )


def add_image(p):
    number = str(p["pr"])
    info = json.loads(gh("pr", "view", number, "--json", "headRefName,state,files"))
    if info["state"] != "OPEN":
        tg.send(p["chat_id"], f"⚠️ PR #{number} 은 이미 {info['state']} 상태라 사진을 추가할 수 없어요.", reply_to=p["message_id"])
        return
    index = next(
        (f["path"] for f in info["files"] if re.fullmatch(r"content/post/[^/]+/index\.md", f["path"])),
        None,
    )
    if not index:
        raise RuntimeError(f"PR #{number} 에서 포스트(index.md)를 찾지 못했습니다.")

    branch = info["headRefName"]
    data = tg.download(p["file_id"])
    folder = ROOT / Path(index).parent
    date = re.search(r"(\d{4})(\d{2})(\d{2})", folder.name).groups()
    date = "-".join(date)

    # 앨범으로 여러 장이 동시에 오면 push 가 충돌할 수 있어 최신 브랜치 기준으로 재시도
    for attempt in range(5):
        git("fetch", "origin", branch)
        git("checkout", "-B", branch, f"origin/{branch}")
        markdown = (folder / "index.md").read_text()
        name = image_name(folder, date, p["ext"])
        (folder / name).write_bytes(data)
        if p.get("thumbnail") or not has_thumbnail(markdown):
            markdown = set_thumbnail(markdown, name)
            kind = "썸네일"
        else:
            markdown = markdown.rstrip() + f"\n\n![]({name})\n"
            kind = "본문 이미지"
        (folder / "index.md").write_text(markdown)
        git("add", str(folder))
        git("commit", "-m", f"feat: {name} 이미지 추가")
        try:
            git("push", "origin", branch)
            break
        except subprocess.CalledProcessError:
            if attempt == 4:
                raise
    tg.send(p["chat_id"], f"🖼 PR #{number} 에 {kind} 추가: {name}", reply_to=p["message_id"])


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    payload = event["client_payload"]
    try:
        if event["action"] == "telegram-post":
            new_post(payload)
        elif event["action"] == "telegram-image":
            add_image(payload)
    except Exception as err:
        detail = err.stderr if isinstance(err, subprocess.CalledProcessError) else err
        tg.send(payload["chat_id"], f"⚠️ 처리 실패: {detail}\n로그: {tg.run_url()}", reply_to=payload["message_id"])
        raise


if __name__ == "__main__":
    sys.exit(main())
