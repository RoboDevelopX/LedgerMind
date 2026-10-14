"""Offline stand-in for Nemotron. It honours each task's JSON contract with simple rules so the whole app,
the eval and the tests run without network. Answers are templated from tool results, so they are plain but
always numerically correct."""
from __future__ import annotations

import json
import re
from typing import Any

from .client import ChatRequest, ChatResult, ToolCall

# Words a model would associate with a category, for merchants the local rules don't know.
_HINTS = {"brewpub": "Dining", "restaurant": "Dining", "1924": "Dining", "diagnostic": "Health",
          "practo": "Health", "infiniti": "Shopping", "electronics": "Shopping", "market": "Groceries"}


def _last_user(req: ChatRequest) -> str:
    return next((m.get("content") or "" for m in reversed(req.messages) if m["role"] == "user"), "")


def _categorize(req: ChatRequest) -> dict[str, Any]:
    out = {}
    for line in _last_user(req).splitlines():
        key, merchant, _ = (line.split("|") + ["", ""])[:3]
        low = merchant.lower()
        category = next((c for hint, c in _HINTS.items() if hint in low), "Other")
        out[key] = {"category": category, "merchant": merchant.title()}
    return out


def _third_person(clause: str) -> str:
    s = re.sub(r"\b(I am|I'm)\b", "is", clause, flags=re.I)
    s = re.sub(r"\bI\b", "", s)
    s = re.sub(r"\bmy\b", "their", s, flags=re.I)
    s = re.sub(r"\bme\b", "them", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.")
    first, _, rest = s.partition(" ")
    verbs = {"earn": "earns", "make": "makes", "get": "gets", "live": "lives", "have": "has", "prefer": "prefers",
             "like": "likes", "hate": "hates", "take": "takes", "want": "wants", "usually": "usually",
             "don't": "doesn't", "do": "does"}
    s = f"{verbs.get(first.lower(), first)} {rest}".strip()
    return s[:1].upper() + s[1:] + "."


# Rest of a sentence (a "." followed by a digit is a decimal point, not the end).
_CLAUSE = r"(?:[^.?!]|\.(?=\d))*"

# (type, key or None, pattern on the user's message)
_MEMORY_RULES = [
    ("profile", "income", r"\bi (?:earn|make|take home|get paid) (?:[^.?!]|\.(?=\d))*?\d" + _CLAUSE),
    ("profile", "income", r"\bmy (?:salary|income) is " + _CLAUSE),
    ("profile", "payday", r"\b(?:i get|i'm|i am) paid on " + _CLAUSE),
    ("profile", "household", r"\bi (?:live with|have) (?:a |an |my |two |three )?(?:wife|husband|partner|kids?|"
                             r"children|son|daughter|roommates?|parents|dog|cat)" + _CLAUSE),
    ("goal", None, r"\bi(?:'m| am) saving (?:up )?(?:for|towards) " + _CLAUSE),
    ("preference", None, r"\bi (?:prefer|like|don't like|do not like|hate|want to avoid|never)" + _CLAUSE),
    ("preference", None, r"\bi(?:'m| am) (?:vegetarian|vegan|frugal|trying to)" + _CLAUSE),
    ("habit", None, r"\bi (?:usually|always|often|tend to)" + _CLAUSE),
    ("decision", None, r"\bi (?:cancelled|canceled|decided to|will stop|stopped|switched to|am going to)" + _CLAUSE),
]


def _extract(req: ChatRequest) -> dict[str, Any]:
    text = _last_user(req).split("USER:", 1)[-1].split("ASSISTANT (context):", 1)[0]
    out = []
    for kind, key, pattern in _MEMORY_RULES:
        for m in re.finditer(pattern, text, re.I):
            clause = re.split(r"\s+and\s+(?:i|my)\s+", m.group(0), flags=re.I)[0]
            out.append({"type": kind, "key": key, "content": _third_person(clause),
                        "confidence": 0.85, "importance": 0.8 if kind in ("profile", "goal") else 0.6})
    return {"memories": out}


def mock_brain(req: ChatRequest) -> ChatResult:
    """Dispatch on the request's task name."""
    if req.task == "categorize":
        return ChatResult(content=json.dumps(_categorize(req)))
    if req.task == "extract_memory":
        return ChatResult(content=json.dumps(_extract(req)))
    if req.task in ("agent", "deep_answer"):
        return _agent(req)
    if req.task == "guard_retry":
        return ChatResult(content=next(m["content"] for m in reversed(req.messages) if m["role"] == "assistant"))
    if req.task == "monthly_review":
        return ChatResult(content="not json")  # exercises the deterministic fallback offline
    if req.task == "consolidate_memory":
        lines = [ln[2:] for ln in _last_user(req).splitlines() if ln.startswith("- ")]
        return ChatResult(content=json.dumps({"memories": lines[:4]}))
    return ChatResult(content="(offline mode) I can't write a free-form answer without a model.")


# --------------------------------------------------------------------------- agent

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_AMOUNT = re.compile(r"(?:₹|\$|rs\.?\s?|inr\s?)?(\d[\d,]*(?:\.\d+)?)\s?(lakh|lakhs|l\b|k\b|crore|cr\b)?", re.I)


def _amount(text: str) -> float | None:
    for m in _AMOUNT.finditer(text):
        raw = m.group(1).replace(",", "")
        if re.fullmatch(r"20\d\d", raw) and not m.group(2):
            continue  # a year, not an amount
        value = float(raw)
        unit = (m.group(2) or "").lower()
        value *= {"lakh": 1e5, "lakhs": 1e5, "l": 1e5, "k": 1e3, "crore": 1e7, "cr": 1e7}.get(unit, 1)
        if value >= 50 or unit:
            return value
    return None


def _deadline(text: str) -> str | None:
    from ..periods import month_end, today

    m = re.search(r"by (?:the end of )?(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?", text.lower())
    if not m:
        return None
    month = _MONTHS.index(m.group(1)) + 1
    t = today()
    year = int(m.group(2)) if m.group(2) else (t.year if month > t.month else t.year + 1)
    from datetime import date
    return month_end(date(year, month, 1)).isoformat()


def _period(text: str) -> str:
    from ..periods import today

    t = text.lower()
    m = re.search(r"last (\d+) months", t)
    if m:
        return f"last_{m.group(1)}_months"
    for words, period in (("last month", "last_month"), ("this month", "this_month"), ("this year", "this_year"),
                          ("last 90 days", "last_90_days"), ("last 30 days", "last_30_days"),
                          ("last quarter", "last_3_months"), ("past year", "last_12_months"),
                          ("last year", "last_12_months")):
        if words in t:
            return period
    for i, name in enumerate(_MONTHS, 1):
        if re.search(rf"\b(in|for|during) {name}\b", t):
            year = today().year if i < today().month else today().year - 1
            return f"{year}-{i:02d}"
    return "this_month"


def _category(text: str) -> str | None:
    from ..merchants import closest_category

    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) > 3 and w not in ("much", "spend", "spent", "spending", "money", "this", "last", "month", "what",
                                    "budget", "monthly", "limit", "with", "from", "that", "have", "about"):
            cat = closest_category(w)
            if cat and cat not in ("Other",):
                return cat
    return None


def _call(tool: str, **args: Any) -> dict[str, Any]:
    return {"name": tool, "args": {k: v for k, v in args.items() if v is not None}}


def _route(text: str) -> list[dict[str, Any]]:
    """Which tools a model would call for this message."""
    t = text.lower().strip()
    if re.match(r"(please )?remember\b", t):
        content = re.sub(r"^(please )?remember( that)?\s*", "", text, flags=re.I)
        return [_call("remember", content=_third_person(content), type="preference")]
    if re.match(r"(please )?forget\b", t):
        return [_call("forget", about=re.sub(r"^(please )?forget( about)?\s*", "", text, flags=re.I).strip(" ?."))]
    if re.search(r"what do you (know|remember)|what have you learned", t):
        return [_call("recall_memories")]
    m = re.search(r"^(.+?) (?:is|are|should be) (?:actually |really )?(?:a |an )?([a-z ]+?),? not ([a-z ]+)", t)
    if m:
        from ..merchants import closest_category
        cat = closest_category(m.group(2))
        if cat:
            return [_call("correct_category", merchant=m.group(1).strip().title(), category=cat)]
    if re.search(r"what if|what happens if|cut .* by|reduce .* by|spend .* less", t):
        changes = []
        for cm in re.finditer(r"([a-z]+(?: [a-z]+)?) by (\d+)\s?%", t):
            from ..merchants import closest_category
            words = cm.group(1).split()
            cat = closest_category(words[-1]) or closest_category(cm.group(1))
            if cat:
                sign = 1 if re.search(r"increase|raise|more", t[: cm.start()]) else -1
                changes.append({"category": cat, "change_pct": sign * float(cm.group(2))})
        inc = re.search(r"(?:income|salary|raise) (?:goes up |increases |rises )?by (\d+)\s?%", t)
        return [_call("what_if_simulator", changes=changes or None,
                      income_change_pct=float(inc.group(1)) if inc else None)]
    if re.search(r"monthly review|review (?:of )?my|month in review|how did i do", t):
        p = _period(t)
        return [_call("monthly_review", month=p if re.fullmatch(r"\d{4}-\d{2}", p) else None)]
    if "budget" in t:
        amount = _amount(t)
        if amount and re.search(r"\bset|limit|cap|make|keep|create", t):
            return [_call("set_budget", category=_category(t) or "Other", monthly_limit=amount)]
        return [_call("budget_check", category=_category(t))]
    m = re.search(r"saving (?:up )?(?:(?:₹|\$|rs\.? ?)?[\d,.]+ ?(?:lakhs?|k|cr|crore)? )?(?:for|towards) "
                  r"(?:a |an |my )?([a-z ]+?)(?:,| -| by| of| which|\.|$)", t)
    if m and _amount(t):
        saved = re.search(r"(?:have|saved|already) (?:saved )?(?:₹|\$)?\s?([\d,]+(?:\.\d+)?\s?(?:lakh|k)?)", t)
        return [_call("set_goal", name=m.group(1).strip().title(), target_amount=_amount(t[m.end():]) or _amount(t),
                      deadline=_deadline(t), saved_so_far=_amount(saved.group(0)) if saved else None)]
    if re.search(r"goal|on track|afford", t):
        return [_call("goal_progress")]
    if re.search(r"subscription|recurring|bills|forgotten", t):
        return [_call("detect_subscriptions")]
    if re.search(r"unusual|anomal|suspicious|duplicate|weird|strange|odd", t):
        return [_call("detect_anomalies", period="last_90_days")]
    if re.search(r"categori[sz]e", t):
        return [_call("categorize_transactions")]
    if re.search(r"\b(income|earn|earned|salary)\b", t):
        return [_call("spending_summary", period=_period(t))]
    m = re.search(r"(?:on|at) ([a-z][a-z.' ]+?)(?:\?|$| this| last| in| during| for)", t)
    if re.search(r"spend|spent|spending|where .*money|summary|overview|how much|breakdown", t):
        cat = _category(t)
        merchant = None
        if m and not cat:
            merchant = m.group(1).strip().title()
        return [_call("spending_summary", period=_period(t), category=cat, merchant=merchant)]
    return []


def _fmt_list(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items)


def _answer_for(name: str, v: dict[str, Any]) -> str:
    """Template an answer from one tool's (already formatted) result."""
    if "error" in v:
        return f"I couldn't do that: {v['error']}"
    if name == "spending_summary":
        if v.get("empty"):
            return v["message"] + " Import a bank CSV or load the demo ledger to get started."
        cmp = v["compared_with"]
        if v.get("filter"):
            what = v["filter"]["merchant"] or v["filter"]["category"]
            return (f"You spent {v['spent']} on {what} in {v['period']['label']} across {v['transactions']} "
                    f"transactions, compared with {cmp['spent']} in {cmp['period']['label']}.")
        head = (f"For {v['period']['label']} you spent {v['spent']} against income of {v['income']}, and "
                f"{v['saved_or_invested']} went to savings and investments.")
        if cmp["spent_change_pct"] is not None:
            head += f" Change versus {cmp['period']['label']} ({cmp['spent']}): {cmp['spent_change_pct']}%."
        cats = [f"{c['category']}: {c['spent']} ({c['share_pct']}% of spending)" for c in v["by_category"][:4]]
        return head + "\n\nBiggest categories:\n" + _fmt_list(cats)
    if name == "detect_subscriptions":
        subs = [f"{s['merchant']}: {s['amount']} {s['cadence']}" for s in v["subscriptions"]]
        out = (f"You have {len(v['subscriptions'])} active subscriptions costing about "
               f"{v['subscriptions_monthly_total']} a month ({v['subscriptions_yearly_total']} a year).\n\n"
               + _fmt_list(subs))
        for s in v["subscriptions"]:
            if s.get("price_change"):
                pc = s["price_change"]
                out += f"\n\n{s['merchant']} went up from {pc['from']} to {pc['to']} ({pc['change_pct']}%)."
            if "possibly_forgotten" in s["flags"]:
                ov = s["overlaps_with"]
                out += (f"\n\n{s['merchant']} looks forgotten: you also pay for {ov['merchant']} "
                        f"({ov['group']}) since {ov['since']}.")
        return out
    if name == "detect_anomalies":
        if not v["anomalies"]:
            return f"Nothing unusual in {v['period']['label']}."
        if isinstance(v["anomalies"], dict):
            return (f"I found {v['count']} unusual items in {v['period']['label']}. Strict privacy mode kept the "
                    "details on your device; open the tool step below to see them.")
        items =[f"{a.get('merchant') or a.get('category')}: {a['amount']}. {a['reason']}" for a in v["anomalies"]]
        return f"I found {v['count']} things worth a look in {v['period']['label']}:\n\n" + _fmt_list(items)
    if name == "budget_check":
        if not v["budgets"]:
            sugg = [f"{s['category']}: you usually spend {s['recent_monthly_average']}"
                    for s in v.get("suggestions", [])]
            return v.get("message", "No budgets yet.") + (" Some candidates:\n\n" + _fmt_list(sugg) if sugg else "")
        rows = [f"{b['category']}: {b['spent']} of {b['monthly_limit']} ({b['pct_used']}%), "
                f"heading for {b['projected_month_end']} ({b['status'].replace('_', ' ')})" for b in v["budgets"]]
        return f"{v['month']}, day {v['day_of_month']} of {v['days_in_month']}:\n\n" + _fmt_list(rows)
    if name == "set_budget":
        return (f"Done: your {v['category']} budget is {v['monthly_limit']} a month. Recently you've averaged "
                f"{v['recent_monthly_average']}. I'll warn you as you approach it.")
    if name in ("set_goal", "goal_progress"):
        if not v.get("goals"):
            return v.get("message", "No goals yet.") + " Tell me what you're saving for, how much, and by when."
        rows = []
        for g in v["goals"]:
            line = f"{g['name']}: {g['saved']} of {g['target']} ({g['pct_done']}%)"
            if g["needed_per_month"]:
                line += f", needs {g['needed_per_month']} a month until {g['deadline']}"
            rows.append(line)
        fit = ("which fits within" if v["fits_in_surplus"] else
               "so on current habits you'd need to save more or move the deadline, since that's more than")
        return (("Saved. " if name == "set_goal" else "") + "Your goals:\n\n" + _fmt_list(rows)
                + f"\n\nTogether they need {v['total_needed_per_month']} a month, {fit} your average monthly "
                  f"surplus of {v['avg_monthly_surplus']}.")
    if name == "what_if_simulator":
        rows = [f"{c['category']}: {c['current_monthly']} to {c['new_monthly']} a month" for c in v["changes"]]
        out = (f"That would free up {v['monthly_saving_change']} a month, {v['total_change_over_horizon']} over "
               f"{v['horizon_months']} months.\n\n" + _fmt_list(rows))
        for g in v["goals"]:
            if g["months_sooner"]:
                unit = "month" if g["months_sooner"] == 1 else "months"
                out += f"\n\n{g['name']} would be reached {g['months_sooner']} {unit} sooner."
        return out
    if name == "monthly_review":
        if v.get("empty"):
            return v["message"]
        r = v["report"]
        return (f"{r['headline']}\n\nWins:\n{_fmt_list(r['wins'])}\n\nRisks:\n{_fmt_list(r['risks'])}\n\n"
                "Actions for next month:\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(r["actions"], 1)))
    if name == "remember":
        return f"Got it, I'll remember: {v['memory']['content']}"
    if name == "forget":
        return ("Forgotten: " + "; ".join(v["forgotten"])) if v.get("forgotten") else v.get("message", "Done.")
    if name == "recall_memories":
        if not v["memories"]:
            return "I don't remember anything about you yet."
        return "Here's what I remember:\n\n" + _fmt_list([f"{m['content']} ({m['type']})" for m in v["memories"]])
    if name == "correct_category":
        return (f"Fixed: {v['merchant']} is now {v['category']} across {v['transactions_updated']} transactions, "
                "and I'll remember that for future imports.")
    if name == "categorize_transactions":
        return f"Categorised {v['categorized']} transactions."
    return "Done."


def _agent(req: ChatRequest) -> ChatResult:
    last_user = max(i for i, m in enumerate(req.messages) if m["role"] == "user")
    results = [(m.get("name", ""), json.loads(m["content"])) for m in req.messages[last_user:] if m["role"] == "tool"]
    if not results and any(m["role"] == "tool" for m in req.messages):
        # FORCE_ANSWER or a follow-up instruction came after the tool results.
        prev = max(i for i, m in enumerate(req.messages[:last_user]) if m["role"] == "user")
        results = [(m.get("name", ""), json.loads(m["content"])) for m in req.messages[prev:last_user]
                   if m["role"] == "tool"]
    if results:
        return ChatResult(content="\n\n".join(_answer_for(n, v) for n, v in results))
    calls = _route(req.messages[last_user]["content"]) if req.tools else []
    if calls:
        return ChatResult(content="", finish_reason="tool_calls", tool_calls=[
            ToolCall(f"call_{i}", c["name"], json.dumps(c["args"])) for i, c in enumerate(calls)])
    return ChatResult(content=(
        "I'm Ledgermind, running in offline demo mode. Ask me where your money went, about subscriptions, unusual "
        "charges, budgets, savings goals, a what-if like \"cut dining by a third\", or a monthly review."))
