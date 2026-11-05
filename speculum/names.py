"""Display names for organizations from filings, which arrive in capitals ("ERISA INDUSTRY COMMITTEE PAC; THE")."""
import re

_UPPER = {"Pac": "PAC", "Pacs": "PACs", "Llc": "LLC", "Llp": "LLP", "Lp": "LP", "Usa": "USA", "Ii": "II", "Iii": "III", "Iv": "IV",
          "Afl-Cio": "AFL-CIO", "Ibew": "IBEW", "Seiu": "SEIU", "Uaw": "UAW", "Nra": "NRA", "Aipac": "AIPAC"}


def org_name(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    t = s.title() if s.isupper() or s.islower() else s
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
