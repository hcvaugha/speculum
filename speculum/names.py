"""Display names for organizations from filings, which arrive in capitals ("ERISA INDUSTRY COMMITTEE PAC; THE")."""
import re

_UPPER = {"Pac": "PAC", "Pacs": "PACs", "Llc": "LLC", "Llp": "LLP", "Lp": "LP", "Usa": "USA", "Ii": "II", "Iii": "III", "Iv": "IV",
          "Afl-Cio": "AFL-CIO", "Ibew": "IBEW", "Seiu": "SEIU", "Uaw": "UAW", "Nra": "NRA", "Aipac": "AIPAC",
          "Ubs": "UBS", "Tiaa": "TIAA", "Hca": "HCA", "Aarp": "AARP", "Ctia": "CTIA", "Ahip": "AHIP", "Usaa": "USAA",
          "Aig": "AIG", "Jpmorgan": "JPMorgan", "Ibm": "IBM", "Ups": "UPS", "Bae": "BAE", "Abb": "ABB", "Aep": "AEP",
          "Nea": "NEA", "Aft": "AFT", "Ama": "AMA", "Aba": "ABA", "Nab": "NAB", "Nar": "NAR", "Phrma": "PhRMA",
          "Ge": "GE", "Gm": "GM", "Erisa": "ERISA", "Aicpa": "AICPA", "Naic": "NAIC", "Nahb": "NAHB", "Nreca": "NRECA", "Ncta": "NCTA", "Nfib": "NFIB", "Aflac": "Aflac", "Ny": "NY", "Nyc": "NYC", "Rtx": "RTX", "Usa,": "USA,", "Us": "US"}
_KEEP = {"Mr", "Mrs", "Ms", "Dr", "St", "Mt", "Ltd", "Hwy", "Pkwy", "Jr", "Sr", "Bros", "Wm", "Ft", "Sq", "Hq"}


def _word(w: str) -> str:
    if w in _UPPER:
        return _UPPER[w]
    if len(w) >= 2 and w not in _KEEP and not re.search(r"[aeiouyAEIOUY]", w):
        return w.upper()                                      # no vowels: an initialism (PNC, FMR, LPL, CVS)
    return w


def org_name(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    t = s.title() if s.isupper() or s.islower() else s
    if t is not s:
        t = re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)*", lambda m: _UPPER.get(m.group(0)) or "-".join(_word(p) for p in m.group(0).split("-")), t)
        t = re.sub(r"\b([A-Za-z]{1,2})&([A-Za-z]{1,2})\b", lambda m: (m.group(1) + "&" + m.group(2)).upper(), t)   # AT&T, BB&T, S&P
        t = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), t)
    else:
        t = re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)*", lambda m: _UPPER.get(m.group(0), m.group(0)), t)
    t = re.sub(r"(\w)'S\b", r"\1's", t)                       # title() capitalizes after apostrophes
    m = re.match(r"^(.*?)[;,]\s*The(\s*\(.*\))?$", t)         # "Committee Pac; The" -> "The Committee PAC"
    if m:
        t = "The " + m.group(1).strip() + (m.group(2) or "")
    words = t.split(" ")                                        # small words stay small inside a name
    t = " ".join([words[0]] + [w.lower() if w in ("Of", "And", "For", "The", "In", "On", "To", "At", "A") else w for w in words[1:]])
    return t


def is_placeholder(name: str) -> bool:
    """Filers sometimes type a placeholder ("None", "N/A") where a PAC name goes; that is not a PAC."""
    return (name or "").strip().strip(".").lower() in ("", "none", "n/a", "na", "not applicable", "-")
