# YouTube 小說頻道自動化系統

爆款小說仿寫 → AI 配音（配音神器，**人工**在小程序生成、傳語音檔給 bot）→ 解壓遊戲素材剪輯 →
字幕燒錄 → 驗片 → 上傳 YouTube，用 Telegram bot 串控制與通知，用 Cloudflare Pages 做驗片標註前端。

> 配音這一步原本想全自動（Playwright 操作配音神器網頁版），花了大量時間排查後確認網站對「開始合成」
> 這個會扣付費配額的動作有防自動化偵測（詳見下方「配音神器自動化調查結果」），決定改成人工配音，
> 其餘步驟全自動。

## 架構

```
                    ┌─────────────────┐
   Telegram  ─────► │ Cloudflare Worker │  收指令/配音檔、寫入 D1、觸發 Actions
                    └─────────┬────────┘
                              │ repository_dispatch
                              ▼
                    ┌─────────────────┐
                    │  GitHub Actions   │  跑 Poe API 產劇本 / ffmpeg 剪輯 /
                    │  (ubuntu runner)  │  Whisper 字幕對齊（配音是人工上傳）
                    └─────────┬────────┘
                              │ 上傳成品
                              ▼
                    ┌─────────────────┐
                    │  Cloudflare R2    │  音檔/素材/成片/字幕 儲存
                    └─────────┬────────┘
                              │
                    ┌─────────▼────────┐
                    │ Cloudflare Pages  │  驗片標註前端（讀R2影片，寫回D1）
                    └──────────────────┘
```

**為什麼這樣分工**：Cloudflare Worker 無法執行 ffmpeg／瀏覽器自動化（沒有原生執行環境、CPU時間受限），
所以所有「重運算」與「需要完整網路存取」的工作都放到 GitHub Actions（public repo 免費、無限分鐘數、
完整 Ubuntu 環境）。Worker 只負責輕量的 webhook 收發、狀態記錄、觸發下游 job。

> 目前的 Claude Code 開發環境本身有組織級網路政策，無法直接呼叫 Cloudflare API / Telegram API /
> 配音神器等外部服務，所以所有部署與測試都透過「推 commit → GitHub Actions 執行」完成，而不是
> 在對話環境裡手動操作。

## 資料表（Cloudflare D1，見 migrations/）

- `episodes`：每一集的狀態機（腳本/配音/剪輯/驗片/上傳 各階段狀態與檔案路徑、觸發者 `chat_id`）
- `annotations`：驗片頁提交的標註（時間戳、座標、文字、是否已處理）

## 你需要提供的 GitHub Secrets

到 repo 的 **Settings → Secrets and variables → Actions → New repository secret** 新增：

| Secret 名稱 | 說明 |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard → My Profile → API Tokens → Create Token，權限：Workers Scripts Edit / D1 Edit / Cloudflare Pages Edit / Account Settings Read |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard 首頁右側欄可看到 |
| `TELEGRAM_BOT_TOKEN` | 從 @BotFather 拿到的 token |
| `TELEGRAM_WEBHOOK_SECRET` | 自己隨便打一串英數字（如 `openssl rand -hex 20`），用來驗證 webhook 請求真的來自 Telegram，設定 webhook 時要帶上同一組值 |
| `GH_DISPATCH_TOKEN` | GitHub Personal Access Token（fine-grained，僅限這個 repo，`Contents: read`、`Actions: write` 權限），讓 Worker 能觸發 Actions；注意 GitHub 不允許 secret 名稱以 `GITHUB_` 開頭，所以叫這個名字 |
| `POE_API_KEY` | Poe API key，用於劇本生成 |
| `POE_BOT_NAME`（選填） | 要用哪個 Poe bot 生成劇本，預設 `Claude-3.7-Sonnet`，看你的 Poe 方案有哪些 bot 可用再調整 |

## 配音神器自動化調查結果（已放棄，改人工）

配音神器（peiyinshenqi.com）用掃碼登入，沒有帳密。逆向分析後找到：

- 真正的後端 API 在 `https://api3.peiyinshenqi.club/pc/v220/tts/`（`getVoices`/`selectVoice` 選音色 →
  `getSynthList` 開確認面板 → `synthFormat` 送出文字合成 → `getTtsResult` 輪詢結果 → `preDownload` 準備下載）
- 每個請求都要帶 `X-TOKEN`/`X-ACCOUNT`/`X-SIGN` 等 header，`X-SIGN` 是逐次變動的簽名，回應內容也是加密的
  （`"encryption": true`），沒有原始碼難以逆向簽名演算法與加解密方式，所以改用 Playwright 開真的瀏覽器、
  注入登入態、照 UI 操作，簽名/解密交給網站自己的 JS 處理
- **卡關**：把登入態注入瀏覽器後，選配音、填文字都正常（也會正確打對應的 API），唯獨按下「開始合成」
  （會扣付費配額的動作）在 Playwright 裡完全沒反應——沒有任何錯誤、沒有原生 dialog、也不會打
  `synthFormat`。用使用者自己真實瀏覽器操作、攔截 network 請求比對後確認：一樣的按鈕，真人點擊會正常
  觸發 `synthFormat`，Playwright 點擊（含模擬真實滑鼠移動軌跡、蓋掉 `navigator.webdriver`）完全不會。
  合理推斷網站對這個付費動作做了不只一種自動化偵測，繼續猜測性排查的邊際效益已經很低
- 已排除的假設（照排查順序）：loading mask 誤判、一次性提示視窗擋住點擊、多餘的 `x-wx-ob-env` header
  （這個 header 本身有害，會讓 aegis.qq.com 遙測的 CORS preflight 失敗，已移除）、需要連點兩次確認、
  原生 `confirm()` 對話框被 Playwright 預設自動取消、缺乏真實滑鼠移動軌跡、下拉選單 v-model 未寫入、
  `navigator.webdriver` 偵測
- `pipeline/peiyinshenqi_tts.py` 保留在 repo 裡（連同 `test-tts.yml` 診斷 workflow）供以後有新線索時繼續
  排查，但**目前的 pipeline 不會呼叫它**
- 決定：配音改成人工——使用者在小程序手動生成配音，把音檔傳給 Telegram bot（`src/worker/index.ts`
  的 `handleVoiceUpload` 已經處理這段：綁定到最新 `script_ready` 集數、存到 R2、觸發後續剪輯）

新增完 secrets 後，push 到這個分支就會觸發 `.github/workflows/deploy.yml` 自動部署 Worker + Pages + D1 migration。

## 目前狀態

- [x] repo 骨架、Worker webhook 雛形、D1 schema、部署 workflow
- [x] Telegram bot：`/newscript` 建集數（含 `chat_id`）、`/status` 查狀態、收語音檔綁定集數並觸發渲染
- [x] 配音神器全自動化：調查後確認網站對付費動作有防自動化偵測，改為人工配音（見上方調查結果）
- [x] `pipeline/render.py` 的 `generate_script`：呼叫 Poe API 仿寫劇本（用「接續」的方式湊到目標字數）、
      存 R2、更新 D1、把劇本用 Telegram 傳給使用者去配音 —— 邏輯已寫完，**還沒實跑驗證過**
- [ ] `pipeline/render.py` 的 `render_video`（ffmpeg 剪輯／Whisper 字幕對齊）卡在下面三項還沒定案
- [ ] 版權策略（仿寫 vs 改寫）待定
- [ ] 素材庫（解壓遊戲畫面）來源待定
- [ ] 背景音樂來源待定
- [ ] 字幕字型（抖音美好體）授權/取得方式待定
- [ ] Cloudflare Account ID 待補（其餘 secrets 見上表）
