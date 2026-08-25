from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _load_mnemonic() -> str:
    path = os.getenv("TON_MNEMONIC_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    value = os.getenv("TON_MNEMONIC")
    if value:
        return value.strip()
    raise RuntimeError("Set TON_MNEMONIC_FILE or TON_MNEMONIC locally before real payout")


def _network_id(name: str):
    from ton_core import NetworkGlobalID
    return NetworkGlobalID.TESTNET if name.casefold() == "testnet" else NetworkGlobalID.MAINNET


async def _send(to: str, amount: float, comment: str) -> str:
    from ton_core import to_nano
    from tonutils.clients import ToncenterClient
    from tonutils.contracts import WalletV4R2

    mnemonic = _load_mnemonic()
    client = ToncenterClient(network=_network_id(os.getenv("TON_NETWORK", "mainnet")))
    await client.connect()
    try:
        wallet, _, _, _ = WalletV4R2.from_mnemonic(client, mnemonic)
        msg = await wallet.transfer(destination=to, amount=to_nano(amount), body=comment or None)
        return str(msg.normalized_hash)
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a real TON transfer with tonutils WalletV4R2")
    parser.add_argument("--to", required=True)
    parser.add_argument("--amount", required=True, type=float)
    parser.add_argument("--comment", default="")
    args = parser.parse_args()
    if args.amount <= 0:
        print("amount must be positive", file=sys.stderr)
        return 2
    try:
        tx_hash = asyncio.run(_send(args.to, args.amount, args.comment))
    except Exception as exc:
        print(f"send failed: {exc}", file=sys.stderr)
        return 1
    print("status=sent")
    print(f"tx_hash={tx_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
