#!/usr/bin/env python3
"""执行 sql/schema.sql，用环境变量连接 MySQL（不含库名时先建库）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql" / "schema.sql"


def main() -> int:
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    sql = SCHEMA.read_text(encoding="utf-8")

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            for stmt in _split_statements(sql):
                cur.execute(stmt)
        print(f"已应用 schema: {SCHEMA}")
        return 0
    finally:
        conn.close()


def _split_statements(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        if stripped.endswith(";"):
            parts.append("\n".join(buf).rstrip(";").strip())
            buf = []
    if buf:
        parts.append("\n".join(buf).strip())
    return [p for p in parts if p]


if __name__ == "__main__":
    sys.exit(main())
