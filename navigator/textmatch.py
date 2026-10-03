"""Checking that a quoted passage really is on the page it cites.

This is the guard against invented text. A model's quote is compared with the
page after both are reduced to letters and digits, so line breaks, layout
spacing, hyphenation and curly quotes cannot cause a false miss. A quote that
is close but not exact (a dropped word, a fixed typo) is accepted only above a
high similarity, and is labelled as such.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.9


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.findall(r"[a-z0-9]+", text)


def squash(text: str) -> str:
    """Letters and digits only. Joins words split by a hyphenated line break."""
    return "".join(words(text))


STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "by", "with", "is", "are", "be", "as", "at", "that", "this", "from", "its", "their", "will", "shall", "must", "all", "any"}


@dataclass(frozen=True)
class Match:
    status: str  # "exact", "fuzzy", "assembled" (table cells) or "missing"
    page: int | None
    similarity: float
    end_page: int | None = None  # set when the quote runs onto the next page


FOOTER = re.compile(r"^\s*(?:page\s*)?\d{1,3}(?:\s*of\s*\d{1,3})?\s*$|^.{0,60}\bpage\s+\d{1,3}(?:\s+of\s+\d{1,3})?\s*$", re.I)


def strip_footer(text: str) -> str:
    """Drop page-number lines at the end of a page so a quote can run across the break."""
    lines = text.rstrip().split("\n")
    while lines and (not lines[-1].strip() or FOOTER.match(lines[-1])):
        lines.pop()
    return "\n".join(lines)


def best_window_ratio(quote_words: list[str], page_words: list[str]) -> float:
    """Highest similarity between the quote and any similar-length run of page words.

    Windows only start where one of the quote's opening words appears, which
    keeps a long document fast without missing a real match: a close quote
    still shares at least one of its first few words with the page.
    """
    n = len(quote_words)
    if n == 0 or not page_words:
        return 0.0
    target = " ".join(quote_words)
    anchors = set(quote_words[:4])
    starts = [i for i, w in enumerate(page_words) if w in anchors]
    best = 0.0
    for start in starts:
        for size in (max(1, int(n * 0.85)), n, int(n * 1.15) + 1):
            window = " ".join(page_words[start : start + size])
            matcher = SequenceMatcher(None, target, window, autojunk=False)
            if matcher.real_quick_ratio() <= best or matcher.quick_ratio() <= best:
                continue
            best = max(best, matcher.ratio())
    return best


def smallest_window(needed: set[str], page_words: list[str]) -> int | None:
    """Length of the shortest run of page words containing every needed word."""
    counts: dict[str, int] = {}
    have, left, best = 0, 0, None
    for right, word in enumerate(page_words):
        if word in needed:
            counts[word] = counts.get(word, 0) + 1
            if counts[word] == 1:
                have += 1
        while have == len(needed):
            size = right - left + 1
            best = size if best is None or size < best else best
            w = page_words[left]
            if w in needed:
                counts[w] -= 1
                if counts[w] == 0:
                    have -= 1
            left += 1
    return best


def _overlap(quote_words: list[str], page_words: list[str]) -> float:
    q = set(quote_words)
    return len(q & set(page_words)) / len(q) if q else 0.0


def locate(quote: str, pages: dict[int, str], cited: int | None) -> Match:
    """Find the quote on its cited page first, then anywhere, then across page breaks."""
    key = squash(quote)
    if len(key) < 12:
        return Match("missing", None, 0.0)
    order = ([cited] if cited in pages else []) + [n for n in pages if n != cited]
    squashed = {n: squash(pages[n]) for n in order}
    for n in order:
        if key in squashed[n]:
            return Match("exact", n, 1.0)
    spans = {n: strip_footer(pages[n]) + "\n" + pages[n + 1] for n in order if n + 1 in pages}
    for n, text in spans.items():
        if key in squash(text):
            return Match("exact", n, 1.0, n + 1)
    quote_words = words(quote)
    best_page, best_end, best = None, None, 0.0
    candidates = [(n, None, pages[n]) for n in order] + [(n, n + 1, text) for n, text in spans.items()]
    # Only pages that share most of the quote's vocabulary can hold a 90% match.
    candidates = [c for c in candidates if _overlap(quote_words, words(c[2])) >= 0.8]
    for n, end, text in candidates:
        ratio = best_window_ratio(quote_words, words(text))
        if ratio > best + 1e-9:
            best_page, best_end, best = n, end, ratio
    if best >= FUZZY_THRESHOLD:
        return Match("fuzzy", best_page, round(best, 3), best_end)
    # Tables: a model reading across cells writes the right words in a new order.
    # Accept only when every content word sits close together on one page.
    needed = {w for w in quote_words if w not in STOPWORDS}
    if len(needed) >= 5:
        for n in order:
            size = smallest_window(needed, words(pages[n]))
            if size is not None and size <= 3 * len(quote_words):
                return Match("assembled", n, round(len(quote_words) / size, 3))
    return Match("missing", None, round(best, 3))


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, " ".join(words(a)), " ".join(words(b)), autojunk=False).ratio()
