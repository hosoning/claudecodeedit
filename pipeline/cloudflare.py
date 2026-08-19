"""
用 wrangler CLI 存取 Cloudflare D1 / R2，而不是自己刻 REST API 呼叫。

這樣做的原因：wrangler 的 D1/R2 指令只需要 CLOUDFLARE_API_TOKEN +
CLOUDFLARE_ACCOUNT_ID 這組已經有的 token（deploy.yml 部署 Worker 時就是
用同一組），不需要另外申請 R2 專用的 S3 相容金鑰。

D1 SQL 一律用短字串（key/status/title），不會把完整劇本內容放進 SQL —
完整內容都走 R2，D1 只存 R2 key。所以這裡的 escape 只需處理短欄位。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

D1_DATABASE_NAME = "novel_channel"
R2_BUCKET_NAME = "novel-channel-media"


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def d1_query(sql: str) -> list[dict]:
    """執行一段 SQL，回傳結果 rows（SELECT 用）。呼叫前請自己用
    _sql_escape() 處理好字串值，這裡不做參數綁定。"""
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DATABASE_NAME, "--remote", "--json", "--command", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(result.stdout)
    # wrangler d1 execute --json 回傳格式：[{"results": [...], "success": true, ...}]
    if not parsed:
        return []
    return parsed[0].get("results", [])


def d1_execute(sql: str) -> None:
    """執行一段不需要回傳結果的 SQL（INSERT/UPDATE）。"""
    subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DATABASE_NAME, "--remote", "--command", sql],
        capture_output=True,
        text=True,
        check=True,
    )


def r2_put(key: str, local_path: Path) -> None:
    subprocess.run(
        ["npx", "wrangler", "r2", "object", "put", f"{R2_BUCKET_NAME}/{key}", "--file", str(local_path), "--remote"],
        capture_output=True,
        text=True,
        check=True,
    )


def r2_get(key: str, local_path: Path) -> None:
    subprocess.run(
        ["npx", "wrangler", "r2", "object", "get", f"{R2_BUCKET_NAME}/{key}", "--file", str(local_path), "--remote"],
        capture_output=True,
        text=True,
        check=True,
    )


def get_episode(episode_id: str) -> dict:
    rows = d1_query(f"SELECT * FROM episodes WHERE id = {int(episode_id)}")
    if not rows:
        raise RuntimeError(f"找不到集數 #{episode_id}")
    return rows[0]


def update_episode(episode_id: str, **fields: str) -> None:
    assignments = ", ".join(f"{k} = '{_sql_escape(str(v))}'" for k, v in fields.items())
    sql = f"UPDATE episodes SET {assignments}, updated_at = datetime('now') WHERE id = {int(episode_id)}"
    d1_execute(sql)


def upload_text_to_r2(episode_id: str, filename: str, text: str) -> str:
    """把文字內容存成 R2 物件，回傳 R2 key。"""
    key = f"episodes/{episode_id}/{filename}"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp_path = Path(f.name)
    try:
        r2_put(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return key
