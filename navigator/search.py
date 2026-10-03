"""Hybrid search: keyword ranking (BM25) and embeddings, fused by rank.

Keywords catch exact terms such as "micro-purchase" or "§ 3-08"; embeddings
catch the same idea in other words ("buy without bids"). Reciprocal rank
fusion combines the two lists without tuning their scores against each other.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .sources import Document, Passage, is_history

EMBED_MODEL = "text-embedding-nomic-embed-text-v1.5"
STOP = set("a an and are as at be by for from has have how if in into is it its may must not of on or shall that the their this to under was what when which who will with does do can".split())

JURISDICTION_WORDS = {
    "CUNY": r"\bcuny\b|city university|\bcollege\b",
    "New York City": r"\bnyc\b|new york city|\bcity agenc|\bppb\b|charter|\bmocs\b|\bthe city\b",
    "New York State": r"\bnys\b|new york state|\bstate agenc|\bogs\b|state finance law|\bsdvob\b|\bthe state\b",
    "Federal": r"\bfederal\b|\bfar\b|\bsba\b|vetcert|\bsdvosb\b|\bsam\b|\bcfr\b",
}


def tokens(text: str) -> list[str]:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text.lower())  # 1,500,000 -> 1500000
    out = []
    for t in re.findall(r"[a-z0-9]+(?:[-/][a-z0-9]+)*", text):
        if t in STOP or len(t) < 2:
            continue
        out.append(t[:-1] if len(t) > 4 and t.endswith("s") and not t.endswith("ss") else t)
    return out


def jurisdictions(question: str) -> set[str]:
    q = question.lower()
    return {name for name, pattern in JURISDICTION_WORDS.items() if re.search(pattern, q)}


@dataclass
class Hit:
    passage: Passage
    score: float
    bm25_rank: int | None
    vector_rank: int | None


class Index:
    def __init__(self, passages: list[Passage], docs: list[Document], vectors: list[list[float]] | None):
        self.passages = passages
        self.docs = {d.id: d for d in docs}
        self.vectors = vectors
        self.terms = [Counter(tokens(f"{p.section} {p.heading} {p.text}")) for p in passages]
        self.lengths = [sum(t.values()) for t in self.terms]
        self.avg = sum(self.lengths) / max(1, len(self.lengths))
        df: Counter = Counter()
        for t in self.terms:
            df.update(t.keys())
        n = len(passages)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def bm25(self, query: str, k1: float = 1.4, b: float = 0.75) -> list[tuple[int, float]]:
        q = tokens(query)
        scores = []
        for i, tf in enumerate(self.terms):
            s = 0.0
            for term in q:
                f = tf.get(term)
                if f:
                    s += self.idf.get(term, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lengths[i] / self.avg))
            if s:
                scores.append((i, s))
        return sorted(scores, key=lambda x: -x[1])

    def nearest(self, query_vector: list[float]) -> list[tuple[int, float]]:
        if not self.vectors:
            return []
        qn = math.sqrt(sum(v * v for v in query_vector)) or 1.0
        sims = []
        for i, vec in enumerate(self.vectors):
            dot = sum(a * b for a, b in zip(query_vector, vec))
            sims.append((i, dot / qn))
        return sorted(sims, key=lambda x: -x[1])

    REFERENCES = [
        (re.compile(r"section\s+(\d{3})\s+of\s+the\s+charter", re.I), "Charter § {}"),
        (re.compile(r"(?:§|\bsection)\s*(\d{1,2}-\d{2})\b", re.I), "§ {}"),
        (re.compile(r"\bsee\s+(?:FAR\s+)?(\d{1,2}\.\d{3,4})\b", re.I), "FAR {}"),
        (re.compile(r"\b(128\.\d{3})\b"), "13 CFR {}"),
    ]

    def follow_references(self, hits: list[Hit], query: str, query_vector: list[float] | None = None, limit: int = 4) -> list[Hit]:
        """Add the passage a hit points to ("...subdivision (i) of section 311 of the Charter").

        Rules often set a number by reference. Without this step the model sees
        the pointer but not the number. Within the referenced section, a named
        subdivision narrows the candidates, and the closest match to the
        question in meaning (or by keywords, without embeddings) is taken.
        """
        have = {(h.passage.doc, h.passage.section) for h in hits}
        by_section: dict[tuple[str, str], list[int]] = {}
        for i, p in enumerate(self.passages):
            by_section.setdefault((p.doc, p.section), []).append(i)
        keyword = dict(self.bm25(query))
        meaning = dict(self.nearest(query_vector)) if query_vector and self.vectors else {}
        added: list[Hit] = []
        for hit in hits:
            for pattern, label in self.REFERENCES:
                for m in pattern.finditer(hit.passage.text):
                    key = (hit.passage.doc, label.format(m.group(1)))
                    if key in have or key not in by_section:
                        continue
                    candidates = by_section[key]
                    sub = re.search(r"subdivision\s+\(?([a-z])\)?\s+of\s*$", hit.passage.text[max(0, m.start() - 40) : m.start()], re.I)
                    if sub:
                        marked = [i for i in candidates if re.search(rf"(?m)^\s*{sub.group(1)}\.\s", self.passages[i].text)]
                        candidates = marked or candidates
                    best = max(candidates, key=lambda i: (meaning.get(i, 0.0), keyword.get(i, 0.0)))
                    added.append(Hit(self.passages[best], 0.0, None, None))
                    # A numbered list can split across passages ("...may provide by rule that:" / "1. ...").
                    following = best + 1
                    if following < len(self.passages) and (self.passages[following].doc, self.passages[following].section) == key:
                        added.append(Hit(self.passages[following], 0.0, None, None))
                    have.add(key)
                    if len(added) >= limit:
                        return hits + added
        return hits + added

    def definitions(self, query: str) -> list[int]:
        """Passages that define a phrase from the question ("simplified acquisition threshold means")."""
        words = re.findall(r"[a-z0-9-]+", query.lower())
        phrases = {" ".join(words[i : i + n]) for n in (4, 3, 2) for i in range(len(words) - n + 1)}
        phrases = {p for p in phrases if p.split()[0] not in STOP and p.split()[-1] not in STOP}
        found = []
        for i, passage in enumerate(self.passages):
            text = passage.text.lower()
            if " means" in text and any(f"{p} means" in text for p in phrases):
                found.append(i)
        return found[:3]

    def search(self, query: str, query_vector: list[float] | None, k: int = 8, per_doc: int = 4, pool: int = 40) -> list[Hit]:
        """Fuse both rankings; favour documents from the jurisdiction the question names; cap any one document."""
        bm = self.bm25(query)[:pool]
        vec = self.nearest(query_vector)[:pool] if query_vector else []
        fused: dict[int, list] = {}
        for rank, (i, _) in enumerate(bm):
            fused.setdefault(i, [0.0, None, None])
            fused[i][0] += 1 / (60 + rank)
            fused[i][1] = rank + 1
        for rank, (i, _) in enumerate(vec):
            fused.setdefault(i, [0.0, None, None])
            fused[i][0] += 1 / (60 + rank)
            fused[i][2] = rank + 1
        # "What is the X?" is usually answered by a definition: "X means ...".
        for i in self.definitions(query):
            fused.setdefault(i, [0.0, None, None])
            fused[i][0] += 2 / 60
        # Tables of past amendments mention every rule; keep them below the rule in force.
        for i, row in fused.items():
            if is_history(self.passages[i].heading):
                row[0] *= 0.5
        named = jurisdictions(query)
        if named:
            for i, row in fused.items():
                if self.docs[self.passages[i].doc].jurisdiction in named:
                    row[0] *= 1.5
        hits, per = [], Counter()
        for i, (score, br, vr) in sorted(fused.items(), key=lambda x: -x[1][0]):
            doc = self.passages[i].doc
            if per[doc] >= per_doc:
                continue
            per[doc] += 1
            hits.append(Hit(self.passages[i], round(score, 5), br, vr))
            if len(hits) == k:
                break
        return self.follow_references(self.widen(hits), query, query_vector)

    def widen(self, hits: list[Hit]) -> list[Hit]:
        """Give each hit its neighbours in the same section, merging windows that touch.

        Passages are small so search can find them; a rule's subparagraph often
        spans two or three of them, and the model needs the whole of it.
        """
        position = {p.id: i for i, p in enumerate(self.passages)}
        windows: list[list] = []  # [start, end, best hit]
        for hit in hits:
            i = position[hit.passage.id]
            same = lambda j: 0 <= j < len(self.passages) and (self.passages[j].doc, self.passages[j].section) == (hit.passage.doc, hit.passage.section)  # noqa: E731
            start = i - 1 if same(i - 1) else i
            end = i + 1 if same(i + 1) else i
            for w in windows:
                if start <= w[1] + 1 and end >= w[0] - 1 and same(w[0]):
                    w[0], w[1] = min(w[0], start), max(w[1], end)
                    break
            else:
                windows.append([start, end, hit])
        out = []
        for start, end, hit in windows:
            parts = self.passages[start : end + 1]
            first = parts[0]
            merged = Passage(first.id, first.doc, "\n\n".join(p.text for p in parts), first.section, first.page, first.heading)
            out.append(Hit(merged, hit.score, hit.bm25_rank, hit.vector_rank))
        return out


# ---------------------------------------------------------------- building and saving

def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def embed_passages(passages: list[Passage], embed, cache_path: Path, batch: int = 32) -> list[list[float]]:
    """nomic-embed expects task prefixes; vectors are cached by passage text so rebuilds are cheap."""
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [p for p in passages if _key(p.text) not in cache]
    for start in range(0, len(todo), batch):
        group = todo[start : start + batch]
        texts = [f"search_document: {p.section} {p.heading}\n{p.text}".strip() for p in group]
        for p, vec in zip(group, embed(texts)):
            cache[_key(p.text)] = [round(v, 5) for v in vec]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache))
    return [cache[_key(p.text)] for p in passages]


def save(path: Path, passages: list[Passage], vectors: list[list[float]] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passages": [p.to_dict() for p in passages], "vectors": vectors}))


def load(path: Path, docs: list[Document]) -> Index:
    data = json.loads(path.read_text())
    return Index([Passage(**p) for p in data["passages"]], docs, data.get("vectors"))
