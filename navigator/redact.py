"""Removes contact details from published sample answers.

Source documents can name staff with their email and phone (a college memo
lists its procurement contacts). The saved sample answers in this repository
are redacted so the repo does not republish people's details.
Shared with Solicitation Analyzer.
"""
from __future__ import annotations

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+?1[-. ]?)?\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b(?:\s*(?:x|ext\.?)\s*\d+)?", re.I)
NAME = r"(?:[A-Z][a-zA-Z.'-]+\s){1,2}[A-Z][a-zA-Z'-]+"
# "Primary Contact: Pat Doe", "Contracting Officer (CO) Pat Doe", "Attn: Pat Doe"
NAMED_CONTACT = re.compile(
    r"\b((?i:(?:primary|secondary|designated)\s+)?(?i:contacts?|contracting officer|contract specialist|contract manager|poc|attn)"
    r"(?:\s*\((?:CO|CS|CM|POC)\))?)(\s*(?:is|:|-|,)?\s*)(" + NAME + r")"
)
# "Pat Doe at [email]" once the address itself is gone
NAME_AT_EMAIL = re.compile(r"\b" + NAME + r"(?=\s+(?:at|:|,)?\s*\[email\])")


def contact_names(texts: list[str]) -> set[str]:
    """People named as contacts anywhere in the documents, so they can be removed everywhere.

    Two sources: a name after a contact label, and first.last email addresses.
    Form fields print names in capitals with no label, so matching by the
    collected names catches what the patterns alone would miss.
    """
    names: set[str] = set()
    for text in texts:
        for m in NAMED_CONTACT.finditer(text):
            names.add(m.group(3).strip())
        for m in EMAIL.finditer(text):
            parts = [p for p in re.split(r"[._]", m.group(0).split("@")[0]) if p.isalpha() and len(p) > 1]
            if len(parts) >= 2:
                names.add(" ".join(parts[:2]))
    return {n for n in names if len(n) > 4 and len(n.split()) >= 2}


def redact(text: str, names: set[str] | frozenset[str] = frozenset()) -> str:
    text = EMAIL.sub("[email]", text)
    text = PHONE.sub("[phone]", text)
    for name in sorted(names, key=len, reverse=True):
        pattern = r"\b" + r"[\s.]+".join(re.escape(part) for part in name.split()) + r"\b"
        text = re.sub(pattern, "[name]", text, flags=re.I)
    text = NAMED_CONTACT.sub(lambda m: f"{m.group(1)}{m.group(2) or ' '}[name]", text)
    return NAME_AT_EMAIL.sub("[name]", text)
