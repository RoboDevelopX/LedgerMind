"""Categories, merchant normalisation and local categorisation rules.

Raw bank descriptions ("UPI/P2M/412398765432/SWIGGY/YESB/swiggy@ybl/Order") stay in SQLite. Everything else,
including what a model may see, uses the normalised merchant name ("Swiggy").
"""
from __future__ import annotations

import re

CATEGORIES = [
    "Income", "Housing", "Utilities", "Groceries", "Dining", "Transport", "Shopping", "Subscriptions",
    "Health", "Entertainment", "Travel", "Education", "Insurance", "Fees", "Gifts", "Cash",
    "Savings", "Transfers", "Other",
]
# Money moved, not money spent: excluded from spending totals.
NON_SPENDING = {"Income", "Savings", "Transfers"}

P2P_MERCHANT = "Person-to-person transfer"

# (pattern, canonical merchant, category). First match wins; patterns run on the lowercased description.
KNOWN: list[tuple[str, str, str]] = [
    (r"salary|payroll|paycheck|direct dep", "", "Income"),
    (r"upwork", "Upwork", "Income"),
    (r"freelance|fiverr|toptal", "", "Income"),
    (r"interest credit|int\.pd|interest paid", "Bank interest", "Income"),
    (r"\brent\b|landlord|nobroker|nestaway|mortgage", "Rent", "Housing"),
    (r"bescom", "BESCOM", "Utilities"),
    (r"pg&e", "PG&E", "Utilities"),
    (r"tata power|adani elec|electricity|msedcl", "Electricity", "Utilities"),
    (r"airtel", "Airtel", "Utilities"),
    (r"jio(?!hotstar)", "Jio", "Utilities"),
    (r"verizon", "Verizon", "Utilities"),
    (r"act fibernet", "ACT Fibernet", "Utilities"),
    (r"comcast|xfinity", "Comcast", "Utilities"),
    (r"internet|broadband", "Broadband", "Utilities"),
    (r"bwssb|water bill|indane|piped gas|mahanagar gas", "Water & gas", "Utilities"),
    (r"bigbasket|big basket", "BigBasket", "Groceries"),
    (r"blinkit", "Blinkit", "Groceries"),
    (r"zepto", "Zepto", "Groceries"),
    (r"dmart|d-mart|avenue supermarts", "DMart", "Groceries"),
    (r"trader joe", "Trader Joe's", "Groceries"),
    (r"whole foods", "Whole Foods", "Groceries"),
    (r"safeway", "Safeway", "Groceries"),
    (r"more retail|reliance fresh|nature'?s basket|kroger|aldi|supermarket", "", "Groceries"),
    (r"swiggy instamart", "Swiggy Instamart", "Groceries"),
    (r"swiggy", "Swiggy", "Dining"),
    (r"zomato", "Zomato", "Dining"),
    (r"doordash", "DoorDash", "Dining"),
    (r"uber eats", "Uber Eats", "Dining"),
    (r"starbucks", "Starbucks", "Dining"),
    (r"third wave", "Third Wave Coffee", "Dining"),
    (r"cafe|coffee|restaurant|dhaba|biryani|pizza|sushi", "", "Dining"),
    (r"uber", "Uber", "Transport"),
    (r"\bola\b|olacabs", "Ola", "Transport"),
    (r"rapido", "Rapido", "Transport"),
    (r"lyft", "Lyft", "Transport"),
    (r"bmrcl|namma metro", "Namma Metro", "Transport"),
    (r"metro|bmtc|clipper|transit", "Public transit", "Transport"),
    (r"indian ?oil|hpcl|bharat petroleum|bpcl|shell|fuel|petrol", "Fuel", "Transport"),
    (r"fastag", "FASTag", "Transport"),
    (r"netflix", "Netflix", "Subscriptions"),
    (r"spotify", "Spotify", "Subscriptions"),
    (r"icloud", "iCloud+", "Subscriptions"),
    (r"apple\.com/bill|apple music|itunes", "Apple Music", "Subscriptions"),
    (r"jiohotstar|hotstar", "JioHotstar", "Subscriptions"),
    (r"hulu", "Hulu", "Subscriptions"),
    (r"youtube ?premium|google \*youtube", "YouTube Premium", "Subscriptions"),
    (r"audible", "Audible", "Subscriptions"),
    (r"amazon prime|primevideo|prime video", "Amazon Prime", "Subscriptions"),
    (r"cult\.?fit|cultfit|curefit", "Cult.fit", "Subscriptions"),
    (r"equinox|\bgym\b", "Gym", "Subscriptions"),
    (r"notion|chatgpt|openai|adobe|canva", "", "Subscriptions"),
    (r"amazon|amzn", "Amazon", "Shopping"),
    (r"flipkart", "Flipkart", "Shopping"),
    (r"myntra", "Myntra", "Shopping"),
    (r"croma", "Croma", "Shopping"),
    (r"apple store", "Apple Store", "Shopping"),
    (r"best buy", "Best Buy", "Shopping"),
    (r"target", "Target", "Shopping"),
    (r"reliance digital|vijay sales|ikea|decathlon|nykaa", "", "Shopping"),
    (r"apollo", "Apollo Pharmacy", "Health"),
    (r"practo", "Practo", "Health"),
    (r"cvs", "CVS Pharmacy", "Health"),
    (r"pharmeasy|1mg|medplus|pharmacy|hospital|clinic|diagnostic|dental|one medical", "", "Health"),
    (r"bookmyshow", "BookMyShow", "Entertainment"),
    (r"pvr|inox", "PVR INOX", "Entertainment"),
    (r"amc theatres", "AMC Theatres", "Entertainment"),
    (r"ticketmaster", "Ticketmaster", "Entertainment"),
    (r"cinema|steam|playstation|concert", "", "Entertainment"),
    (r"indigo", "IndiGo", "Travel"),
    (r"air india", "Air India", "Travel"),
    (r"makemytrip", "MakeMyTrip", "Travel"),
    (r"airbnb", "Airbnb", "Travel"),
    (r"delta air", "Delta", "Travel"),
    (r"united air", "United Airlines", "Travel"),
    (r"marriott", "Marriott", "Travel"),
    (r"vistara|akasa|goibibo|cleartrip|irctc|oyo|hotel|taj ", "", "Travel"),
    (r"coursera|udemy|byju|unacademy|school fee|tuition", "", "Education"),
    (r"acko", "Acko", "Insurance"),
    (r"geico", "GEICO", "Insurance"),
    (r"lic |insurance|hdfc ergo|icici lombard|star health", "", "Insurance"),
    (r"zerodha", "Zerodha", "Savings"),
    (r"vanguard", "Vanguard", "Savings"),
    (r"groww|mutual fund|\bsip\b|ppf|recurring deposit|transfer to savings", "", "Savings"),
    (r"\bfee\b|charges|penalty|late payment|gst on", "Bank charges", "Fees"),
    (r"atm|cash withdrawal", "ATM cash", "Cash"),
]

_PREFIXES = re.compile(
    r"^(upi/(dr|cr)/|upi/p2m/|upi-|pos |ecom |neft (cr|dr)-|imps[/-]|ach d- ?|nach[/-]|bil/onl/|vin/|"
    r"sq \*|paypal \*|tst\* ?|int'l pos |intl pos )", re.I)


def is_p2p(description: str) -> bool:
    """UPI person-to-person payments carry someone's name; they are never named to a model."""
    return bool(re.search(r"\bP2[PA]\b|/P2[PA]/", description, re.I))


def clean_description(description: str) -> str:
    """Strip bank noise (prefixes, reference numbers, UPI handles, card digits) and title-case the rest."""
    d = description.strip()
    for _ in range(2):
        d = _PREFIXES.sub("", d)
    d = re.sub(r"[/*#|_]+|(?<=\w)-(?=\w)", " ", d)
    d = re.sub(r"\S+@\S+", " ", d)                # UPI handles / emails
    d = re.sub(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", " ", d)  # IFSC codes
    d = re.sub(r"\b(?:x+)?\d[\d-]{3,}\b", " ", d, flags=re.I)
    d = re.sub(r"\b(yesb|icic|hdfc|sbin|utib|kkbk|okaxis|oksbi|ybl|paytm|payment|order|txn|ref|pvt|ltd|limited|"
               r"private|inc|llc|salary|sal|bangalore|bengaluru|mumbai|delhi|pune|chennai|hyderabad|in|"
               r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
               " ", d, flags=re.I)
    d = re.sub(r"\b(?=\w*\d)(?=\w*[a-z])\w{5,}\b", " ", d, flags=re.I)  # booking codes like HM4K2Z
    d = re.sub(r"\s+", " ", d).strip(" -.")
    words = d.split()[:3]
    return " ".join(w if w.isupper() and len(w) <= 3 else w.capitalize() for w in words) or "Unknown"


def rule_match(description: str) -> tuple[str, str] | None:
    """(merchant, category) from local rules, or None if no rule applies."""
    if is_p2p(description):
        return P2P_MERCHANT, "Transfers"
    low = description.lower()
    for pattern, merchant, category in KNOWN:
        if re.search(pattern, low):
            return merchant or clean_description(description), category
    return None


def normalise(description: str) -> str:
    """Best local guess at the merchant name."""
    match = rule_match(description)
    return match[0] if match else clean_description(description)


def closest_category(name: str) -> str | None:
    """Map free text ("restaurants", "food delivery") to a category, or None."""
    n = name.strip().lower()
    aliases = {"food": "Dining", "eating out": "Dining", "restaurants": "Dining", "food delivery": "Dining",
               "rent": "Housing", "bills": "Utilities", "cabs": "Transport", "taxi": "Transport",
               "travel": "Travel", "trips": "Travel", "shopping": "Shopping", "medical": "Health",
               "movies": "Entertainment", "investments": "Savings", "salary": "Income"}
    for c in CATEGORIES:
        if c.lower() == n or c.lower().rstrip("s") == n.rstrip("s"):
            return c
    if n in aliases:
        return aliases[n]
    for c in CATEGORIES:
        if n and (n in c.lower() or c.lower() in n):
            return c
    return None
