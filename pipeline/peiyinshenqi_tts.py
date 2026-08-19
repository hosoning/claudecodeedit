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

import base64
import json
import os
import time
from pathlib import Path
from typing import Iterable

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

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

# 直接按「合成配音」會出現「请先选择右侧的配音」錯誤 —— 要先在右側配音員列表
# 明確點選一個配音，網站才會認定「已選擇」。這裡先寫死帳號目前顯示的預設配音
# 「晓辰-知性女声」讓 pipeline 先跑通，之後要讓使用者可指定配音時再改成參數。
DEFAULT_VOICE_SELECTOR = "text=晓辰-知性女声"


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


def dump_debug(page: Page, output_dir: Path, label: str, emit_base64: bool = False) -> None:
    """存檔（給人事後下載看）+ 直接印到 stdout（GitHub Actions log 一定看得到，
    不像 artifact 還要另外下載）。emit_base64=True 時會把截圖用 base64 印到 log
    裡（分段印，避免單行過長），這樣即使沒辦法下載 artifact，也能從 log 文字
    重組出實際畫面看。只在關鍵失敗點開，避免每次都印一大包灌爆 log。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / f"debug_{label}.png"
    try:
        page.screenshot(path=str(screenshot_path))  # 只截 viewport，不用 full_page，檔案小很多
    except Exception as e:  # noqa: BLE001
        print(f"截圖失敗（{label}）：{e}")
    try:
        html = page.content()
        (output_dir / f"debug_{label}.html").write_text(html, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"HTML dump 失敗（{label}）：{e}")

    if emit_base64 and screenshot_path.exists():
        try:
            b64 = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
            print(f"----- screenshot base64 [{label}] len={len(b64)} -----")
            for i in range(0, len(b64), 200):
                print(b64[i : i + 200])
            print(f"----- end screenshot base64 [{label}] -----")
        except Exception as e:  # noqa: BLE001
            print(f"screenshot base64 輸出失敗（{label}）：{e}")

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


def goto_with_retry(page: Page, url: str, attempts: int = 3, timeout: int = 30_000) -> None:
    """實測發現 page.goto() 對這個站點時好時壞（同一個 runner，curl 秒連，
    Chromium 卻可能 30 秒 timeout），懷疑是網站對自動化瀏覽器流量的速率限制/
    風控，跟 curl 這種輕量請求的待遇不同。用重試處理這種間歇性失敗。"""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except PlaywrightTimeoutError as e:
            last_err = e
            print(f"goto 第 {i + 1}/{attempts} 次 timeout，重試中...")
    assert last_err is not None
    raise last_err


def install_api_logger(page: Page) -> None:
    """把打去 api3.peiyinshenqi.club 的請求/回應印到 stdout，用來直接確認
    點『开始合成』有沒有真的送出合成請求，而不是只能用畫面上的 toast 文字猜。

    NOTE：第一版在這個 callback 裡呼叫了 response.text()——這是同步 API 的
    經典地雷：在 page.on() 的同步 callback 裡再呼叫別的同步 Playwright API
    會卡住 dispatcher thread，導致 callback 整個不會執行、什麼都印不出來
    （實測完全沒看到任何 [API] 開頭的 log）。改成只印 response 物件已經有的
    屬性（url/status/method），不做任何額外的同步呼叫。"""

    def on_response(response):
        url = response.url
        if "peiyinshenqi" not in url or "/tts/" not in url:
            return
        print(f"[API] {response.request.method} {url} -> {response.status}")

    def on_request(request):
        url = request.url
        if "peiyinshenqi" not in url or "/tts/" not in url:
            return
        print(f"[API-REQ] {request.method} {url}")

    def on_console(msg):
        print(f"[CONSOLE:{msg.type}] {msg.text}")

    def on_pageerror(exc):
        print(f"[PAGEERROR] {exc}")

    def on_dialog(dialog):
        # 新假設：如果「开始合成」跳的是瀏覽器原生 confirm()/alert()，Playwright
        # 預設會自動 dismiss（等同使用者按取消），完全不會留下任何 DOM/網路/
        # console 痕跡——這會剛好解釋我們觀察到的所有現象（點擊成功、面板關掉、
        # 但完全沒有 synthFormat request、也沒有任何錯誤訊息）。這裡明確接受，
        # 並印出內容確認有沒有真的是這個原因。dialog.accept() 是 Playwright
        # 官方文件示範的標準寫法，跟 response.text() 那種同步呼叫死鎖不同。
        print(f"[DIALOG] type={dialog.type} message={dialog.message!r}")
        dialog.accept()

    page.on("response", on_response)
    page.on("request", on_request)
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("dialog", on_dialog)


def synthesize_chunk(page: Page, text: str, download_dir: Path) -> Path:
    install_api_logger(page)

    # 這個 SPA 疑似有持續背景網路活動（心跳/分析類請求），導致
    # wait_until="networkidle" 時好時壞（實測有時 8 秒完成、有時 30 秒直接
    # timeout）。改用 domcontentloaded（只等 DOM 就緒，不等網路安靜）+
    # 固定緩衝時間讓前端 JS 有機會渲染，穩定性好很多。
    goto_with_retry(page, TTS_PAGE_URL)
    page.wait_for_timeout(5_000)
    wait_for_login_check(page)
    dump_debug(page, download_dir, "after_goto")

    try:
        editor = find_editor_locator(page)
        editor.click()
        editor.fill("")
        editor.fill(text)
        dump_debug(page, download_dir, "after_fill")

        # 沒先選配音的話按「合成配音」會被前端擋下（跳「请先选择右侧的配音」
        # 錯誤，不會開確認 dialog），所以要先明確點一次配音卡片。
        page.locator(DEFAULT_VOICE_SELECTOR).first.click()
        dump_debug(page, download_dir, "after_voice_select")

        with page.expect_download(timeout=180_000) as download_info:
            page.locator(SYNTH_BUTTON_SELECTOR).first.click()
            dump_debug(page, download_dir, "after_synth_click")

            # 這個網站是 Element Plus (Vue) 做的：按「合成配音」只會跳出一個
            # 「配音清单」確認面板（列出字數/語速/聲音等），要再按一次
            # 「开始合成」才會真正送出合成請求。
            #
            # 實測發現一個大坑：第一次按「开始合成」時，網站不會真的送出，而是
            # 先彈一個一次性提示（「生成配音前，可以先点击试听哦...」+「我知道了」）
            # 蓋住畫面；關掉提示後「配音清单」面板還在原地、完全沒變化，代表根本
            # 沒送出合成請求。要再按一次「开始合成」才會真的送出。所以這裡改成
            # 反覆「按开始合成 → 順手關掉可能跳出的提示」，直到「配音清单」面板
            # 真的消失（代表已送出）或超過重試次數。
            confirm_btn = page.locator("text=开始合成").first
            confirm_btn.wait_for(state="visible", timeout=10_000)

            # 花了很多輪都測不出「开始合成」為什麼點了卻不會打 synthFormat，
            # 直接把按鈕本身跟往上兩層祖先的真實 HTML 印出來看，比再繼續猜
            # UI 行為有效率。.evaluate() 是主流程呼叫、不是在 event handler
            # 裡呼叫，不會有同步呼叫死鎖的問題。
            try:
                html_dump = confirm_btn.evaluate(
                    "el => { let s = el.outerHTML; let p = el; "
                    "for (let i = 0; i < 2 && p.parentElement; i++) { p = p.parentElement; } "
                    "return JSON.stringify({self: el.outerHTML, ancestor2: p.outerHTML}); }"
                )
                print(f"[HTML] 开始合成 按鈕結構 (前3000字): {html_dump[:3000]}")
            except Exception as e:  # noqa: BLE001
                print(f"[HTML] 讀取按鈕結構失敗：{e}")

            for attempt in range(4):
                try:
                    confirm_btn.click(timeout=5_000)
                except PlaywrightTimeoutError:
                    # 按不到了，很可能是上一輪其實已經送出成功、面板正在關閉
                    # 動畫中或已消失，視為完成而不是錯誤。
                    break
                dump_debug(page, download_dir, f"after_confirm_click_{attempt}")

                dismiss_hint_btn = page.locator("text=我知道了").first
                try:
                    dismiss_hint_btn.wait_for(state="visible", timeout=3_000)
                    dismiss_hint_btn.click()
                    dump_debug(page, download_dir, f"after_dismiss_hint_{attempt}")
                except PlaywrightTimeoutError:
                    pass

                # 給面板關閉動畫一點時間，避免動畫還沒跑完就誤判成「還沒送出」
                # 而多按一次。
                page.wait_for_timeout(2_000)
                if page.locator("text=开始合成").count() == 0:
                    break

            # 實測過再點一次外層「合成配音」——結果只是讓 getSynthList 重打
            # 一次、重新打開同一個面板，不是送出合成，這條路已排除。

            # 送出確認後先被動等一下（不點任何東西），純粹觀察畫面有沒有變化
            # （例如出現進度條/播放器變成可播放），用截圖確認送出後到底有沒有
            # 真的開始跑，而不是一直用點擊+檢查 toast 去猜。
            page.wait_for_timeout(8_000)
            dump_debug(page, download_dir, "after_confirm_submitted")

            # 實測發現：太早點「下载配音」會跳「请先生成配音后再下载」——代表
            # 後端合成其實是非同步的，跟前面的 loading mask 消失沒有直接關係。
            # 改成反覆點擊 + 檢查這個錯誤提示是否還在，直到它消失（代表真的
            # 合成完成）或超過等待上限。
            not_ready_toast = page.locator("text=请先生成配音后再下载")
            max_wait_seconds = 60
            waited = 0.0
            while True:
                page.locator(DOWNLOAD_BUTTON_SELECTOR).first.click()
                page.wait_for_timeout(2_000)
                waited += 2
                if not_ready_toast.count() == 0:
                    break
                print(f"配音尚未合成完成（已等待 {waited:.0f}s），繼續重試下載...")
                if waited >= max_wait_seconds:
                    dump_debug(page, download_dir, "synthesis_not_ready_timeout", emit_base64=True)
                    raise RuntimeError(
                        f"等待配音合成逾時（{max_wait_seconds}s），一直顯示「请先生成配音后再下载」"
                    )
                page.wait_for_timeout(3_000)
                waited += 3
            dump_debug(page, download_dir, "after_download_click")
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
        # 之前猜測這個網站需要 x-wx-ob-env header 才會顯示真正內容（而不是
        # 「請在小程序內開啟」的 fallback），但實測發現頁面本來就正常顯示
        # 完整功能（VIP 狀態、配音清单都對），這個 header 反而讓瀏覽器把它
        # 加到「所有」出站請求上（包含第三方 aegis.qq.com 遙測），導致這些
        # 請求的 CORS preflight 失敗（"Request header field x-wx-ob-env is
        # not allowed by Access-Control-Allow-Headers"）。這個 header 不需要，
        # 拿掉。
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
