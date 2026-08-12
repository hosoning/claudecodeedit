"""
配音神器 (peiyinshenqi.com) 自動化配音。

背景：這個網站的「合成配音」請求(POST /pc/v220/tts/synthFormat)帶有逐次變動的
X-SIGN 簽名，回應內容也是加密的（"encryption": true），簽名演算法與加解密方式
都沒有原始碼可查，逆向成本高。因此改用瀏覽器自動化(Playwright)——讓網站自己的
JS 去處理簽名/解密，我們只負責操作 UI、抓最終產生的音檔。

登入方式是掃碼，沒有帳密。做法是把已登入瀏覽器的 localStorage（tts:user /
tts:uservip / WXOBS_USER_IDENTIFIER_KEY 等）原封不動注入到 Playwright 的瀏覽器
context 裡，跳過重新掃碼。這組資料存在 GitHub secret `PEIYINSHENQI_SESSION`
（JSON 字串）。

⚠️ 這組 session 是使用者的登入憑證，可能會過期(確切時效未知)。過期時網站會
顯示需要重新登入，這裡的 TODO 部分要補上偵測邏輯並透過 Telegram 通知使用者
重新掃碼、匯出、更新 secret。

⚠️ 下面的 DOM selector（TEXTAREA_SELECTOR / SYNTH_BUTTON_SELECTOR 等）是
根據截圖與已知的頁面行為做的最佳猜測，還沒有實際跑過驗證，第一次在 GitHub
Actions 執行大概率需要依實際錯誤訊息/截圖調整。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

from playwright.sync_api import sync_playwright, Page, BrowserContext

TTS_PAGE_URL = "https://peiyinshenqi.com/tts/index"
ORIGIN = "https://peiyinshenqi.com"
MAX_CHARS_PER_CALL = 8000  # 站方單次合成上限（實測得知）

# TODO: 這些是最佳猜測，第一次實跑後需要依實際 DOM 校正
TEXTAREA_SELECTOR = "textarea"
SYNTH_BUTTON_SELECTOR = "text=合成配音"
DOWNLOAD_BUTTON_SELECTOR = "text=下载配音"
LOGIN_REQUIRED_HINT_SELECTOR = "text=登录"  # 用來偵測 session 是否過期


def split_into_chunks(text: str, max_chars: int = MAX_CHARS_PER_CALL) -> list[str]:
    """依段落邊界切割文本，避免超過單次合成上限。"""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > max_chars:
            if current:
                chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def inject_session(context: BrowserContext, session: dict[str, str]) -> None:
    """把登入態灌進瀏覽器 context，注入 localStorage，跳過掃碼登入。"""
    script = f"""
    (() => {{
        const kv = {json.dumps(session)};
        for (const [k, v] of Object.entries(kv)) {{
            window.localStorage.setItem(k, v);
        }}
    }})();
    """
    context.add_init_script(script)


def wait_for_login_check(page: Page) -> None:
    if page.locator(LOGIN_REQUIRED_HINT_SELECTOR).count() > 0:
        raise RuntimeError(
            "配音神器 session 疑似已過期，需要重新掃碼登入並更新 PEIYINSHENQI_SESSION secret"
        )


def synthesize_chunk(page: Page, text: str, download_dir: Path) -> Path:
    page.goto(TTS_PAGE_URL, wait_until="networkidle")
    wait_for_login_check(page)

    textarea = page.locator(TEXTAREA_SELECTOR).first
    textarea.click()
    textarea.fill("")
    textarea.fill(text)

    with page.expect_download(timeout=120_000) as download_info:
        page.locator(SYNTH_BUTTON_SELECTOR).first.click()
        page.locator(DOWNLOAD_BUTTON_SELECTOR).first.click()

    download = download_info.value
    dest = download_dir / download.suggested_filename
    download.save_as(dest)
    return dest


def synthesize_script(
    text: str,
    output_dir: Path,
    session: dict[str, str],
    headless: bool = True,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks = split_into_chunks(text)
    results: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        inject_session(context, session)
        page = context.new_page()

        for i, chunk in enumerate(chunks):
            audio_path = synthesize_chunk(page, chunk, output_dir)
            renamed = output_dir / f"part_{i:03d}.mp3"
            audio_path.rename(renamed)
            results.append(renamed)

        browser.close()

    return results


def _load_session_from_env() -> dict[str, str]:
    raw = os.environ.get("PEIYINSHENQI_SESSION")
    if not raw:
        raise RuntimeError("缺少環境變數 PEIYINSHENQI_SESSION")
    return json.loads(raw)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--text-file", required=True, help="要配音的文字檔路徑")
    parser.add_argument("--output-dir", required=True, help="輸出音檔片段的目錄")
    args = parser.parse_args()

    session_data = _load_session_from_env()
    script_text = Path(args.text_file).read_text(encoding="utf-8")
    parts = synthesize_script(script_text, Path(args.output_dir), session_data)
    print(f"生成 {len(parts)} 個音檔片段：")
    for part in parts:
        print(" -", part)
