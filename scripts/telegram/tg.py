"""GitHub Actions 에서 쓰는 Telegram Bot API 헬퍼 (표준 라이브러리만 사용)."""

import json
import os
import urllib.request

API = "https://api.telegram.org"


def _token():
    return os.environ["TELEGRAM_BOT_TOKEN"]


def call(method, **params):
    req = urllib.request.Request(
        f"{API}/bot{_token()}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)["result"]


def send(chat_id, text, reply_to=None):
    params = {"chat_id": chat_id, "text": text[:4000], "link_preview_options": {"is_disabled": True}}
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    return call("sendMessage", **params)


def download(file_id):
    path = call("getFile", file_id=file_id)["file_path"]
    with urllib.request.urlopen(f"{API}/file/bot{_token()}/{path}", timeout=60) as res:
        return res.read()


def run_url():
    return f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
