from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"


class PaperModeE2ETests(unittest.TestCase):
    def test_cli_check_uses_isolated_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = self._write_env(directory, dry_run=True)

            result = self._run_bot("--check", "--env-file", str(env_file))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Configuration looks usable for paper mode", result.stderr)

    def test_dry_run_once_does_not_record_trade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            env_file = self._write_env(directory, dry_run=True, state_path=state_path)

            result = self._run_bot("--once", "--env-file", str(env_file))

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY_RUN would place", result.stderr)
        self.assertEqual(state["trades_count"], 0)

    def test_paper_order_flow_records_trade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            env_file = self._write_env(directory, dry_run=False, state_path=state_path)

            result = self._run_bot("--once", "--env-file", str(env_file))

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Order sent: Paper", result.stderr)
        self.assertEqual(state["trades_count"], 1)

    def test_csv_uptrend_places_buy_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "uptrend.csv"
            state_path = Path(directory) / "state.json"
            self._write_uptrend_csv(data_path)
            env_file = self._write_env(
                directory,
                dry_run=False,
                state_path=state_path,
                data_csv_path=data_path,
            )

            result = self._run_bot("--once", "--env-file", str(env_file))

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Order sent: Paper BUY", result.stderr)
        self.assertEqual(state["trades_count"], 1)

    def _write_env(
        self,
        directory: str,
        *,
        dry_run: bool,
        state_path: Path | None = None,
        data_csv_path: Path | None = None,
    ) -> Path:
        state_path = state_path or (Path(directory) / "state.json")
        env_file = Path(directory) / ".env.e2e"
        env_file.write_text(
            "\n".join(
                [
                    "TRADING_MODE=paper",
                    f"DRY_RUN={'true' if dry_run else 'false'}",
                    "USE_LLM=false",
                    "SYMBOL=XAUUSD",
                    "TIMEFRAME=M1",
                    "BARS=300",
                    f"DATA_CSV_PATH={data_csv_path or ''}",
                    "FIXED_LOT=0.01",
                    "MAX_POSITIONS=1",
                    "MAX_TRADES_PER_DAY=3",
                    "COOLDOWN_SECONDS=0",
                    f"STATE_PATH={state_path}",
                    "LOG_LEVEL=INFO",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return env_file

    def _write_uptrend_csv(self, path: Path) -> None:
        lines = ["time,open,high,low,close,tick_volume"]
        price = 2300.0
        for index in range(300):
            open_value = price
            close = open_value + 0.25
            high = close + 0.08
            low = open_value - 0.08
            lines.append(
                f"2026-01-01T00:{index % 60:02d}:00Z,"
                f"{open_value:.2f},{high:.2f},{low:.2f},{close:.2f},250"
            )
            price = close
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _run_bot(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            "PYTHONPATH": str(SRC_PATH),
            "PYTHONIOENCODING": "utf-8",
            "PATH": os.environ.get("PATH", ""),
        }
        for key in ("SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
            if key in os.environ:
                env[key] = os.environ[key]

        return subprocess.run(
            [sys.executable, "-m", "trend_scalper", *args],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
