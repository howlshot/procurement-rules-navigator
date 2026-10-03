"""Build the static sample-answer gallery for GitHub Pages into _site/.

    python3 scripts/build_gallery.py

Nothing runs a model here: the page shows the saved answers in answers/ and
the evaluation in eval/results.json, so visitors need no key and no local model.
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"
E = html.escape
ORDER = ["New York State", "New York City", "CUNY", "Federal", "Other"]
LABEL = {"answered": "Answered", "partial": "Partly answered", "not_found": "Not in these sources", "unsupported": "Not supported", "error": "Error"}


def card(a: dict, correct: bool | None) -> str:
    cites = "".join(
        f'<li><a href="{E(c["link"])}">{E(c["cite"])}</a> <span class="meta">· dated {E(c["as_of"])}</span><blockquote>{E(c["quote"])}</blockquote></li>'
        for c in a["citations"]
    )
    warn = "".join(
        f'<div class="warn"><strong>The sources disagree</strong>{E(d["summary"])}<ul>'
        + "".join(f'<li><a href="{E(s["link"])}">{E(s["cite"])}</a> (dated {E(s["as_of"])})</li>' for s in d["sources"])
        + "</ul></div>"
        for d in a["disagreements"]
    )
    mark = "" if correct is None else (' <span class="ok">matches the reference answer</span>' if correct else ' <span class="miss">does not match the reference answer</span>')
    return (
        f'<article class="card" id="{E(a["id"])}"><p class="q">{E(a["question"])}</p>'
        f'<p><span class="status {E(a["status"])}">{E(LABEL.get(a["status"], a["status"]))}</span>{mark}</p>'
        f'<p class="answer">{E(a["answer"])}</p>{warn}'
        + (f'<ul class="cites">{cites}</ul>' if cites else "")
        + "</article>"
    )


def main() -> None:
    results = json.loads((ROOT / "eval/results.json").read_text())
    correct = {s["id"]: s["correct"] for s in results["scores"]}
    answers = [json.loads(p.read_text()) for p in sorted((ROOT / "answers").glob("*.json"))]
    questions = json.loads((ROOT / "eval/questions.json").read_text())["questions"]
    order = {q["id"]: i for i, q in enumerate(questions)}
    answers.sort(key=lambda a: order.get(a["id"], 999))
    sections = []
    for j in ORDER:
        group = [a for a in answers if a.get("jurisdiction_asked") == j]
        if group:
            title = "Out of scope (should say not found)" if j == "Other" else j
            sections.append(f'<h2>{E(title)}</h2><div class="grid">' + "".join(card(a, correct.get(a["id"])) for a in group) + "</div>")
    r = results
    pct = lambda a, b: f"{round(100 * a / b)}%" if b else "n/a"  # noqa: E731
    table = (
        "<table><tbody>"
        f"<tr><td>Answer correct</td><td>{r['correct']}/{r['answerable']} ({pct(r['correct'], r['answerable'])})</td></tr>"
        f"<tr><td>Cites the right document with a checked quote</td><td>{r['right_source']}/{r['answerable']}</td></tr>"
        f"<tr><td>Out-of-scope questions answered “not found”</td><td>{r['not_found_correct']}/{r['not_found_total']}</td></tr>"
        f"<tr><td>Known conflicts between sources flagged</td><td>{r['disagreements_flagged']}/{r['disagreements_expected']}</td></tr>"
        f"<tr><td>Search alone: top passage contains the answer</td><td>{r['search_top1']}/{r['answerable']}</td></tr>"
        "</tbody></table>"
    )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Procurement Rules Navigator samples</title>
<meta name="description" content="Answers about New York State, NYC, CUNY and federal purchasing rules, quoted from the rules, with conflicts between sources flagged.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Next:wght@400;600;700&family=Young+Serif&display=swap">
<style>
:root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:rgba(11,11,11,.1);--accent:#1c5cab;--quote:#f1f0ec;--warn-bg:#fff4dc;--warn-ink:#6b4a00;--ok:#006300;--bad:#a12626}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--line:rgba(255,255,255,.1);--accent:#86b6ef;--quote:#232321;--warn-bg:#3a2e12;--warn-ink:#ffd27a;--ok:#0ca30c;--bad:#ff8a8a}}}}
:root[data-theme=dark]{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--line:rgba(255,255,255,.1);--accent:#86b6ef;--quote:#232321;--warn-bg:#3a2e12;--warn-ink:#ffd27a;--ok:#0ca30c;--bad:#ff8a8a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:400 16px/1.55 "Atkinson Hyperlegible Next",system-ui,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:40px 16px 64px}}h1,h2{{font-family:"Young Serif",Georgia,serif;font-weight:400}}
h1{{font-size:clamp(2rem,5vw,2.8rem);line-height:1.1;margin:0 0 12px}}h2{{margin:36px 0 12px}}.lede{{font-size:1.1rem;max-width:68ch}}.note{{color:var(--ink2);max-width:68ch}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px}}
.q{{font-weight:700;margin:0 0 8px}}.answer{{margin:8px 0}}
.status{{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:2px 9px;border-radius:999px;border:1px solid var(--line);color:var(--ink2)}}
.status.answered{{color:var(--ok)}}.status.not_found{{color:var(--ink2)}}.ok{{color:var(--ok);font-size:.85rem}}.miss{{color:var(--bad);font-size:.85rem}}
.cites{{list-style:none;margin:10px 0 0;padding:0}}.cites li{{border-top:1px solid var(--line);padding-top:8px;margin-top:8px;font-size:.92rem}}.meta{{color:var(--ink2)}}
blockquote{{margin:6px 0 0;padding:8px 12px;background:var(--quote);border-radius:8px;color:var(--ink2)}}
.warn{{margin-top:10px;padding:10px 12px;border-radius:10px;background:var(--warn-bg);color:var(--warn-ink);font-size:.92rem}}.warn strong{{display:block}}.warn ul{{margin:6px 0 0;padding-left:18px}}
a{{color:var(--accent)}}table{{border-collapse:collapse;background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden}}td{{padding:9px 14px;border-top:1px solid var(--line)}}tr:first-child td{{border-top:0}}
footer{{margin-top:40px;color:var(--ink2);font-size:.9rem}}
</style></head><body><main>
<p class="note">Studio Chingie · Procurement Rules Navigator</p>
<h1>Answers quoted from the rules</h1>
<p class="lede">These answers about New York State, New York City, CUNY and federal purchasing rules were written by a model running on a local machine, using only the rule documents. Every quote was checked against its source passage, and conflicts between sources are flagged with the date of each document.</p>
<p class="note">Saved outputs: nothing runs when you open this page. Not legal advice; check the cited rule before relying on it.</p>
<h2>Measured against reference answers</h2>
{table}
{''.join(sections)}
<footer>Source code, questions and method: <a href="https://github.com/howlshot/procurement-rules-navigator">github.com/howlshot/procurement-rules-navigator</a>. Built by <a href="https://studiochingie.com/services">Studio Chingie LLC</a>.</footer>
</main></body></html>"""
    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(page)
    print(f"_site: {len(answers)} answers")


if __name__ == "__main__":
    main()
