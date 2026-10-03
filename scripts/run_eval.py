"""Ask every question in eval/questions.json, score the answers, save them.

    python3 scripts/run_eval.py [--parallel 4] [--only nyc-micro]

Writes answers/<id>.json (redacted, used by the sample gallery),
eval/results.json and eval/RESULTS.md. Model replies are cached in runs/cache.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from navigator import search  # noqa: E402
from navigator.__main__ import embedder  # noqa: E402
from navigator.answer import ask  # noqa: E402
from navigator.llm import Cached, make_provider  # noqa: E402
from navigator.redact import redact  # noqa: E402
from navigator.sources import cite, load_manifest  # noqa: E402


def norm(text: str) -> str:
    return " ".join(text.lower().replace(",", "").split())


def covers(text: str, groups: list[list[str]]) -> bool:
    t = norm(text)
    return all(any(norm(alt) in t for alt in group) for group in groups)


def score(q: dict, a: dict, hits_text: list[str]) -> dict:
    s = {"id": q["id"], "jurisdiction": q["jurisdiction"], "question": q["question"], "status": a["status"]}
    if q["answerable"]:
        s["correct"] = a["status"] in ("answered", "partial") and covers(a["answer"], q["expect"])
        s["right_source"] = any(c["doc_id"] in q["sources"] for c in a["citations"])
        s["search_top1"] = bool(hits_text) and covers(hits_text[0], q["expect"])
        s["search_any"] = any(covers(t, q["expect"]) for t in hits_text)
    else:
        s["correct"] = a["status"] == "not_found"
        s["right_source"] = None
        s["search_top1"] = s["search_any"] = None
    if q["disagreement"] is True:
        s["flagged_disagreement"] = bool(a["disagreements"])
    s["false_disagreement"] = q["disagreement"] is False and bool(a["disagreements"])
    s["citations"] = len(a["citations"])
    s["removed"] = len(a["removed"])
    return s


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--provider", default="local")
    parser.add_argument("--model")
    args = parser.parse_args()

    docs = load_manifest(ROOT)
    index = search.load(ROOT / "index/index.json", docs)
    provider = Cached(make_provider(args.provider, args.model, None), ROOT / "runs/cache")
    embed = embedder(None)
    questions = json.loads((ROOT / "eval/questions.json").read_text())["questions"]
    if args.only:
        questions = [q for q in questions if q["id"] in args.only]

    def run(q: dict) -> tuple[dict, dict, list[str]]:
        answer = ask(q["question"], index, provider, embed).to_dict()
        vector = embed([f"search_query: {q['question']}"])[0]
        hits = index.search(q["question"], vector)
        return q, answer, [f"{h.passage.section} {h.passage.text}" for h in hits]

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        runs = list(pool.map(run, questions))

    out_dir = ROOT / "answers"
    out_dir.mkdir(exist_ok=True)
    scores = []
    for q, a, hits_text in runs:
        scores.append(score(q, a, hits_text))
        published = json.loads(json.dumps(a))
        published["answer"] = redact(published["answer"])
        for c in published["citations"]:
            c["quote"] = redact(c["quote"])
        for r in published["removed"]:
            r["quote"] = redact(r["quote"])
        published["id"] = q["id"]
        published["jurisdiction_asked"] = q["jurisdiction"]
        (out_dir / f"{q['id']}.json").write_text(json.dumps(published, indent=2) + "\n")
        print(f"{'ok ' if scores[-1]['correct'] else 'MISS'} {q['id']:24} [{a['status']}] {a['answer'][:110]}")

    if args.only:
        return
    answerable = [s for s in scores if s["right_source"] is not None]
    out = {
        "model": provider.model,
        "questions": len(scores),
        "answerable": len(answerable),
        "correct": sum(s["correct"] for s in answerable),
        "right_source": sum(s["right_source"] for s in answerable),
        "search_top1": sum(s["search_top1"] for s in answerable),
        "search_any": sum(s["search_any"] for s in answerable),
        "not_found_correct": sum(s["correct"] for s in scores if s["right_source"] is None),
        "not_found_total": sum(1 for s in scores if s["right_source"] is None),
        "disagreements_flagged": sum(s.get("flagged_disagreement", False) for s in scores),
        "disagreements_expected": sum(1 for s in scores if "flagged_disagreement" in s),
        "false_disagreements": sum(s["false_disagreement"] for s in scores),
        "citations_kept": sum(s["citations"] for s in scores),
        "citations_removed": sum(s["removed"] for s in scores),
        "scores": scores,
    }
    (ROOT / "eval/results.json").write_text(json.dumps(out, indent=2) + "\n")
    (ROOT / "eval/RESULTS.md").write_text(markdown(out))
    print(markdown(out))


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({round(100 * a / b) if b else 0}%)"


def markdown(o: dict) -> str:
    lines = [
        "# Evaluation results",
        "",
        f"Model: `{o['model']}`, run locally. {o['questions']} questions in `eval/questions.json`: {o['answerable']} answerable from the sources and {o['not_found_total']} that are not.",
        "",
        "| Measure | Result |",
        "|---|---:|",
        f"| Answer correct (expected figure or rule in the answer) | {pct(o['correct'], o['answerable'])} |",
        f"| Cites the right document, with a checked quote | {pct(o['right_source'], o['answerable'])} |",
        f"| Out-of-scope questions answered \"not found\" | {pct(o['not_found_correct'], o['not_found_total'])} |",
        f"| Known conflicts between sources flagged | {pct(o['disagreements_flagged'], o['disagreements_expected'])} |",
        f"| Conflicts flagged where the reference expects none | {o['false_disagreements']} |",
        f"| Search alone: top passage contains the answer | {pct(o['search_top1'], o['answerable'])} |",
        f"| Search alone: any retrieved passage contains the answer | {pct(o['search_any'], o['answerable'])} |",
        "",
        f"Quotes: {o['citations_kept']} citations kept after checking, {o['citations_removed']} removed because the quote was not in the passage.",
        "",
        "| Question | Jurisdiction | Status | Correct | Right source |",
        "|---|---|---|:-:|:-:|",
    ]
    mark = {True: "yes", False: "no", None: "n/a"}
    for s in o["scores"]:
        lines.append(f"| {s['question']} | {s['jurisdiction']} | {s['status']} | {mark[s['correct']]} | {mark[s['right_source']]} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
