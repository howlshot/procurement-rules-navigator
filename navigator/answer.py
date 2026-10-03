"""Answer a question from retrieved passages, then check every quote.

The model sees numbered passages and must quote the one it relies on. A
citation whose quote is not in its passage is dropped; an answer left with no
checked citation is marked unsupported instead of being shown as fact.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from .llm import ModelError, Provider
from .search import Hit, Index, jurisdictions
from .sources import cite, is_history, source_link
from .textmatch import locate

SYSTEM_PROMPT = """You answer questions about public procurement rules for New York State agencies, New York City agencies, CUNY, and the federal government. Use only the numbered passages you are given; never use outside knowledge.

Return JSON matching the schema.

Rules:
1. status: "answered" when the passages answer the question, "partial" when they answer part of it, "not_found" when they do not answer it.
2. answer: two to four plain sentences. Lead with the direct answer (the dollar amount, the yes or no, the rule). Name whose rule it is: New York State, New York City, CUNY, or federal. If the question names one of these, answer for that one.
3. citations: for every fact in the answer, give the passage label (P1, P2...) and a quote of 8 to 40 consecutive words copied exactly from that passage. Never reword anything inside quote.
4. disagreements: when passages from different documents give different numbers or rules for the same situation, or an older document conflicts with a newer one, describe the difference in one sentence and list the passage labels. Say which document is newer when the dates differ. Otherwise return an empty list.
   When documents from the same jurisdiction disagree, the answer itself must use the newer document's figure.
5. Passages marked as history of past amendments describe earlier versions. Answer from the rule in force; mention the history only if the question asks about it.
6. If a passage refers to another provision for the number (for example "the maximum amount authorized in section 311 of the Charter"), look for that provision among the passages and use it.
7. When status is "not_found", say briefly what the passages cover instead, and return no citations."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "partial", "not_found"]},
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"passage": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["passage", "quote"],
                "additionalProperties": False,
            },
        },
        "disagreements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"summary": {"type": "string"}, "passages": {"type": "array", "items": {"type": "string"}}},
                "required": ["summary", "passages"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "answer", "citations", "disagreements"],
    "additionalProperties": False,
}


@dataclass
class Citation:
    label: str
    quote: str
    cite: str
    link: str
    document: str
    doc_id: str
    jurisdiction: str
    as_of: str
    verification: str
    similarity: float


@dataclass
class Answer:
    question: str
    status: str
    answer: str
    citations: list[Citation]
    removed: list[dict]
    disagreements: list[dict]
    retrieved: list[dict]
    jurisdictions: list[str]
    model: str
    seconds: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


LABELS = re.compile(r"\s*\((?:passages?\s+)?P\d+(?:\s*(?:,|and|&)\s*P\d+)*\)|\s*\[P\d+\]", re.I)


def unlabel(text: str) -> str:
    """Passage labels (P3) mean nothing to a reader; the citations carry the sources."""
    return LABELS.sub("", text).strip()


def newest_first(hits: list[Hit], index: Index) -> list[Hit]:
    """Within one jurisdiction, show newer documents before older ones; keep relevance order otherwise."""
    order = {j: [h for h in hits if index.docs[h.passage.doc].jurisdiction == j] for j in dict.fromkeys(index.docs[h.passage.doc].jurisdiction for h in hits)}
    for group in order.values():
        group.sort(key=lambda h: index.docs[h.passage.doc].as_of, reverse=True)
    cursor = {j: iter(g) for j, g in order.items()}
    return [next(cursor[index.docs[h.passage.doc].jurisdiction]) for h in hits]


AMOUNT = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion))?", re.I)


def amounts(text: str) -> set[str]:
    out = set()
    for m in AMOUNT.findall(text):
        value = m.lower().replace("$", "").replace(",", "").strip()
        scale = 1_000_000 if "million" in value else 1_000_000_000 if "billion" in value else 1
        number = float(re.sub(r"[^\d.]", "", value) or 0) * scale
        out.add(f"{number:.0f}")
    return out


def stale_figure(answer: str, citations: list["Citation"]) -> dict | None:
    """The answer's dollar figure comes only from an older document while a newer one, same jurisdiction, says otherwise."""
    said = amounts(answer)
    if not said:
        return None
    for old in citations:
        for new in citations:
            if old.doc_id == new.doc_id or old.jurisdiction != new.jurisdiction or new.as_of <= old.as_of:
                continue
            old_figs, new_figs = amounts(old.quote), amounts(new.quote)
            if said & old_figs and new_figs and not (said & new_figs):
                return {"summary": f"The answer's figure comes from {old.document} (dated {old.as_of}); the newer {new.document} (dated {new.as_of}) gives a different figure. Check the newer document.",
                        "sources": [{"cite": new.cite, "as_of": new.as_of, "link": new.link}, {"cite": old.cite, "as_of": old.as_of, "link": old.link}]}
    return None


def passages_block(hits: list[Hit], index: Index) -> str:
    blocks = []
    for n, hit in enumerate(hits, start=1):
        p = hit.passage
        doc = index.docs[p.doc]
        where = cite(p, doc) + (f" ({p.heading})" if p.heading else "")
        note = " | HISTORY OF PAST AMENDMENTS, not the rule in force" if is_history(p.heading) else ""
        followed = " | cross-referenced by another passage" if hit.score == 0.0 else ""
        blocks.append(f"[P{n}] {doc.jurisdiction} | {where} | document dated {doc.as_of}{note}{followed}\n{p.text}")
    return "\n\n".join(blocks)


def ask(question: str, index: Index, provider: Provider, embed=None, k: int = 8) -> Answer:
    started = time.monotonic()
    query_vector = None
    warnings: list[str] = []
    if embed and index.vectors:
        try:
            query_vector = embed([f"search_query: {question}"])[0]
        except ModelError as err:
            warnings.append(f"Embedding search unavailable, keyword search only: {err}")
    hits = newest_first(index.search(question, query_vector, k=k), index)
    labels = {f"P{n}": hit for n, hit in enumerate(hits, start=1)}
    user = f"Question: {question}\n\nPassages:\n\n{passages_block(hits, index)}"
    try:
        raw = provider.complete_json(SYSTEM_PROMPT, user, ANSWER_SCHEMA, 2000)
    except ModelError as err:
        raw = {"status": "error", "answer": f"The model could not answer: {err}", "citations": [], "disagreements": []}

    citations, removed = [], []
    for c in raw.get("citations", []):
        label = str(c.get("passage", "")).strip().strip("[]")
        hit = labels.get(label)
        quote = str(c.get("quote", "")).strip()
        if not hit:
            removed.append({"label": label, "quote": quote, "reason": "no such passage"})
            continue
        match = locate(quote, {1: hit.passage.text}, 1)
        if match.status == "missing":
            removed.append({"label": label, "quote": quote, "reason": "quote not in the passage"})
            continue
        doc = index.docs[hit.passage.doc]
        citations.append(Citation(label, quote, cite(hit.passage, doc), source_link(hit.passage, doc), doc.short, doc.id, doc.jurisdiction, doc.as_of, match.status, match.similarity))

    status = raw.get("status", "error")
    if status in ("answered", "partial") and not citations:
        status = "unsupported"
        warnings.append("No quote in the answer could be found in the sources, so it is not shown as supported.")

    disagreements = []
    for d in raw.get("disagreements", []):
        found = [labels[x.strip().strip("[]")] for x in d.get("passages", []) if x.strip().strip("[]") in labels]
        if len({h.passage.doc for h in found}) >= 2 or len({h.passage.section for h in found}) >= 2:
            disagreements.append({
                "summary": unlabel(d.get("summary", "")),
                "sources": [{"cite": cite(h.passage, index.docs[h.passage.doc]), "as_of": index.docs[h.passage.doc].as_of, "link": source_link(h.passage, index.docs[h.passage.doc])} for h in found],
            })

    stale = stale_figure(str(raw.get("answer", "")), citations)
    if stale:
        warnings.append(stale["summary"])
        if not disagreements:
            disagreements.append(stale)

    retrieved = [
        {"label": f"P{n}", "cite": cite(h.passage, index.docs[h.passage.doc]), "jurisdiction": index.docs[h.passage.doc].jurisdiction, "score": h.score, "bm25_rank": h.bm25_rank, "vector_rank": h.vector_rank}
        for n, h in enumerate(hits, start=1)
    ]
    return Answer(
        question=question,
        status=status,
        answer=unlabel(str(raw.get("answer", ""))),
        citations=citations,
        removed=removed,
        disagreements=disagreements,
        retrieved=retrieved,
        jurisdictions=sorted(jurisdictions(question)),
        model=provider.model,
        seconds=round(time.monotonic() - started, 1),
        warnings=warnings,
    )
