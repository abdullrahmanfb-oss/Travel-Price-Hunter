"""
One place that turns a point-of-sale code into something a human reads.

Every surface (digest email, dashboard, CLI) goes through these helpers —
bare ISO codes like "PL" or "KZ" should never reach the user again.
"""

NAME = {
    "SA": "Saudi Arabia", "AE": "UAE", "QA": "Qatar", "KW": "Kuwait",
    "BH": "Bahrain", "OM": "Oman", "JO": "Jordan", "EG": "Egypt",
    "MA": "Morocco", "TR": "Turkey", "IN": "India", "PK": "Pakistan",
    "LK": "Sri Lanka", "KZ": "Kazakhstan", "AR": "Argentina",
    "US": "United States",
    "GB": "United Kingdom", "DE": "Germany", "PT": "Portugal",
    "PL": "Poland", "RO": "Romania", "HU": "Hungary", "ZA": "South Africa",
    "JP": "Japan", "KR": "South Korea", "SG": "Singapore",
    "MY": "Malaysia", "TH": "Thailand", "ID": "Indonesia",
}

HOME = "SA"


def name(code) -> str:
    return NAME.get((code or "").upper(), code or "?")


def flag(code) -> str:
    """Flag emoji from the ISO code via Unicode regional indicators."""
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


def label(code) -> str:
    """'🇵🇱 Poland' — the standard way to print a country."""
    f = flag(code)
    return f"{f} {name(code)}" if f else name(code)


def full(code) -> str:
    """'🇵🇱 Poland (PL)' — when the code itself still matters."""
    return f"{label(code)} ({(code or '').upper()})"
