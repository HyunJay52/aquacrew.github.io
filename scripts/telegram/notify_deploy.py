"""배포 워크플로 종료 후 운영 페이지에 새 포스트가 올라왔는지 확인하고 Telegram 으로 알린다."""

import html
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import tg

ROOT = Path(__file__).resolve().parents[2]
SITE = "https://aquacrew.kr"
TIMEOUT = 10 * 60  # GitHub Pages 반영까지 보통 1~3분


def changed_posts():
    before = os.environ.get("BEFORE", "")
    if not before or set(before) == {"0"}:
        before = "HEAD~1"
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM", before, "HEAD", "--", "content/post/*/index.md"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return [ROOT / path for path in out.split()]


def title_of(path):
    match = re.search(r"^title:\s*(.+)$", path.read_text(), re.M)
    return match.group(1).strip().strip("'\"") if match else path.parent.name


def fetch(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status, res.read().decode()


def find_live_url(title):
    """RSS 에서 제목으로 포스트 URL 을 찾고, 해당 페이지가 200 으로 열리는지 확인한다."""
    _, feed = fetch(f"{SITE}/index.xml?t={int(time.time())}")
    for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
        item_title = html.unescape(re.search(r"<title>(.*?)</title>", item, re.S).group(1))
        if item_title.strip() == title:
            link = re.search(r"<link>(.*?)</link>", item).group(1)
            status, _ = fetch(link)
            return link if status == 200 else None
    return None


def main():
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    if os.environ["BUILD_RESULT"] != "success":
        tg.send(chat_id, f"❌ 사이트 빌드/배포 실패\n로그: {tg.run_url()}")
        return

    posts = changed_posts()
    if not posts:
        return  # 테마 업데이트 등 콘텐츠 변경이 없는 배포는 알리지 않음

    pending = {title_of(p): p.parent.name for p in posts}
    found = {}
    deadline = time.time() + TIMEOUT
    while pending and time.time() < deadline:
        time.sleep(20)
        for title in list(pending):
            try:
                url = find_live_url(title)
            except Exception as err:
                print(f"check failed: {err}")
                continue
            if url:
                found[title] = (pending.pop(title), url)

    # 글마다 따로 보내 [post:폴더] 표식이 있는 메시지에 답장하면 그 글을 수정할 수 있게 한다
    hint = "✏️ 수정하려면 이 메시지에 답장하세요. (/help 참고)"
    for title, (folder, url) in found.items():
        tg.send(chat_id, f"🚀 운영 페이지 반영 완료\n{title}\n{url}\n[post:{folder}]\n{hint}")
    for title, folder in pending.items():
        tg.send(chat_id, f"⚠️ {title} — {TIMEOUT // 60}분 안에 운영 페이지에서 확인되지 않음\n[post:{folder}]\n로그: {tg.run_url()}")


if __name__ == "__main__":
    main()
