"""Source documents in, citable passages out.

Each passage keeps what a citation needs: the document, the section when the
source has numbered sections (FAR 19.1405, 13 CFR 128.200, PPB Rules § 3-08),
and the PDF page otherwise.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path

MAX_CHARS = 800


@dataclass
class Document:
    id: str
    title: str
    short: str
    jurisdiction: str
    publisher: str
    url: str
    as_of: str
    format: str
    file: str


@dataclass
class Passage:
    id: str
    doc: str
    text: str
    section: str = ""
    page: int | None = None
    heading: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def load_manifest(root: Path) -> list[Document]:
    data = json.loads((root / "sources/manifest.json").read_text())
    return [Document(**{k: d[k] for k in Document.__dataclass_fields__}) for d in data["documents"]]


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Paragraph-aligned pieces under the limit; a long paragraph is cut at sentence ends."""
    paras = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    pieces: list[str] = []
    current = ""
    for para in (p for p in paras if p):
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if current and len(current) + len(para) + 2 > limit:
            pieces.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        pieces.append(current)
    return pieces


# ---------------------------------------------------------------- PDF

# NYC rules print "Section 3-08" on its own line with the title in capitals below;
# the Charter appendix prints "§ 311. Procurement Policy Board".
RULE_MARK = re.compile(r"(?m)^[ \t]*Section[ \t]+(\d+-\d+(?:\.\d+)?)[ \t]*\n(?:[ \t]*\n)*[ \t]*([A-Z][A-Z0-9 ,/&'’()\-.]{2,90})[ \t]*$")
CHARTER_MARK = re.compile(r"(?m)^[ \t]*§[ \t]*(\d+(?:-[a-z0-9]+)?)\.[ \t]+([A-Z][^\n]{2,90})$")


APPENDIX_MARK = re.compile(r"(?m)^[ \t]*APPENDIX[ \t]+([A-Z])[ \t]*[-–—][ \t]*([^\n]{3,90})$")
HISTORY = re.compile(r"summary chart|changes since|amendment history|history of", re.I)


def is_history(heading: str) -> bool:
    """Tables of past amendments describe old versions of a rule, not the rule in force."""
    return bool(HISTORY.search(heading))


def section_marks(text: str) -> list[tuple[int, str, str]]:
    marks = [(m.start(), f"Appendix {m.group(1)}", m.group(2).strip().title()) for m in APPENDIX_MARK.finditer(text)]
    marks += [(m.start(), f"§ {m.group(1)}", m.group(2).strip().rstrip(".").title()) for m in RULE_MARK.finditer(text)]
    marks += [(m.start(), f"Charter § {m.group(1)}", m.group(2).strip().rstrip(".")) for m in CHARTER_MARK.finditer(text)]
    return sorted(marks)


def _pdftotext(path: Path, *flags: str) -> list[str]:
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext was not found. Install poppler (macOS: brew install poppler).")
    out = subprocess.run(["pdftotext", *flags, "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    pages = out.split("\f")
    return pages[:-1] if pages and not pages[-1].strip() else pages


def pdf_passages(doc: Document, path: Path) -> list[Passage]:
    """Reading-order text, page by page. Section marks (§ 3-08) carry forward across pages."""
    out: list[Passage] = []
    section, heading = "", ""
    for number, text in enumerate(_pdftotext(path), start=1):
        # A page can open in the middle of one section and start another.
        marks = section_marks(text)
        cuts = [0] + [m[0] for m in marks] + [len(text)]
        for i in range(len(cuts) - 1):
            block = text[cuts[i] : cuts[i + 1]]
            if i > 0:
                _, section, heading = marks[i - 1]
            for piece in split_text(block):
                if len(piece) > 40:
                    out.append(Passage(f"{doc.id}#{len(out) + 1}", doc.id, piece, section, number, heading))
    return out


# ---------------------------------------------------------------- FAR (acquisition.gov HTML)

class _FarParser(HTMLParser):
    """Collects text per <article data-part="section" data-part-number="13.003">."""

    BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "tr", "div", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[list] = []  # [number, title, [text parts]]
        self.stack: list[str | None] = []
        self.in_title = False
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "nav", "header", "footer"):
            self.skip += 1
        if tag == "article":
            number = a.get("data-part-number") if a.get("data-part") == "section" else None
            self.stack.append(number)
            if number:
                self.sections.append([number, "", []])
        if tag in ("h2", "h3") and self.sections and self._current() and not self.sections[-1][1]:
            self.in_title = True
        if tag in self.BLOCKS and self.sections:
            self.sections[-1][2].append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer"):
            self.skip = max(0, self.skip - 1)
        if tag == "article" and self.stack:
            self.stack.pop()
        if tag in ("h2", "h3"):
            self.in_title = False
        if tag in self.BLOCKS and self.sections:
            self.sections[-1][2].append("\n\n" if tag in ("p", "li", "tr") else "\n")

    def handle_data(self, data):
        if self.skip or not self.sections or not self._current():
            return
        if self.in_title:
            self.sections[-1][1] += data
        else:
            self.sections[-1][2].append(data)

    def _current(self) -> bool:
        return any(n for n in self.stack)


def far_passages(doc: Document, path: Path) -> list[Passage]:
    parser = _FarParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    out: list[Passage] = []
    for number, title, parts in parser.sections:
        title = re.sub(r"\s+", " ", title).strip()
        heading = re.sub(rf"^{re.escape(number)}\s*", "", title)
        body = re.sub(r"[ \t]+", " ", "".join(parts))
        body = re.sub(r"\n[ \t]*\n[\s]*", "\n\n", body).strip()
        if not body:
            continue
        for piece in split_text(body):
            out.append(Passage(f"{doc.id}#{len(out) + 1}", doc.id, piece, f"FAR {number}", None, heading))
    return out


# ---------------------------------------------------------------- eCFR XML

def ecfr_passages(doc: Document, path: Path) -> list[Passage]:
    root = ET.parse(path).getroot()
    out: list[Passage] = []
    for div in root.iter("DIV8"):
        head = (div.findtext("HEAD") or "").strip()
        number = re.sub(r"^§\s*", "", div.get("N", "")).strip()
        heading = re.sub(r"^§\s*[\d.]+\s*", "", head)
        paras = ["".join(p.itertext()).strip() for p in div if p.tag in ("P", "FP")]
        body = "\n\n".join(p for p in paras if p)
        for piece in split_text(body):
            out.append(Passage(f"{doc.id}#{len(out) + 1}", doc.id, piece, f"13 CFR {number}", None, heading))
    return out


def load_passages(root: Path, docs: list[Document]) -> list[Passage]:
    readers = {"pdf": pdf_passages, "far-html": far_passages, "ecfr-xml": ecfr_passages}
    passages: list[Passage] = []
    for doc in docs:
        path = root / "sources" / doc.file
        if path.exists():
            passages += readers[doc.format](doc, path)
    return passages


def cite(passage: Passage, doc: Document) -> str:
    """'FAR 19.1405', 'NYC PPB Rules § 3-08, p. 84' or 'NYS Discretionary Purchasing Guidelines, p. 3'."""
    if passage.section.startswith(("FAR ", "13 CFR")):
        return passage.section
    if passage.section.startswith("Charter"):
        return f"NYC {passage.section} (in {doc.short}), p. {passage.page}"
    parts = [doc.short]
    if passage.section:
        parts.append(passage.section)
    label = " ".join(parts)
    return f"{label}, p. {passage.page}" if passage.page else label


def source_link(passage: Passage, doc: Document) -> str:
    if doc.format == "pdf" and passage.page:
        return f"{doc.url}#page={passage.page}"
    if doc.format == "far-html":
        return f"https://www.acquisition.gov/far/{passage.section.split()[-1]}"
    if doc.format == "ecfr-xml":
        return f"https://www.ecfr.gov/current/title-13/section-{passage.section.split()[-1]}"
    return doc.url
