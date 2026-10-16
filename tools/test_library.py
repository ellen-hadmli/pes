"""Small behavioral checks for scoped search and bounded page output."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library  # noqa: E402


class LibraryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="library-test-", dir=Path(__file__).resolve().parents[1] / "Library"
        )
        self.root = Path(self.temp.name)
        (self.root / "text").mkdir()
        self.alpha = self.root / "text/alpha.md"
        self.beta = self.root / "text/beta.md"
        self.alpha.write_text(
            "# Alpha\n\n## PDF page 1\n\nalpha needle once\n\n"
            "## PDF page 2\n\nalpha needle twice\n\n"
            "## PDF page 3\n\n" + "x" * 500 + "\n\n"
            "## PDF page 4\n\nfinal page\n",
            encoding="utf-8",
        )
        self.beta.write_text(
            "# Beta\n\n## PDF page 1\n\nbeta needle\n", encoding="utf-8"
        )
        self.index = {
            "books": [
                {"id": "alpha", "title": "Alpha book", "aliases": ["A"],
                 "markdown": "text/alpha.md", "pdf": "books/alpha.pdf",
                 "extraction": "text-layer", "quality": "good"},
                {"id": "beta", "title": "Beta book", "aliases": ["B"],
                 "markdown": "text/beta.md", "pdf": "books/beta.pdf",
                 "extraction": "OCR", "quality": "low"},
            ]
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_book_scope_never_reads_other_book(self) -> None:
        hits = library.search(self.index, "needle", book_key="alpha", all_books=False,
                              max_results=10, context_chars=120, root=self.root)
        self.assertEqual(len(hits), 2)
        self.assertEqual({h["book_id"] for h in hits}, {"alpha"})
        self.assertEqual({h["page_pdf"] for h in hits}, {1, 2})

    def test_search_requires_explicit_book_or_all(self) -> None:
        with self.assertRaisesRegex(ValueError, "--book ID или --all"):
            library.search(self.index, "needle", book_key=None, all_books=False,
                           max_results=10, context_chars=120, root=self.root)

    def test_all_search_is_explicit_and_result_count_is_bounded(self) -> None:
        hits = library.search(self.index, "needle", book_key=None, all_books=True,
                              max_results=1000, context_chars=1000, root=self.root)
        self.assertEqual(len(hits), 3)
        self.assertEqual({h["book_id"] for h in hits}, {"alpha", "beta"})
        self.assertTrue(all(len(h["excerpt"]) <= 720 for h in hits))

    def test_search_hard_caps_result_count_and_context(self) -> None:
        self.alpha.write_text(
            "# Alpha\n\n## PDF page 1\n\n" + ("needle " * 80),
            encoding="utf-8",
        )
        hits = library.search(self.index, "needle", book_key="alpha", all_books=False,
                              max_results=1000, context_chars=10000, root=self.root)
        self.assertEqual(len(hits), library.MAX_RESULTS_HARD)
        self.assertTrue(all(len(h["excerpt"]) <= 620 for h in hits))

    def test_show_caps_pages_and_keeps_pdf_citation(self) -> None:
        pages = library.show_pages(self.index, "A", page=2, pages_count=99,
                                   max_chars=8000, root=self.root)
        self.assertEqual([p["page_pdf"] for p in pages], [2, 3, 4])
        self.assertTrue(all(p["source"] == "books/alpha.pdf" for p in pages))

    def test_show_rejects_missing_page_and_bounds_page_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "отсутствует"):
            library.show_pages(self.index, "alpha", page=5, root=self.root)
        pages = library.show_pages(self.index, "alpha", page=3, max_chars=20,
                                   root=self.root)
        self.assertEqual(len(pages[0]["text"]), 100)  # minimum output cap
        self.assertTrue(pages[0]["truncated"])

    def test_non_pdf_citation_does_not_invent_page_number(self) -> None:
        docx = {"books": [{"id": "doc", "title": "DOCX", "aliases": [],
                           "markdown": "text/doc.md", "pdf": None,
                           "extraction": "docx", "quality": "good"}]}
        (self.root / "text/doc.md").write_text(
            "# DOCX\n\n## DOCX extracted text\n\nbody\n", encoding="utf-8"
        )
        result = library.show_pages(docx, "doc", page=1, root=self.root)
        self.assertIsNone(result[0]["page_pdf"])
        self.assertEqual(result[0]["note"], "DOCX: страница не указана")


if __name__ == "__main__":
    unittest.main(verbosity=2)
