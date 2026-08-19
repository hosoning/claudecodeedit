"""
單集渲染 pipeline，由 .github/workflows/render.yml 觸發，在 GitHub Actions
runner 上執行（跟開發對話環境不同網路，可以正常呼叫外部 API）。

配音已改成人工（見 README「配音神器自動化調查結果」），流程是：
  generate_script: 讀集數主題 -> 呼叫 Poe API 仿寫劇本 -> 存 R2 -> 通知
                    Telegram，使用者拿劇本去配音神器手動配音、把音檔傳回 bot
  render_video:     使用者傳完語音後觸發，讀語音檔 + 素材庫，跑 Whisper 對齊
                    字幕，ffmpeg 合成，回寫 R2 + D1（素材庫/BGM/字型來源還沒
                    定案，這部分還是 TODO）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import cloudflare
import telegram_notify

TARGET_SCRIPT_CHARS = 9000  # 抓一集大約 25-30 分鐘口播的字數（語速偏快抓 300-350 字/分鐘）
POE_BOT_NAME = os.environ.get("POE_BOT_NAME") or "Claude-3.7-Sonnet"

SCRIPT_SYSTEM_PROMPT = """你是短影音頻道的編劇。任務：參考「網路爆款小說」的敘事套路（強衝突開場、
反轉、爽點密集、懸念收尾），針對使用者給的主題/關鍵字，仿寫一篇全新的原創故事（不要抄襲任何
現有小說的具體情節、角色名、句子，只借用敘事節奏與套路），寫成適合真人念稿的口播文案：

- 語速偏快、口語化，適合配音朗讀
- 開場 10 秒內要有強鉤子
- 全篇一集完結，不要留待續
- 只寫故事正文，不要加標題、不要加「第一章」之類的分段標記、不要加任何 markdown"""


def _poe_generate(prompt: str) -> str:
    import fastapi_poe as fp

    api_key = os.environ["POE_API_KEY"]

    async def _run() -> str:
        message = fp.ProtocolMessage(role="user", content=prompt)
        chunks: list[str] = []
        async for partial in fp.get_bot_response(
            messages=[message], bot_name=POE_BOT_NAME, api_key=api_key
        ):
            chunks.append(partial.text)
        return "".join(chunks)

    return asyncio.run(_run())


def generate_script(episode_id: str) -> None:
    episode = cloudflare.get_episode(episode_id)
    topic = episode.get("title") or "隨機挑一個容易吸引人的都市/懸疑題材"
    chat_id = episode.get("chat_id")

    print(f"[render.py] 開始為 #{episode_id} 生成劇本，主題：{topic}")

    full_prompt = f"{SCRIPT_SYSTEM_PROMPT}\n\n主題/關鍵字：{topic}\n\n請直接開始寫故事正文。"
    script = _poe_generate(full_prompt)

    # 一次呼叫的輸出長度受模型上限限制，通常生不出完整 25-45 分鐘份量，
    # 用「接續」的方式讓它繼續寫，直到達到目標字數或連續兩次沒有新增內容。
    attempts = 0
    while len(script) < TARGET_SCRIPT_CHARS and attempts < 8:
        attempts += 1
        tail = script[-800:]
        continue_prompt = (
            f"{SCRIPT_SYSTEM_PROMPT}\n\n主題/關鍵字：{topic}\n\n"
            f"這是故事目前寫到的結尾片段，請直接從這裡自然接續往下寫（不要重複、不要摘要前文、"
            f"不要加任何說明文字，直接接故事正文）：\n\n{tail}"
        )
        more = _poe_generate(continue_prompt)
        if not more.strip():
            break
        script += "\n\n" + more

    print(f"[render.py] 劇本生成完成，共 {len(script)} 字（呼叫 Poe {attempts + 1} 次）")

    script_key = cloudflare.upload_text_to_r2(episode_id, "script.txt", script)
    cloudflare.update_episode(episode_id, script_r2_key=script_key, status="script_ready")

    if chat_id:
        telegram_notify.send_message(
            int(chat_id),
            f"#{episode_id} 劇本已生成（共 {len(script)} 字）：\n\n{script}\n\n"
            "----\n請拿去配音神器手動配音，完成後把音檔直接傳給我，我會接著剪輯。",
        )
    else:
        print("[render.py] 這個集數沒有 chat_id，略過 Telegram 通知")


def render_video(episode_id: str) -> None:
    # TODO: 素材庫（解壓遊戲畫面）/ 背景音樂 / 抖音美好體字型 來源都還沒定案，
    # 這幾個資產一確定就能把 ffmpeg 合成邏輯接上。目前先只把語音檔抓下來、
    # 跑 Whisper 產字幕，驗證這半段可行。
    episode = cloudflare.get_episode(episode_id)
    voice_key = episode.get("voice_r2_key")
    if not voice_key:
        raise RuntimeError(f"集數 #{episode_id} 沒有 voice_r2_key，還沒收到配音檔")

    print(f"[render.py] #{episode_id} 素材庫/BGM/字型尚未定案，剪輯步驟還沒實作")
    raise NotImplementedError("render_video: 等素材庫/BGM/字型來源確定後補完")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-id", required=True)
    args = parser.parse_args()

    episode = cloudflare.get_episode(args.episode_id)
    status = episode.get("status")
    print(f"[render.py] episode_id={args.episode_id} status={status}")

    if status == "script_pending":
        generate_script(args.episode_id)
    elif status == "voice_ready":
        render_video(args.episode_id)
    else:
        print(f"[render.py] 狀態 {status} 目前沒有對應的處理步驟，跳過")

    return 0


if __name__ == "__main__":
    sys.exit(main())
