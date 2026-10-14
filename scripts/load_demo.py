"""Load the demo ledger into the local database (used by `make demo`).

    python scripts/load_demo.py --reset
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledgermind.app import build_app_state  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reset", action="store_true", help="wipe the ledger first")
    args = ap.parse_args()
    state = build_app_state()
    summary = state.load_demo(reset=args.reset)
    print(f"Demo ledger ready in {state.settings.db_path}: {summary}")


if __name__ == "__main__":
    main()
