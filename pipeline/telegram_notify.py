"""從 GitHub Actions 端主動發 Telegram 訊息用（跟 Worker 那邊收 webhook 是分開的方向）。"""

from __future__ import annotations

import os

import requests

TELEGRAM_MESSAGE_LIMIT = 4000  # Telegram 實際上限 4096，抓保守一點的安全值


def send_message(chat_id: int, text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[telegram] 缺少 TELEGRAM_BOT_TOKEN，略過通知")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for i in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        chunk = text[i : i + TELEGRAM_MESSAGE_LIMIT]
        resp = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=30)
        if not resp.ok:
            print(f"[telegram] 傳送失敗：{resp.status_code} {resp.text[:200]}")
