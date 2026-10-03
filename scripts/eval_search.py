"""Search-only check: does the right passage come back for each question? No chat model needed.

    python3 scripts/eval_search.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from navigator import search  # noqa: E402
from navigator.__main__ import embedder  # noqa: E402
from navigator.sources import load_manifest  # noqa: E402
from scripts.run_eval import covers  # noqa: E402

docs = load_manifest(ROOT)
index = search.load(ROOT / "index/index.json", docs)
embed = embedder(None)
questions = [q for q in json.loads((ROOT / "eval/questions.json").read_text())["questions"] if q["answerable"]]
top1 = anyk = 0
for q in questions:
    hits = index.search(q["question"], embed([f"search_query: {q['question']}"])[0])
    texts = [f"{h.passage.section} {h.passage.text}" for h in hits]
    a, b = covers(texts[0], q["expect"]), any(covers(t, q["expect"]) for t in texts)
    top1 += a
    anyk += b
    if not b:
        print("MISS", q["id"])
print(f"top passage {top1}/{len(questions)}, any of {len(hits)} retrieved {anyk}/{len(questions)}")
