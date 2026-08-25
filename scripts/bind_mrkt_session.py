from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_settings


def _status() -> int:
    settings = load_settings()
    files = sorted(Path(settings.mrkt_session_dir).glob(settings.mrkt_session_name + "*"))
    print(f"session_dir={settings.mrkt_session_dir.resolve()}")
    print(f"session_name={settings.mrkt_session_name}")
    print(f"phone_set={bool(settings.tg_phone_number)}")
    print(f"session_files={len(files)}")
    for file in files:
        print(f"session_file={file.resolve()}")
    return 0


async def _bind() -> int:
    settings = load_settings()
    if not settings.tg_api_id or not settings.tg_api_hash:
        print("Set TG_API_ID and TG_API_HASH in .env first.")
        return 2
    from pyrogram import Client

    settings.mrkt_session_dir.mkdir(parents=True, exist_ok=True)
    client = Client(
        settings.mrkt_session_name,
        api_id=settings.tg_api_id,
        api_hash=settings.tg_api_hash,
        phone_number=settings.tg_phone_number,
        workdir=str(settings.mrkt_session_dir),
    )
    print("Starting MRKT Telegram session binding.")
    print(f"Session dir: {settings.mrkt_session_dir.resolve()}")
    await client.start()
    me = await client.get_me()
    print(f"Session bound: id={me.id} username=@{me.username or '-'} phone=***")
    await client.stop()
    files = sorted(Path(settings.mrkt_session_dir).glob(settings.mrkt_session_name + "*"))
    for file in files:
        print(f"session_file={file.resolve()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind MRKT Telegram session locally")
    parser.add_argument("--status", action="store_true", help="show session status without logging in")
    args = parser.parse_args()
    if args.status:
        return _status()
    return asyncio.run(_bind())


if __name__ == "__main__":
    raise SystemExit(main())
