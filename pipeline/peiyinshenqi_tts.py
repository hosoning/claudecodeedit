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

# 第一次實跑（2026-08-16）確認：登入態注入有效（頁面顯示「包终身 VIP /
# 到期时间：永久」），合成配音/下载配音兩個按鈕的文字選擇器也在頁面上找得到。
# 唯獨 <textarea> 完全不存在（count=0），但頁面上有字數統計「284/8000」，代表
# 編輯區其實是某種富文本 contenteditable（能插入「停頓」之類的行內元件，
# 不是純文字 textarea 能做到的），所以改成依序嘗試多個候選 selector。
EDITOR_SELECTOR_CANDIDATES = [
    '[contenteditable="true"]',
    '.ql-editor',
    'div[role="textbox"]',
    'textarea',
]
SYNTH_BUTTON_SELECTOR = "text=合成配音"
DOWNLOAD_BUTTON_SELECTOR = "text=下载配音"
LOGIN_REQUIRED_HINT_SELECTOR = "text=登录"  # 用來偵測 session 是否過期（已知會誤判，見 wait_for_login_check）


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
    # NOTE: 這個 selector 是猜的，目前已知會誤判（頁面上可能本來就有不相關的
    # "登录" 文字）。先只印警告、不中斷流程，等靠 debug 截圖確認真正的登入態
    # 判斷方式後再改回會丟例外。
    if page.locator(LOGIN_REQUIRED_HINT_SELECTOR).count() > 0:
        print("⚠️  偵測到頁面上有「登录」文字，可能是 session 過期，也可能是誤判，繼續執行以便截圖排查")


def dump_debug(page: Page, output_dir: Path, label: str) -> None:
    """存檔（給人事後下載看）+ 直接印到 stdout（GitHub Actions log 一定看得到，
    不像 artifact 還要另外下載）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(output_dir / f"debug_{label}.png"), full_page=True)
    except Exception as e:  # noqa: BLE001
        print(f"截圖失敗（{label}）：{e}")
    try:
        html = page.content()
        (output_dir / f"debug_{label}.html").write_text(html, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"HTML dump 失敗（{label}）：{e}")

    print(f"----- page debug [{label}] -----")
    try:
        print("title:", page.title())
        print("url:", page.url)
        for tag in ("textarea", "input", "button", "iframe"):
            print(f"count({tag}):", page.locator(tag).count())
        for i, frame in enumerate(page.frames):
            print(f"frame[{i}] url:", frame.url)
        body_text = page.locator("body").inner_text()
        print("body text (前 1500 字):")
        print(body_text[:1500])
    except Exception as e:  # noqa: BLE001
        print(f"page debug 收集失敗（{label}）：{e}")
    print(f"----- end page debug [{label}] -----")


def find_editor_locator(page: Page):
    for sel in EDITOR_SELECTOR_CANDIDATES:
        count = page.locator(sel).count()
        print(f"editor selector candidate {sel!r}: count={count}")
        if count > 0:
            return page.locator(sel).first
    raise RuntimeError(
        f"找不到配音文字編輯區，所有候選 selector 都是 0 個元素：{EDITOR_SELECTOR_CANDIDATES}"
    )


def synthesize_chunk(page: Page, text: str, download_dir: Path) -> Path:
    # 這個 SPA 疑似有持續背景網路活動（心跳/分析類請求），導致
    # wait_until="networkidle" 時好時壞（實測有時 8 秒完成、有時 30 秒直接
    # timeout）。改用 domcontentloaded（只等 DOM 就緒，不等網路安靜）+
    # 固定緩衝時間讓前端 JS 有機會渲染，穩定性好很多。
    page.goto(TTS_PAGE_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(5_000)
    wait_for_login_check(page)
    dump_debug(page, download_dir, "after_goto")

    try:
        editor = find_editor_locator(page)
        editor.click()
        editor.fill("")
        editor.fill(text)
        dump_debug(page, download_dir, "after_fill")

        with page.expect_download(timeout=120_000) as download_info:
            page.locator(SYNTH_BUTTON_SELECTOR).first.click()
            dump_debug(page, download_dir, "after_synth_click")

            # 這個網站是 Element Plus (Vue) 做的：按「合成配音」只會跳出一個
            # 「配音清单」確認 dialog（列出字數/語速/聲音等），要再按一次
            # 「开始合成」才會真正送出合成請求。實測不等這步、直接找「下载配音」
            # 會被 el-loading-mask / dialog 的 pointer-events 擋住，卡到 timeout。
            confirm_btn = page.locator("text=开始合成").first
            confirm_btn.wait_for(state="visible", timeout=10_000)
            confirm_btn.click()
            dump_debug(page, download_dir, "after_confirm_click")

            # 合成過程中畫面會有 loading mask，等它消失代表合成完成，
            # 這時「下载配音」才點得到。
            page.locator(".el-loading-mask").first.wait_for(state="detached", timeout=90_000)
            dump_debug(page, download_dir, "after_loading_done")

            page.locator(DOWNLOAD_BUTTON_SELECTOR).first.click()
    except Exception:
        dump_debug(page, download_dir, "on_error")
        raise

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
        # 逆向分析發現這個網站是 WeChat 小程序的 web wrapper，判斷是否在小程序
        # 環境內是靠這個 header；一般瀏覽器直接開網址不會帶，可能導致顯示的是
        # 「請在小程序內開啟」之類的 fallback 內容而不是真正的工具頁面。
        context.set_extra_http_headers({"x-wx-ob-env": "web"})
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
