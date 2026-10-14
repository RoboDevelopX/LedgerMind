"""The eval harnesses' own scoring logic (the evals are only as honest as this)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

from categorisation_eval import accuracy  # noqa: E402
from memory_eval import score_case  # noqa: E402


def test_memory_match_needs_type_and_text():
    gold = [{"type": "goal", "match": "japan"}, {"type": "recurring", "match": "rent"}]
    predicted = [{"type": "goal", "content": "Saving for a Japan trip."},
                 {"type": "habit", "content": "Pays rent on the 1st."}]  # right text, wrong type
    matched, fp, notes = score_case(gold, predicted)
    assert matched == [gold[0]] and fp == 1
    assert any(n.startswith("missed recurring") for n in notes)


def test_each_gold_memory_matches_once():
    gold = [{"type": "goal", "match": "japan"}]
    predicted = [{"type": "goal", "content": "Japan trip."}, {"type": "goal", "content": "Japan by March."}]
    matched, fp, _ = score_case(gold, predicted)
    assert len(matched) == 1 and fp == 1


def test_negative_case_with_nothing_extracted_is_clean():
    assert score_case([], []) == ([], 0, [])


def test_accuracy():
    cases = [{"label": "Dining"}, {"label": "Groceries"}]
    assert accuracy(["Dining", "Other"], cases) == 0.5
