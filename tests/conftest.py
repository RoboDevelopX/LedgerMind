"""Test defaults: offline mock models, a fixed 'today', and no background scheduler.

Live tests (tests/test_live.py) opt back in to real models when LEDGERMIND_LIVE_TESTS=1, so the key is kept then.
"""
import os

os.environ["LEDGERMIND_MOCK"] = "1"
os.environ["LEDGERMIND_TODAY"] = "2026-10-20"
os.environ["LEDGERMIND_PROACTIVE"] = "0"
if os.environ.get("LEDGERMIND_LIVE_TESTS") != "1":
    os.environ.pop("NEBIUS_API_KEY", None)
