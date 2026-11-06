"""Which sector a lobbying client belongs to, from its own description of its business.

Every lobbying registration carries the client's "general description" in its own words ("bank holding
company", "private equity firm", "labor union representing nurses"). A client is placed in the first rule
below whose words appear in that description; when the description is blank, its name is read instead. A
client the filing marks as a government entity is placed under governments.

The order is part of the rule: "credit union" is read as banking before "union" can be read as labor; a trade
association that says whose interests it represents is placed with them, before the catch-all for
associations; generic words such as "company" come last. Words match whole words; a word ending in * matches
any word that starts with it ("pharmaceut*" matches "pharmaceuticals").

The rules are published on the Method page and are never changed because of what they show.
"""
from __future__ import annotations

import re

# (key, words). A key may appear more than once; the first matching rule wins.
RULES = [
    ("government", ["municipal*", "county", "counties", "city of", "town of", "village of", "state government",
                    "state agency", "tribe", "tribes", "tribal", "local government", "public housing authority",
                    "port authority", "transit authority", "school district", "water district", "irrigation district",
                    "public utility district", "public power", "regional council", "council of governments",
                    "economic development", "development authority"]),
    ("banks", ["bank*", "bancorp*", "credit union*", "mortgage*", "lender*", "lending", "savings and loan*", "thrift*",
               "consumer finance", "student loan*"]),
    ("investment", ["private equity", "asset manag*", "investment manag*", "investment advis*", "investment firm*",
                    "investment compan*", "hedge fund*", "venture capital", "alternative asset*", "alternative investment*",
                    "wealth manag*", "fund manag*", "private capital", "private credit", "private investment*",
                    "investment bank*", "investment fund*", "real estate invest*", "capital markets"]),
    ("insurance", ["insurance", "insurer*", "reinsur*", "underwrit*"]),
    ("finance", ["financial", "fintech", "payment*", "securities", "broker*", "stock exchange*", "securities exchange*",
                 "credit card*", "accounting", "accountant*", "tax prepar*", "crypto*", "digital asset*", "blockchain"]),
    ("labor", ["labor union*", "labor organization*", "trade union*", "union representing", "union of", "afl-cio",
               "brotherhood", "teamsters", "federation of teachers", "workers union", "employees union",
               "international union", "labor federation", "union"]),
    ("defense", ["defense", "defence", "aerospace", "military", "munition*", "weapon*", "shipbuild*", "national security",
                 "armed forces"]),
    ("health", ["pharmaceut*", "biopharm*", "biotech*", "medical", "medicine*", "health*", "hospital*", "physician*",
                "clinic*", "drug*", "nursing", "nurses", "dental", "dentist*", "therapeut*", "diagnostic*", "life science*", "patient*", "dermatolog*",
                "surgeon*", "surgical", "oncolog*", "cardiolog*", "radiolog*", "anesthesi*", "pediatric*", "psychiatr*",
                "kidney", "cancer", "disease*", "pharmac*", "laborator*", "rehabilitat*", "inpatient", "emergency medic*"]),
    ("energy", ["oil", "natural gas", "petroleum", "energy", "electric*", "utility", "utilities", "power generat*",
                "nuclear", "solar", "wind", "coal", "mining", "minerals", "pipeline*", "renewable*", "refin*", "hydrogen",
                "batter*"]),
    ("tech", ["technolog*", "software", "internet", "telecommunic*", "telecom*", "wireless", "broadband", "semiconductor*",
              "computer*", "data", "cloud", "artificial intelligence", "cyber*", "media", "broadcast*", "cable", "satellite*",
              "video game*", "streaming", "publish*", "newspaper*", "e-commerce", "online", "digital", "app"]),
    ("transport", ["airline*", "aviation", "airport*", "railroad*", "railway*", "rail", "trucking", "shipping",
                   "transportation", "automotive", "automobile*", "vehicle*", "logistic*", "maritime", "freight",
                   "truck*", "motor*", "ports"]),
    ("realestate", ["real estate", "construction", "homebuild*", "home build*", "housing", "realtor*", "propert*",
                    "apartment*", "builder*", "engineering", "architect*", "infrastructure"]),
    ("agriculture", ["agricultur*", "farm*", "food*", "beverage*", "crop*", "dairy", "livestock", "grocer*",
                     "restaurant*", "sugar", "seed*", "fertiliz*", "cattle", "poultry", "fishing", "fisheries", "seafood",
                     "brew*", "distill*", "wine*", "tobacco", "ranch*"]),
    ("industry", ["manufactur*", "chemical*", "steel", "aluminum", "retail*", "consumer product*", "apparel", "industrial",
                  "gaming", "casino*", "hotel*", "travel", "sports", "entertainment", "cosmetic*", "packaging", "paper",
                  "plastic*", "waste", "recycl*", "staffing", "franchis*", "textile*", "glass", "lumber", "forest*"]),
    ("education", ["universit*", "college*", "school*", "education*", "academ*", "research institut*", "museum*",
                   "librar*", "scientific", "science", "teacher*", "student*"]),
    ("professional", ["law firm*", "lobbying", "government relations", "consulting", "consultant*", "public affairs",
                      "public relations", "advisory"]),
    ("advocacy", ["nonprofit", "non-profit", "not-for-profit", "not for profit", "advocacy", "advocate*", "foundation",
                  "charit*", "civil rights", "public interest", "humanitarian", "relief", "religious", "church*", "faith",
                  "veteran*", "conservation", "environmental", "human rights", "citizens", "grassroots", "think tank",
                  "policy institute", "social welfare", "501(c)", "rights", "democracy", "voting", "voters"]),
    ("trade", ["trade association*", "trade group*", "industry association*", "business association*", "chamber of commerce",
               "chambers of commerce", "association*", "assn", "coalition*", "council", "alliance", "federation", "institute",
               "society", "members"]),
    ("industry", ["compan*", "corporation*", "corp", "business*", "firm*", "products", "services", "manufacturer*",
                  "enterprise*", "holding*", "group", "inc"]),
]

NAMES = {
    "government": "Governments and public bodies", "banks": "Banks, credit unions and lending",
    "investment": "Private equity, investment and asset management", "insurance": "Insurance", "labor": "Labor unions",
    "defense": "Defense and aerospace", "health": "Health, pharmaceuticals and medical", "energy": "Energy, utilities and mining",
    "tech": "Technology, telecom and media", "transport": "Transportation", "realestate": "Real estate and construction",
    "agriculture": "Agriculture, food and beverage", "finance": "Other financial services",
    "industry": "Manufacturing, retail and other business", "education": "Education and research",
    "professional": "Law, lobbying and consulting firms", "advocacy": "Advocacy groups and nonprofits",
    "trade": "Trade and business associations (sector not stated)", "other": "Not described",
}
GROUPS = {k: "Business" for k in NAMES}
GROUPS.update({"government": "Governments", "labor": "Labor unions", "advocacy": "Advocacy and nonprofits",
               "education": "Education and research", "other": "Not described"})
GROUP_ORDER = ["Business", "Labor unions", "Advocacy and nonprofits", "Education and research", "Governments", "Not described"]
ORDER = list(NAMES)


def _rx(words):
    parts = [re.escape(w[:-1]) + r"[a-z0-9-]*" if w.endswith("*") else re.escape(w) for w in words]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")


_RULES = [(k, _rx(words)) for k, words in RULES]


def sector(description: str | None, name: str | None = None, government: bool = False) -> str:
    if government:
        return "government"
    for text in ((description or "").strip().lower(), (name or "").strip().lower()):
        if not text:
            continue
        for key, rx in _RULES:
            if rx.search(text):
                return key
    return "other"
