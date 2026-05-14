"""Dump FastAPI OpenAPI schema to JSON file.

Usage:
    python backend/scripts/dump_openapi.py [output_path]

Default output: backend/openapi.json (gitignored / build artifact).
前端 `npm run gen:types` 调本脚本，再喂给 openapi-typescript。
"""
import json
import os
import sys
from pathlib import Path


def main() -> None:
    # 确保不真正连接 DB / 启动 scheduler。下面环境变量在 import app.main 前必须就位。
    os.environ.setdefault("USE_MOCK_DATA", "true")
    os.environ.setdefault("API_KEY", "")
    os.environ.setdefault("SENSOR_TOWER_API_KEY", "")
    os.environ.setdefault("ANTHROPIC_API_KEY", "")
    # SQLite in-memory; dump 仅需要 schema，不接触持久化
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    # backend/scripts/ → backend/
    backend_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(backend_root))

    from app.main import app  # noqa: E402

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else backend_root / "openapi.json"
    schema = app.openapi()
    out_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OpenAPI schema written to {out_path} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
