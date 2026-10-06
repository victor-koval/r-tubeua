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

    def test_same_video_is_one_group_named_by_first_id(self):
        rows = batch.describe([(A, A, "1"), (B, B, "2"), (A, A, "3"), (A, A, "4")])
        self.assertEqual([r["kind"] for r in rows], ["shared", "", "same", "same"])
        self.assertEqual(rows[0]["note"], "▸ спільне ще для 2: 3, 4")
        self.assertEqual(rows[2]["note"], "↳ те саме відео, що й у 1")
        self.assertEqual([r["group"] for r in rows], [0, None, 0, 0])

    def test_groups_get_their_own_numbers(self):
        rows = batch.describe([(A, A, "1"), (B, B, "2"), (A, A, "3"), (B, B, "4")])
        self.assertEqual([r["group"] for r in rows], [0, 1, 0, 1])

    def test_skipped_rows_are_not_in_group(self):
        rows = batch.describe([(A, A, "1"), (A, A, "1"), (A, A, "3")], limit=2)
        self.assertEqual([r["kind"] for r in rows], ["", "skip", "over"])
        self.assertEqual([r["group"] for r in rows], [None, None, None])

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


class GroupColorTest(unittest.TestCase):
    def test_neighbouring_groups_never_share_a_color(self):
        # Дев'ять груп по два товари; групи 0 і 8 стоять поруч — «по колу»
        # з 8 кольорів вони б збіглися.
        urls = [f"https://www.youtube.com/watch?v=video{i:06d}" for i in range(9)]
        order = list(range(9)) + [8, 0] + list(range(1, 8))
        entries = [(urls[g], urls[g], str(100 + n)) for n, g in enumerate(order)]
        rows = batch.describe(entries)
        for a, b in zip(rows, rows[1:]):
            if a["group"] != b["group"]:
                self.assertNotEqual(a["color"], b["color"], (a, b))
        self.assertEqual(len({r["color"] for r in rows}), 8)

    def test_single_rows_have_no_color(self):
        rows = batch.describe([(A, A, "1"), (B, B, "2")])
        self.assertEqual([r["color"] for r in rows], [None, None])


class PluralTest(unittest.TestCase):
    def test_forms(self):
        forms = [batch.plural(n, "товар", "товари", "товарів") for n in (1, 2, 5, 11, 21, 22, 112)]
        self.assertEqual(forms, ["товар", "товари", "товарів", "товарів", "товар", "товари",
                                 "товарів"])


class SkippedLinesTest(unittest.TestCase):
    def test_counts_lines_without_links(self):
        text = "ID товару;Відео\n590312170 https://youtu.be/pn6mZ0Bcugo ;\n\n ; \n580250272\n"
        self.assertEqual(tools.count_lines_without_links(text), 2)


if __name__ == "__main__":
    unittest.main()
