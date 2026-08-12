# YouTube 小說頻道自動化系統

爆款小說仿寫 → AI 配音（配音神器，人工觸發）→ 解壓遊戲素材剪輯 → 字幕燒錄 → 驗片 → 上傳 YouTube，
用 Telegram bot 串控制與通知，用 Cloudflare Pages 做驗片標註前端。

## 架構

```
                    ┌─────────────────┐
   Telegram  ─────► │ Cloudflare Worker │  收指令/配音檔、寫入 D1、觸發 Actions
                    └─────────┬────────┘
                              │ repository_dispatch
                              ▼
                    ┌─────────────────┐
                    │  GitHub Actions   │  跑 Poe API 產劇本 / ffmpeg 剪輯 /
                    │  (ubuntu runner)  │  Whisper 字幕對齊 / 配音神器自動化
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

## 資料表（Cloudflare D1，見 migrations/0001_init.sql）

- `episodes`：每一集的狀態機（腳本/配音/剪輯/驗片/上傳 各階段狀態與檔案路徑）
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
| `PEIYINSHENQI_SESSION` | 配音神器登入態，JSON字串，內容是瀏覽器 localStorage 的 `tts:user`／`tts:uservip`／`WXOBS_USER_IDENTIFIER_KEY` 等 key/value（見下方「配音神器整合」段落）。這組資料可能會過期，過期時需要重新掃碼登入、重新匯出、更新這個 secret |

## 配音神器整合

配音神器（peiyinshenqi.com）用掃碼登入，沒有帳密。逆向分析後找到：

- 真正的後端 API 在 `https://api3.peiyinshenqi.club/pc/v220/tts/`（`getSynthList` 查詢音色 → `synthFormat` 送出文字合成 → `getTtsResult` 輪詢結果）
- 每個請求都要帶 `X-TOKEN`/`X-ACCOUNT`/`X-SIGN` 等 header，`X-SIGN` 是逐次變動的簽名，回應內容也是加密的（`"encryption": true`），沒有原始碼難以逆向簽名演算法與加解密方式
- 因此**不走直接呼叫API的路**，改用 `pipeline/peiyinshenqi_tts.py`：Playwright 開真的瀏覽器，把使用者登入後的 localStorage 原封不動注入進去（跳過掃碼），再照著真實 UI 操作（貼文字→按合成→抓下載的音檔）。簽名/解密都交給網站自己的 JS 處理
- 單次合成上限 **8000 字**，超過的稿子會自動切段分次合成，之後再用 ffmpeg 接起來（接音檔的邏輯還沒寫，是下一步）
- `pipeline/peiyinshenqi_tts.py` 裡的 DOM selector 是根據截圖做的最佳猜測，還沒有實跑驗證過，第一次在 Actions 跑大概率要依實際錯誤調整

新增完 secrets 後，push 到這個分支就會觸發 `.github/workflows/deploy.yml` 自動部署 Worker + Pages + D1 migration。

## 目前狀態

- [x] repo 骨架、Worker webhook 雛形、D1 schema、部署 workflow
- [x] Cloudflare API Token 已取得，Account ID 待補
- [x] 配音神器 API 逆向分析完成，`peiyinshenqi_tts.py` 骨架已寫（用 Playwright，selector 待實跑驗證）
- [x] `PEIYINSHENQI_SESSION` secret 已存入 GitHub，新增獨立的 `test-tts.yml` workflow 可以手動跑單一功能測試
- [ ] 音檔分段合成後的接軌（ffmpeg 拼接）邏輯待寫
- [ ] 版權策略（仿寫 vs 改寫）待定
- [ ] 素材庫來源待定
