"""Таблиця картки пакета: короткі посилання й примітки до рядків."""

import unittest

from rtube import batch, tools

A = "https://www.youtube.com/watch?v=pn6mZ0Bcugo"
B = "https://www.youtube.com/watch?v=1G01ROKhAAw"
C = "https://www.youtube.com/watch?v=ZNMNKI4xPbY"


class ShortUrlTest(unittest.TestCase):
    def test_watch(self):
        self.assertEqual(tools.short_url(A), "youtu.be/pn6mZ0Bcugo")

    def test_other(self):
        self.assertEqual(tools.short_url("https://example.com/x"), "example.com/x")


class DescribeTest(unittest.TestCase):
    def test_plain_rows_use_title_or_short_url(self):
        rows = batch.describe([(A, A, "590312170"), (B, "Навушники", "")])
        self.assertEqual([r["video"] for r in rows], ["youtu.be/pn6mZ0Bcugo", "Навушники"])
        self.assertEqual([r["kind"] for r in rows], ["", ""])
        self.assertEqual(rows[0]["pid"], "590312170")

    def test_same_video_other_product_is_copy(self):
        rows = batch.describe([(A, A, "1"), (B, B, "2"), (A, A, "3")])
        self.assertEqual(rows[2]["kind"], "copy")
        self.assertIn("рядку 1", rows[2]["note"])

    def test_exact_repeat_is_skipped(self):
        rows = batch.describe([(A, A, "1"), (A, A, "1")])
        self.assertEqual(rows[1]["kind"], "skip")
        self.assertIn("повтор рядка 1", rows[1]["note"])

    def test_already_queued(self):
        rows = batch.describe([(A, A, "1"), (B, B, "2")], busy={(B, "2")})
        self.assertEqual([r["kind"] for r in rows], ["", "skip"])
        self.assertIn("уже в черзі", rows[1]["note"])

    def test_beyond_limit(self):
        rows = batch.describe([(A, A, ""), (B, B, ""), (C, C, "")], limit=2)
        self.assertEqual([r["kind"] for r in rows], ["", "", "over"])
        self.assertIn("Перші 2", rows[2]["note"])


class SkippedLinesTest(unittest.TestCase):
    def test_counts_lines_without_links(self):
        text = "ID товару;Відео\n590312170 https://youtu.be/pn6mZ0Bcugo ;\n\n ; \n580250272\n"
        self.assertEqual(tools.count_lines_without_links(text), 2)


if __name__ == "__main__":
    unittest.main()
