"""CSV import, demo data, categorisation, summary, subscriptions and anomalies (models mocked)."""
from datetime import date

import pandas as pd
import pytest

from ledgermind.app import AppState
from ledgermind.config import load_settings
from ledgermind.demo import generate, to_csv
from ledgermind.llm import LLMError, MockClient, ModelRouter
from ledgermind.llm.mock import mock_brain
from ledgermind.memory.store import MemoryStore
from ledgermind.money import Money, fmt, money, render_for_model
from ledgermind.periods import resolve_period
from ledgermind.skills import SkillContext, run_skill
from ledgermind.skills.importer import CSVImportError, import_text, parse_amount, parse_csv


@pytest.fixture
def state(tmp_path):
    s = AppState(settings=load_settings(env={"LEDGERMIND_MOCK": "1"}), db_path=str(tmp_path / "t.db"))
    s.load_demo()
    return s


# ---------------------------------------------------------------- CSV import

def test_header_autodetect_with_preamble_and_debit_credit():
    text = ("My Bank Statement\nAccount: XXXX1234\n\nTxn Date;Narration;Withdrawal Amt;Deposit Amt;Balance\n"
            "05/10/2026;SWIGGY ORDER;450.00;;1000\n06/10/2026;SALARY;;50,000.00;51000\n")
    parsed = parse_csv(text)
    assert parsed.header_line == 3 and parsed.date_format == "%d/%m/%Y"
    assert [(r["date"], r["amount"]) for r in parsed.rows] == [("2026-10-05", -450.0), ("2026-10-06", 50000.0)]


def test_amount_formats_and_type_column():
    assert parse_amount("₹1,234.50") == 1234.5
    assert parse_amount("1,200 Dr") == -1200 and parse_amount("(45.00)") == -45 and parse_amount("") is None
    rows = parse_csv("Date,Details,Amount,Type\n05-Oct-2026,Coffee,120,DR\n06-Oct-2026,Refund,50,CR\n").rows
    assert [r["amount"] for r in rows] == [-120, 50]


def test_us_dates_and_explicit_mapping():
    rows = parse_csv("When,What,How much\n10/13/2026,Coffee,-4.5\n",
                     mapping={"date": "When", "description": "What", "amount": "How much"}, dayfirst=False).rows
    assert rows[0]["date"] == "2026-10-13"


@pytest.mark.parametrize("text, message", [
    ("", "empty"),
    ("a,b,c\n1,2,3\n", "header row"),
    ("Date,Description,Amount\nnot-a-date,Coffee,-4\n", "dates"),
])
def test_bad_csvs_give_friendly_errors(text, message):
    with pytest.raises(CSVImportError, match=message):
        parse_csv(text)


def test_reimport_is_noop_but_genuine_duplicates_are_kept(state):
    again = import_text(state.ctx, to_csv(generate("INR")), account="Demo savings account")
    assert again["added"] == 0 and again["already_imported"] == again["rows_read"]
    dupes = state.db.all("SELECT * FROM transactions WHERE description LIKE '%402-7731%'")
    assert len(dupes) == 2


# ---------------------------------------------------------------- demo + categorisation

def test_demo_is_seeded_and_fictional():
    a, b = generate("INR", date(2026, 10, 20)), generate("INR", date(2026, 10, 20))
    assert [(t.date, t.description, t.amount) for t in a] == [(t.date, t.description, t.amount) for t in b]
    assert a[0].date >= date(2025, 11, 1) and a[-1].date <= date(2026, 10, 20)
    assert generate("USD", date(2026, 10, 20))[0].amount != a[0].amount


def test_everything_categorized_with_model_for_unknowns(state):
    assert state.db.scalar("SELECT COUNT(*) FROM transactions WHERE category='Uncategorized'") == 0
    toit = state.db.one("SELECT category, category_source FROM transactions WHERE merchant LIKE 'Toit%'")
    assert toit == {"category": "Dining", "category_source": "model"}
    p2p = state.db.one("SELECT merchant, category FROM transactions WHERE description LIKE '%RAHUL VERMA%'")
    assert p2p == {"merchant": "Person-to-person transfer", "category": "Transfers"}


def test_categorizer_falls_back_when_model_is_down(tmp_path):
    settings = load_settings(env={"LEDGERMIND_MOCK": "1"})
    down = {m.id: [LLMError("down", retryable=False)] * 5 for m in settings.models.values()}
    state = AppState(settings=settings, db_path=str(tmp_path / "d.db"))
    state.router = ModelRouter(settings, MockClient(mock_brain, failures=down), sleep=lambda s: None)
    state.load_demo()
    toit = state.db.one("SELECT category, category_source FROM transactions WHERE merchant LIKE 'Toit%'")
    assert toit == {"category": "Other", "category_source": "fallback"}
    assert state.db.scalar("SELECT COUNT(*) FROM transactions WHERE category='Uncategorized'") == 0


def test_correction_is_remembered_and_applied_first(state):
    r = run_skill(state.ctx, "correct_category", {"merchant": "Practo", "category": "Health"})
    assert r["transactions_updated"] >= 1
    r = run_skill(state.ctx, "correct_category", {"merchant": "Swiggy", "category": "Groceries"})
    assert r["previous_categories"] == {"Dining": r["transactions_updated"]}
    mem = MemoryStore(state.db).list("correction")
    assert any(m["data"]["merchant"] == "Swiggy" and m["data"]["category"] == "Groceries" for m in mem)
    import_text(state.ctx, "Date,Narration,Amount\n19/10/2026,UPI/P2M/999999999/SWIGGY/swiggy@ybl/Order,-300\n")
    new = state.db.one("SELECT category, category_source FROM transactions WHERE description LIKE '%999999999%'")
    assert new == {"category": "Groceries", "category_source": "correction"}


# ---------------------------------------------------------------- summary

def test_summary_matches_pandas(state):
    r = run_skill(state.ctx, "spending_summary", {"period": "last_month"})
    df = pd.DataFrame(state.db.all("SELECT * FROM transactions WHERE date BETWEEN '2026-09-01' AND '2026-09-30'"))
    spend = df[~df.category.isin(["Income", "Savings", "Transfers"])]
    assert r["spent"] == pytest.approx(-spend.amount.sum())
    assert r["income"] == pytest.approx(df[df.category == "Income"].amount.sum())
    dining = next(c for c in r["by_category"] if c["category"] == "Dining")
    assert dining["spent"] == pytest.approx(-df[df.category == "Dining"].amount.sum())
    assert r["compared_with"]["period"]["label"] == "August 2026"
    assert isinstance(r["spent"], Money)


def test_summary_filters_and_empty_periods(state):
    r = run_skill(state.ctx, "spending_summary", {"period": "last_6_months", "category": "eating out"})
    assert r["filter"]["category"] == "Dining" and len(r["monthly"]) == 6
    assert run_skill(state.ctx, "spending_summary", {"period": "2020-01"})["empty"] is True
    assert "error" in run_skill(state.ctx, "spending_summary", {"category": "spaceships"})


# ---------------------------------------------------------------- subscriptions

def test_subscriptions_price_hike_and_forgotten(state):
    r = run_skill(state.ctx, "detect_subscriptions")
    subs = {s["merchant"]: s for s in r["subscriptions"]}
    assert {"Netflix", "Spotify", "Apple Music", "iCloud+", "Cult.fit", "YouTube Premium", "JioHotstar"} <= set(subs)
    assert subs["Netflix"]["price_change"]["from"] == 649 and subs["Netflix"]["price_change"]["to"] == 799
    assert r["price_hikes"] == ["Netflix"]
    assert r["possibly_forgotten"] == ["Apple Music"]
    assert subs["Apple Music"]["overlaps_with"]["merchant"] == "Spotify"
    assert subs["JioHotstar"]["cadence"] == "quarterly"
    assert "Rent" in {b["merchant"] for b in r["bills"]}
    assert r["subscriptions_monthly_total"] == pytest.approx(sum(s["monthly_equivalent"] for s in r["subscriptions"]))


# ---------------------------------------------------------------- anomalies

def test_anomalies_find_the_planted_ones(state):
    r = run_skill(state.ctx, "detect_anomalies", {"period": "last_90_days"})
    kinds = {(a["kind"], a.get("merchant") or a.get("category")) for a in r["anomalies"]}
    assert ("duplicate", "Amazon") in kinds
    assert ("large_one_off", "Croma") in kinds
    assert ("unusual_amount", "Swiggy") in kinds
    assert ("category_spike", "Dining") in kinds
    assert ("category_spike", "Shopping") not in kinds  # already explained by the Croma purchase


# ---------------------------------------------------------------- helpers

def test_money_formatting():
    assert fmt(120000) == "₹1,20,000" and fmt(12345678.9) == "₹1,23,45,679" and fmt(99.5) == "₹99.50"
    assert fmt(1200.5, "USD") == "$1,200" and fmt(-45.25, "USD") == "-$45.25"
    assert render_for_model({"a": [money(1500)], "n": 3}, "INR") == {"a": ["₹1,500"], "n": 3}


def test_periods():
    p = resolve_period("last_month")
    assert (p.start, p.end, p.previous().start) == (date(2026, 9, 1), date(2026, 9, 30), date(2026, 8, 1))
    mtd = resolve_period("this_month")
    assert mtd.previous().end == date(2026, 9, 20)
    assert resolve_period("last_3_months").start == date(2026, 7, 1)


@pytest.mark.parametrize("text,start", [
    ("september", date(2026, 9, 1)),   # bare month = its latest occurrence, not a year guessed by the model
    ("Sept", date(2026, 9, 1)),
    ("december", date(2025, 12, 1)),
    ("october", date(2026, 10, 1)),
    ("march 2025", date(2025, 3, 1)),
    ("2024-09", date(2024, 9, 1)),     # an explicit year is still honoured
])
def test_month_names(text, start):
    p = resolve_period(text)
    assert p.start == start and p.whole_months


def test_empty_period_tells_the_agent_what_the_ledger_covers(state):
    out = run_skill(state.ctx, "spending_summary", {"period": "2024-09"})
    assert out["empty"] and "The ledger covers" in out["message"] and "september" in out["message"]


def test_unknown_tool_and_bad_args_are_errors_not_exceptions(state):
    assert "unknown tool" in run_skill(state.ctx, "launch_rocket")["error"]
    assert "invalid arguments" in run_skill(SkillContext(state.db, state.settings), "correct_category",
                                            {"merchant": "x"})["error"]
