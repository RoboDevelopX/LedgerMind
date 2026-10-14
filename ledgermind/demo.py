"""Seeded synthetic 12-month bank statement for a fictional person. No real personal data.

The story the data tells (dates are relative to the end date, so the demo always looks current):
salary with an April raise, irregular freelance income, rent, groceries, dining, transport, 7 subscriptions
(Netflix raises its price 4 months ago; Apple Music, billed as "APPLE.COM/BILL", has been running since before
Spotify started, so it looks forgotten), seasonal travel, a big electronics purchase, an unusually large food order,
a dining spike last month, and one duplicate Amazon charge this week.
"""
from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass
from datetime import date, timedelta

from .periods import add_months, month_end, today


@dataclass
class Txn:
    date: date
    description: str
    amount: float  # negative = money out


@dataclass(frozen=True)
class Profile:
    currency: str
    scale: float            # multiplier applied to the INR amounts below
    employer: str
    salary: float
    raise_pct: float
    merchants: dict[str, list[str]]
    fixed: dict[str, str]


INR = Profile(
    currency="INR", scale=1.0, employer="NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY", salary=142500, raise_pct=9.5,
    merchants={
        "groceries": ["BIGBASKET BANGALORE", "UPI/P2M/{ref}/BLINKIT/blinkit@ybl/Order", "ZEPTO MARKETPLACE",
                      "DMART AVENUE SUPERMARTS"],
        "delivery": ["UPI/P2M/{ref}/SWIGGY/swiggy@ybl/Order", "UPI/P2M/{ref}/ZOMATO/zomato@hdfcbank/Food"],
        "eat_out": ["POS XXXXXXXXXXXX4821 THIRD WAVE COFFEE", "UPI/P2M/{ref}/TOIT BREWPUB/toit@icici/Payment",
                    "MTR 1924 RESTAURANT", "SECOND WIFE CAFE INDIRANAGAR"],
        "rides": ["UBER INDIA SYSTEMS", "OLA CABS ANI TECHNOLOGIES", "RAPIDO BIKE TAXI"],
        "metro": ["BMRCL METRO SMART CARD RECHARGE"],
        "fuel": ["INDIAN OIL PETROL PUMP HSR"],
        "shopping": ["AMAZON PAY INDIA AMZN MKTP", "FLIPKART INTERNET PVT", "MYNTRA DESIGNS"],
        "health": ["APOLLO PHARMACY KORAMANGALA", "PRACTO DIAGNOSTIC LABS"],
        "fun": ["BOOKMYSHOW", "PVR INOX CINEMAS"],
        "p2p_out": ["UPI/P2P/{ref}/RAHUL VERMA/rahul.v@okaxis/dinner split",
                    "UPI/P2P/{ref}/PRIYA NAIR/9876501234@ybl/trip share"],
        "p2p_in": ["UPI/P2P/{ref}/KARAN MEHTA/karan.m@oksbi/movie tickets"],
    },
    fixed={"freelance": "INWARD REMIT-UPWORK GLOBAL INC FREELANCE", "interest": "INT.PD SAVINGS INTEREST",
           "rent": "CRED RENTPAY/HOUSE RENT", "sip": "ACH D- ZERODHA BROKING-SIP",
           "electricity": "BBPS/BESCOM/{ref}/ELECTRICITY", "broadband": "ACT FIBERNET BROADBAND BILL",
           "mobile": "AIRTEL POSTPAID BILL", "atm": "ATM CASH WDL SBI ATM KORAMANGALA",
           "flight": "INDIGO 6E AIRLINES BOOKING", "hotel": "MAKEMYTRIP HOTEL BOOKING",
           "flight2": "AIR INDIA ONLINE BOOKING", "stay": "AIRBNB * HM4K2Z",
           "insurance": "ACKO GENERAL INSURANCE POLICY RENEWAL", "fee": "CREDIT CARD ANNUAL FEE",
           "fee_tax": "GST ON ANNUAL FEE", "big": "CROMA ELECTRONICS INFINITI RETAIL",
           "dup": "AMAZON PAY INDIA AMZN MKTP ORDER 402-7731", "netflix": "NETFLIX.COM SI",
           "spotify": "SPOTIFY INDIA", "apple_music": "APPLE.COM/BILL", "icloud": "APPLE.COM/BILL ICLOUD 50GB",
           "gym": "CULTFIT CUREFIT HEALTHCARE", "youtube": "GOOGLE *YOUTUBEPREMIUM", "audible": "AUDIBLE INDIA",
           "hotstar": "JIOHOTSTAR SUPER QUARTERLY"})

USD = Profile(
    currency="USD", scale=1 / 30, employer="ACME CORP PAYROLL DIRECT DEP", salary=142500, raise_pct=6.0,
    merchants={
        "groceries": ["TRADER JOE'S #552", "WHOLE FOODS MARKET", "SAFEWAY #1234"],
        "delivery": ["DOORDASH*ORDER", "UBER EATS"],
        "eat_out": ["STARBUCKS STORE 0921", "BLUE BOTTLE COFFEE", "TST* NOPA RESTAURANT", "SUSHI RAN"],
        "rides": ["UBER TRIP", "LYFT RIDE"],
        "metro": ["CLIPPER TRANSIT RELOAD"],
        "fuel": ["SHELL OIL 5741"],
        "shopping": ["AMAZON MKTPL*A12BC", "TARGET 00012", "BEST BUY 00321"],
        "health": ["CVS PHARMACY 01234", "ONE MEDICAL CLINIC"],
        "fun": ["AMC THEATRES", "TICKETMASTER"],
        "p2p_out": ["ZELLE P2P TO JORDAN LEE"],
        "p2p_in": ["VENMO P2P FROM SAM RIVERA"],
    },
    fixed={"freelance": "UPWORK ESCROW INC FREELANCE", "interest": "INTEREST PAID SAVINGS",
           "rent": "MAPLE STREET APTS RENT", "sip": "VANGUARD BROKERAGE SIP TRANSFER",
           "electricity": "PG&E ELECTRIC BILL {ref}", "broadband": "COMCAST XFINITY INTERNET",
           "mobile": "VERIZON WIRELESS BILL", "atm": "ATM CASH WITHDRAWAL 0042",
           "flight": "DELTA AIR LINES", "hotel": "MARRIOTT HOTEL CHICAGO", "flight2": "UNITED AIRLINES",
           "stay": "AIRBNB * HM4K2Z", "insurance": "GEICO AUTO INSURANCE", "fee": "CREDIT CARD ANNUAL FEE",
           "fee_tax": "FOREIGN TRANSACTION FEE", "big": "APPLE STORE R102 PURCHASE",
           "dup": "AMAZON MKTPL*ZX81Q ORDER", "netflix": "NETFLIX.COM", "spotify": "SPOTIFY USA",
           "apple_music": "APPLE.COM/BILL", "icloud": "APPLE.COM/BILL ICLOUD 50GB",
           "gym": "EQUINOX GYM MEMBERSHIP", "youtube": "GOOGLE *YOUTUBEPREMIUM", "hotstar": "HULU QUARTERLY",
           "audible": "AUDIBLE.COM"})

# (fixed descriptor key, INR amount, day of month, every n months, first month index)
SUBSCRIPTIONS = [
    ("netflix", 649, 6, 1, 0),
    ("spotify", 119, 14, 1, 3),
    ("apple_music", 99, 2, 1, 0),
    ("icloud", 75, 18, 1, 0),
    ("gym", 1299, 1, 1, 0),
    ("youtube", 149, 17, 1, 0),
    ("hotstar", 299, 22, 3, 1),
    ("audible", 199, 9, 1, 10),   # new in the last two months
]
NETFLIX_NEW_PRICE = 799   # from month index 8, i.e. four months before the end


class _Gen:
    def __init__(self, profile: Profile, end: date, seed: int) -> None:
        self.p, self.end, self.rng = profile, end, random.Random(seed)
        self.start = add_months(end, -11)
        self.rows: list[Txn] = []
        self._ref = 412300000000

    def ref(self) -> str:
        self._ref += self.rng.randint(1000, 99999)
        return str(self._ref)

    def amt(self, inr: float) -> float:
        value = inr * self.p.scale
        return round(value, 2) if self.p.currency == "USD" else round(value)

    def add(self, d: date, desc: str, inr: float) -> None:
        if self.start <= d <= self.end:
            self.rows.append(Txn(d, desc.replace("{ref}", self.ref()), self.amt(inr)))

    def pick(self, kind: str) -> str:
        return self.rng.choice(self.p.merchants[kind])

    def day(self, month: date, lo: int = 1, hi: int = 28) -> date:
        return month.replace(day=self.rng.randint(lo, min(hi, month_end(month).day)))


def generate(currency: str = "INR", end: date | None = None, seed: int = 42) -> list[Txn]:
    """Twelve months of transactions ending on `end` (default today), sorted by date."""
    p = USD if currency.upper() == "USD" else INR
    g = _Gen(p, end or today(), seed)
    for i in range(12):
        month = add_months(g.start, i)
        last_day = month_end(month)
        raised = any(add_months(g.start, j).month == 4 for j in range(1, i + 1))
        salary = p.salary * (1 + p.raise_pct / 100) if raised else p.salary
        payday = last_day - timedelta(days=max(0, last_day.weekday() - 4))
        g.add(payday, f"{p.employer} {month:%b}".upper(), salary)
        if g.rng.random() < 0.45:
            g.add(g.day(month, 8, 25), p.fixed["freelance"], g.rng.randint(18, 65) * 1000)
        if month.month in (1, 4, 7, 10):
            g.add(month.replace(day=1), p.fixed["interest"], g.rng.randint(1500, 2200))
        g.add(month.replace(day=3), p.fixed["rent"], -38000)
        g.add(month.replace(day=5), p.fixed["sip"], -15000)
        summer = month.month in (4, 5, 6)
        g.add(month.replace(day=12), p.fixed["electricity"],
              -g.rng.randint(1900, 2600) if summer else -g.rng.randint(900, 1500))
        g.add(month.replace(day=8), p.fixed["broadband"], -1179)
        g.add(month.replace(day=20), p.fixed["mobile"], -599)
        for desc, price, dom, every, first in SUBSCRIPTIONS:
            if i >= first and (i - first) % every == 0:
                cost = NETFLIX_NEW_PRICE if desc == "netflix" and i >= 8 else price
                g.add(month.replace(day=min(dom, last_day.day)), p.fixed[desc], -cost)
        for _ in range(g.rng.randint(8, 11)):
            g.add(g.day(month), g.pick("groceries"), -g.rng.randint(380, 3400))
        for _ in range(g.rng.randint(8, 13)):
            g.add(g.day(month), g.pick("delivery"), -g.rng.randint(240, 880))
        for _ in range(g.rng.randint(2, 5) + (8 if i == 10 else 0)):
            g.add(g.day(month), g.pick("eat_out"), -g.rng.randint(450, 2800))
        for _ in range(g.rng.randint(6, 12)):
            g.add(g.day(month), g.pick("rides"), -g.rng.randint(120, 650))
        for d in (2, 16):
            g.add(month.replace(day=d), g.pick("metro"), -500)
        if g.rng.random() < 0.4:
            g.add(g.day(month), g.pick("fuel"), -g.rng.randint(1500, 3000))
        for _ in range(g.rng.randint(2, 5)):
            g.add(g.day(month), g.pick("shopping"), -g.rng.randint(299, 4999))
        if g.rng.random() < 0.5:
            g.add(g.day(month), g.pick("health"), -g.rng.randint(300, 2200))
        for _ in range(g.rng.randint(0, 2)):
            g.add(g.day(month), g.pick("fun"), -g.rng.randint(450, 1600))
        if g.rng.random() < 0.6:
            g.add(g.day(month), g.pick("p2p_out"), -g.rng.randint(500, 3000))
        if g.rng.random() < 0.3:
            g.add(g.day(month), g.pick("p2p_in"), g.rng.randint(400, 1500))
        if g.rng.random() < 0.7:
            g.add(g.day(month), p.fixed["atm"], -2000)
        if month.month in (10, 11) and i < 11:   # Diwali trip home
            g.add(g.day(month, 1, 10), p.fixed["flight"], -14600)
            g.add(g.day(month, 10, 20), p.fixed["hotel"], -18200)
        if month.month == 5:          # summer holiday
            g.add(month.replace(day=4), p.fixed["flight2"], -22400)
            g.add(month.replace(day=9), p.fixed["stay"], -26100)
        if month.month == 2:
            g.add(month.replace(day=15), p.fixed["insurance"], -12450)
        if month.month == 7:
            g.add(month.replace(day=10), p.fixed["fee"], -500)
            g.add(month.replace(day=10), p.fixed["fee_tax"], -90)
        if i == 9:   # two months before the end: big one-off purchase
            g.add(g.day(month, 5, 15), p.fixed["big"], -74990)
        if i == 10:  # last month: unusually large food order (team party)
            g.add(g.day(month, 15, 25), p.merchants["delivery"][0], -4850)
    dup = max(g.end - timedelta(days=3), g.start)
    for _ in range(2):  # the same order charged twice
        g.add(dup, p.fixed["dup"], -2499)
    g.rows.sort(key=lambda t: t.date)
    return g.rows


def to_csv(rows: list[Txn], currency: str = "INR") -> str:
    """A bank-style export: a few preamble lines, then Withdrawal/Deposit columns (INR) or Amount (USD)."""
    out = io.StringIO()
    w = csv.writer(out)
    if currency.upper() == "USD":
        w.writerow(["Demo Bank of Example - Checking (fictional data)"])
        w.writerow([])
        w.writerow(["Date", "Description", "Amount"])
        for t in rows:
            w.writerow([t.date.strftime("%m/%d/%Y"), t.description, f"{t.amount:.2f}"])
        return out.getvalue()
    balance = 210000.0
    w.writerow(["Ledgermind Demo Bank - Statement of Account (fictional data)"])
    w.writerow(["Account Holder: Demo User", "Account No: XXXXXXXX4821"])
    if rows:
        w.writerow([f"Period: {rows[0].date:%d/%m/%Y} to {rows[-1].date:%d/%m/%Y}"])
    w.writerow([])
    w.writerow(["Date", "Narration", "Withdrawal Amt", "Deposit Amt", "Closing Balance"])
    for t in rows:
        balance += t.amount
        w.writerow([t.date.strftime("%d/%m/%Y"), t.description,
                    f"{-t.amount:.2f}" if t.amount < 0 else "", f"{t.amount:.2f}" if t.amount > 0 else "",
                    f"{balance:.2f}"])
    return out.getvalue()
