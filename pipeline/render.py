"""
單集渲染 pipeline（骨架，待補完）。

由 .github/workflows/render.yml 觸發，在 GitHub Actions runner 上執行
（跟開發對話環境不同網路，可以正常呼叫外部 API）。

目前各階段都只有函式簽章跟 TODO，尚未接上：
- Cloudflare D1 / R2 的實際讀寫（需要 CLOUDFLARE_API_TOKEN）
- Poe API 劇本生成（需要確認 prompt 策略：仿寫 vs 改寫）
- 配音神器整合（需要確認方案 A: 逆向 API / 方案 B: 帳密登入自動化）
- ffmpeg 剪輯 / Whisper 字幕對齊 / 抖音美好體字型套用
"""

import argparse
import sys


def generate_script(episode_id: str) -> None:
    # TODO: 呼叫 Poe API，依大綱仿寫全新故事文本，寫回 D1 (script_r2_key, status=script_ready)
    raise NotImplementedError


def render_video(episode_id: str) -> None:
    # TODO: 從 R2 拉配音檔 + 素材庫，跑 Whisper 對齊字幕，ffmpeg 合成，回寫 R2 + D1
    raise NotImplementedError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-id", required=True)
    args = parser.parse_args()

    print(f"[render.py] received episode_id={args.episode_id}")
    print("[render.py] pipeline not yet implemented — this is a scaffold run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
