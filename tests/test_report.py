"""Звіт xlsx по завантаженнях."""

import os
import tempfile
import unittest

from openpyxl import load_workbook

from rtube import downloader, report


class StatusLabelTest(unittest.TestCase):
    def test_done_variants(self):
        self.assertEqual(report.status_label("done", "Готово"), "Готово")
        self.assertEqual(report.status_label("done", "Готово — копія 1.mp4 (те саме відео)"), "Копія")
        self.assertEqual(report.status_label("done", downloader.ALREADY_NOTE), "Уже було")
        self.assertEqual(report.status_label("done", "Готово, але без субтитрів (YouTube їх не віддав)"),
                         "Без субтитрів")

    def test_other_states(self):
        self.assertEqual(report.status_label("error", "Приватне відео"), "Помилка")
        self.assertEqual(report.status_label("cancelled", "Скасовано"), "Скасовано")
        self.assertEqual(report.status_label("queued", "У черзі"), "У черзі")


class WriteReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_and_total(self):
        video = os.path.join(self.tmp.name, "590312170.mp4")
        with open(video, "wb") as f:
            f.write(b"\0" * (3 * 1024 * 1024))
        items = [
            {"product_id": "590312170", "url": "https://www.youtube.com/watch?v=pn6mZ0Bcugo",
             "title": "Килимок", "filepath": video, "state": "done", "text": "Готово",
             "duration": 207},
            {"product_id": "580250272", "url": "https://www.youtube.com/watch?v=1G01ROKhAAw",
             "title": "Навушники", "filepath": "", "state": "error", "text": "Приватне відео",
             "duration": None},
        ]
        path = report.write_report(os.path.join(self.tmp.name, "zvit.xlsx"), items)
        ws = load_workbook(path).active
        self.assertEqual([c.value for c in ws[1]], list(report.HEADERS))
        first = [c.value for c in ws[2]]
        self.assertEqual(first[1], "590312170")
        self.assertEqual(first[4], "590312170.mp4")
        self.assertEqual(first[5], "Готово")
        self.assertEqual(first[7], "3,27")
        self.assertEqual(first[8], 3.0)
        second = [c.value for c in ws[3]]
        self.assertEqual(second[5], "Помилка")
        self.assertEqual(second[6], "Приватне відео")
        self.assertIsNone(second[8])
        # Разом — лише завантажені.
        self.assertEqual(ws.cell(ws.max_row, 8).value, "3,27")

    def test_default_name_is_latin(self):
        name = report.default_name(0)
        self.assertRegex(name, r"^zvit_\d{4}-\d\d-\d\d_\d{4}\.xlsx$")


if __name__ == "__main__":
    unittest.main()
