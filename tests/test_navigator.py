"""Unit tests that need no model and no network: python3 -m unittest discover -s tests -t ."""
import unittest

from navigator.answer import ask
from navigator.llm import parse_json
from navigator.redact import redact
from navigator.search import Index, jurisdictions, tokens
from navigator.sources import Document, Passage, cite, is_history, section_marks, split_text


def doc(id_, jurisdiction="New York City", short=None, fmt="pdf"):
    return Document(id_, id_, short or id_, jurisdiction, "p", "https://example.gov/x.pdf", "2026-06", fmt, f"{id_}.pdf")


RULES = """Section 3-08

SMALL PURCHASES.

(iv) M/WBE Small Purchases. No competition is required for the procurement of goods,
services, and construction from M/WBE vendors. Agencies shall not make any purchase the value of
which exceeds the maximum amount authorized pursuant to paragraph (1) of subdivision (i) of
section 311 of the Charter."""


class Sources(unittest.TestCase):
    def test_section_marks_for_rules_charter_and_appendix(self):
        text = RULES + "\n\nAPPENDIX A- SUMMARY CHART OF ALL PPB CHANGES SINCE APRIL 2010\nrows\n§ 311. Procurement Policy Board\ntext"
        marks = [(m[1], m[2]) for m in section_marks(text)]
        self.assertEqual(marks, [("§ 3-08", "Small Purchases"), ("Appendix A", "Summary Chart Of All Ppb Changes Since April 2010"), ("Charter § 311", "Procurement Policy Board")])
        self.assertTrue(is_history(marks[1][1]))
        self.assertFalse(is_history(marks[0][1]))

    def test_split_text_keeps_paragraphs_under_the_limit(self):
        pieces = split_text("A short one.\n\n" + "Long sentence here. " * 60, limit=300)
        self.assertTrue(all(len(p) <= 300 for p in pieces))
        self.assertEqual(pieces[0].split("\n\n")[0], "A short one.")

    def test_citations_name_the_section_and_page(self):
        d = doc("nyc-ppb-rules", short="NYC PPB Rules")
        self.assertEqual(cite(Passage("a", "nyc-ppb-rules", "t", "§ 3-08", 92), d), "NYC PPB Rules § 3-08, p. 92")
        self.assertEqual(cite(Passage("b", "far", "t", "FAR 19.1405"), doc("far", "Federal")), "FAR 19.1405")


class Search(unittest.TestCase):
    def setUp(self):
        self.docs = [doc("nyc"), doc("state", "New York State")]
        self.passages = [
            Passage("nyc#1", "nyc", RULES, "§ 3-08", 92, "Small Purchases"),
            Passage("nyc#2", "nyc", "i. In addition to other rules authorized by this section, the board may provide by rule that:", "Charter § 311", 182, "Procurement Policy Board"),
            Passage("nyc#3", "nyc", "1. agencies may make procurements for amounts not exceeding one million five hundred thousand dollars from certified minority or women-owned business enterprises", "Charter § 311", 182, "Procurement Policy Board"),
            Passage("nyc#4", "nyc", "9. rules authorizing the submission of a protest by a vendor, except for small purchases", "Charter § 311", 181, "Procurement Policy Board"),
            Passage("state#1", "state", "Higher discretionary buying thresholds apply to certified SDVOBs ($1,500,000).", "", 3),
        ]
        self.index = Index(self.passages, self.docs, None)

    def test_tokens_normalize_amounts_and_plurals(self):
        self.assertIn("1500000", tokens("$1,500,000 limit"))
        self.assertIn("purchase", tokens("small purchases"))

    def test_jurisdictions_named_in_a_question(self):
        self.assertEqual(jurisdictions("What is the NYC micropurchase limit?"), {"New York City"})
        self.assertEqual(jurisdictions("Can CUNY buy from an SDVOB?"), {"CUNY", "New York State"})

    def test_cross_reference_brings_in_the_referenced_subdivision_and_its_next_passage(self):
        hits = self.index.search("NYC M/WBE small purchase limit", None, k=1)
        self.assertEqual(hits[0].passage.id, "nyc#1")
        self.assertEqual([h.passage.id for h in hits[1:]], ["nyc#2", "nyc#3"])


class Windows(unittest.TestCase):
    def test_hits_carry_their_neighbours_in_the_same_section(self):
        docs = [doc("nyc"), doc("far", "Federal")]
        passages = [
            Passage("nyc#1", "nyc", "(iv) M/WBE Small Purchases.", "§ 3-08", 93),
            Passage("nyc#2", "nyc", "No competition is required for M/WBE vendors.", "§ 3-08", 93),
            Passage("nyc#3", "nyc", "Agencies shall not make purchases pursuant to this subparagraph for human services.", "§ 3-08", 93),
            Passage("nyc#4", "nyc", "Section 3-09 begins here with emergency purchases.", "§ 3-09", 94),
            Passage("far#1", "far", "Simplified acquisition threshold means $350,000, except for contracts outside the United States.", "FAR 2.101"),
            Passage("far#2", "far", "Simplified acquisition procedures apply at or below the simplified acquisition threshold.", "FAR 13.003"),
        ]
        index = Index(passages, docs, None)
        hit = index.search("M/WBE vendors no competition", None, k=1)[0]
        self.assertEqual(hit.passage.text.count("\n\n"), 2)
        self.assertIn("human services", hit.passage.text)
        self.assertNotIn("3-09", hit.passage.text)

    def test_definitions_are_pulled_in(self):
        docs = [doc("far", "Federal")]
        passages = [Passage(f"far#{i}", "far", "Simplified acquisition procedures and the simplified acquisition threshold apply here.", "FAR 13.003") for i in range(1, 6)]
        passages.append(Passage("far#6", "far", "Simplified acquisition threshold means $350,000.", "FAR 2.101"))
        index = Index(passages, docs, None)
        self.assertEqual(index.definitions("What is the simplified acquisition threshold?"), [5])


class FakeModel:
    name, model = "fake", "fake-1"

    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, system, user, schema, max_tokens):
        return self.reply


class Answering(unittest.TestCase):
    def setUp(self):
        self.docs = [doc("nyc"), doc("state", "New York State")]
        self.index = Index([
            Passage("nyc#1", "nyc", RULES, "§ 3-08", 92, "Small Purchases"),
            Passage("state#1", "state", "Higher discretionary buying thresholds apply to certified SDVOBs ($1,500,000) and small businesses ($500,000).", "", 3),
        ], self.docs, None)

    def test_quotes_are_checked_and_bad_ones_removed(self):
        reply = {"status": "answered", "answer": "No competition is required.", "disagreements": [], "citations": [
            {"passage": "P1", "quote": "No competition is required for the procurement of goods, services, and construction from M/WBE vendors"},
            {"passage": "P1", "quote": "Agencies may spend up to five million dollars without any quotes at all"},
            {"passage": "P9", "quote": "anything"},
        ]}
        a = ask("NYC M/WBE small purchase competition?", self.index, FakeModel(reply))
        self.assertEqual(len(a.citations), 1)
        self.assertEqual(a.citations[0].verification, "exact")
        self.assertEqual(sorted(r["reason"] for r in a.removed), ["no such passage", "quote not in the passage"])
        self.assertEqual(a.status, "answered")

    def test_an_answer_with_no_checked_quote_is_unsupported(self):
        reply = {"status": "answered", "answer": "The limit is $9 million.", "disagreements": [], "citations": [{"passage": "P1", "quote": "the limit for M/WBE purchases is nine million dollars in all cases"}]}
        a = ask("NYC M/WBE limit?", self.index, FakeModel(reply))
        self.assertEqual(a.status, "unsupported")
        self.assertTrue(a.warnings)

    def test_disagreements_need_two_different_sources(self):
        reply = {"status": "answered", "answer": "x", "citations": [{"passage": "P1", "quote": "No competition is required for the procurement of goods"}],
                 "disagreements": [{"summary": "one passage only", "passages": ["P1"]}, {"summary": "two documents", "passages": ["P1", "P2"]}]}
        a = ask("M/WBE small purchases and SDVOB discretionary buying thresholds", self.index, FakeModel(reply))
        self.assertEqual([d["summary"] for d in a.disagreements], ["two documents"])


class Recency(unittest.TestCase):
    def test_a_figure_only_in_the_older_document_is_flagged(self):
        from navigator.answer import Citation, amounts, stale_figure

        self.assertEqual(amounts("up to $1,500,000 or $1.5 million"), {"1500000"})
        old = Citation("P1", "SDVOBs ($750,000)", "OGS guide, p. 18", "", "OGS guide", "ogs", "New York State", "2024-12", "exact", 1.0)
        new = Citation("P2", "certified SDVOBs ($1,500,000)", "Guidelines, p. 3", "", "Guidelines", "dpg", "New York State", "2025-08", "exact", 1.0)
        self.assertIsNotNone(stale_figure("Agencies can buy up to $750,000.", [old, new]))
        self.assertIsNone(stale_figure("Agencies can buy up to $1,500,000.", [old, new]))


class Labels(unittest.TestCase):
    def test_passage_labels_are_removed_from_reader_text(self):
        from navigator.answer import unlabel

        self.assertEqual(unlabel("The 2025 guidelines (P1) say $1.5M, the 2024 guide (P6) says $750K [P2]."), "The 2025 guidelines say $1.5M, the 2024 guide says $750K.")
        self.assertEqual(unlabel("Both (P1, P3) agree."), "Both agree.")


class Helpers(unittest.TestCase):
    def test_parse_json_and_redaction(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(redact("call 212-555-0100 or email pat.doe@jjay.cuny.edu"), "call [phone] or email [email]")


if __name__ == "__main__":
    unittest.main()
