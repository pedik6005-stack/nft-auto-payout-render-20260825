from __future__ import annotations

import argparse
import getpass
import os
import stat
from pathlib import Path

TARGET = Path("data/ton_mnemonic.txt")


def _status() -> int:
    print(f"target={TARGET.resolve()}")
    print(f"exists={TARGET.exists()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind payout TON wallet seed locally")
    parser.add_argument("--status", action="store_true", help="show seed-file status without reading input")
    args = parser.parse_args()
    if args.status:
        return _status()
    print("Bind payout TON wallet seed locally.")
    print("Paste the 24 words here in this local console; input is hidden.")
    words = getpass.getpass("TON seed phrase: ").strip()
    parts = words.split()
    if len(parts) not in {12, 18, 24}:
        print(f"Seed has {len(parts)} words; expected 12, 18, or 24.")
        return 2
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(" ".join(parts) + "\n", encoding="utf-8")
    try:
        os.chmod(TARGET, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f"Saved seed to {TARGET.resolve()}")
    print("PAYOUT_MODE=tonutils and TON_MNEMONIC_FILE=data/ton_mnemonic.txt are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
