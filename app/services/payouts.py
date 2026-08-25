from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass

from app.config import Settings

_WALLET_RE = re.compile(r"^(?:EQ|UQ)[A-Za-z0-9_-]{46}$")
_NAME_RE = re.compile(r"^(@?[A-Za-z0-9_\-.]{2,64}|id:[0-9]{1,20})$")


@dataclass(frozen=True)
class PayoutResult:
    status: str
    tx_hash: str | None
    raw_output: str


class PayoutService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def calculate_amount(self, floor_ton: float) -> float:
        amount = float(floor_ton) * (100.0 - float(self.settings.payout_hold_percent)) / 100.0
        return round(max(0.0, amount), 6)

    @staticmethod
    def validate_wallet(wallet: str) -> bool:
        return bool(_WALLET_RE.fullmatch(wallet.strip()))

    @staticmethod
    def validate_name(value: str) -> bool:
        return bool(_NAME_RE.fullmatch(value.strip()))

    async def send(self, to_wallet: str, amount_ton: float, comment: str) -> PayoutResult:
        to_wallet = to_wallet.strip()
        if not self.validate_wallet(to_wallet):
            return PayoutResult("failed", None, "bad destination wallet")
        if amount_ton <= 0:
            return PayoutResult("failed", None, "amount must be positive")
        if self.settings.payout_mode == "dry_run":
            return PayoutResult(
                "dry_run",
                f"DRYRUN-{to_wallet[-6:]}-{amount_ton:.6f}",
                f"dry-run transfer {amount_ton:.6f} TON to {to_wallet}; comment={comment}",
            )
        if self.settings.payout_mode == "tonutils":
            return await asyncio.to_thread(self._run_tonutils_script, to_wallet, amount_ton, comment)
        template = self.settings.ton_transfer_command
        if not template:
            return PayoutResult("failed", None, "TON_TRANSFER_COMMAND is empty")
        command = template.format(to=to_wallet, amount=f"{amount_ton:.6f}", comment=comment.replace('"', "'"))
        return await asyncio.to_thread(self._run_command, command)

    def _run_tonutils_script(self, to_wallet: str, amount_ton: float, comment: str) -> PayoutResult:
        import os
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parents[2] / "scripts" / "send_ton.py"
        env = os.environ.copy()
        if self.settings.ton_mnemonic_file:
            env["TON_MNEMONIC_FILE"] = str(self.settings.ton_mnemonic_file)
        env["TON_NETWORK"] = self.settings.ton_network
        command = [sys.executable, str(script), "--to", to_wallet, "--amount", f"{amount_ton:.6f}", "--comment", comment]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=180, env=env)
        output = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0:
            return PayoutResult("failed", None, output or f"exit {completed.returncode}")
        tx_hash = None
        for line in output.splitlines():
            if line.startswith("tx_hash="):
                tx_hash = line.partition("=")[2].strip()
                break
        return PayoutResult("sent", tx_hash, output)

    @staticmethod
    def _run_command(command: str) -> PayoutResult:
        completed = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=120)
        output = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0:
            return PayoutResult("failed", None, output or f"exit {completed.returncode}")
        tx_hash = None
        for token in output.replace("=", " ").split():
            if len(token) >= 16 and all(ch.isalnum() or ch in "_-:" for ch in token):
                tx_hash = token
                break
        return PayoutResult("sent", tx_hash, output)
