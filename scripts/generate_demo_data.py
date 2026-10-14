"""Write the seeded 12-month demo bank statement (fictional person, no real data) as CSV.

    python scripts/generate_demo_data.py                 # INR, ends today -> sample_data/demo_statement_inr.csv
    python scripts/generate_demo_data.py --currency USD --end 2026-10-13 --seed 7
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledgermind.demo import generate, to_csv  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--currency", default="INR", choices=["INR", "USD"])
    ap.add_argument("--end", type=date.fromisoformat, default=None, help="last date (default today)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rows = generate(args.currency, args.end, args.seed)
    out = args.out or Path("sample_data") / f"demo_statement_{args.currency.lower()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_csv(rows, args.currency), encoding="utf-8")
    print(f"Wrote {len(rows)} transactions ({rows[0].date} to {rows[-1].date}) to {out}")


if __name__ == "__main__":
    main()
