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

    def test_shared_video_group(self):
        video = os.path.join(self.tmp.name, "590312170.mp4")
        with open(video, "wb") as f:
            f.write(b"\0" * 1024 * 1024)
        same = "Те саме відео, що й у 590312170 — файл 590312170.mp4"
        items = [
            {"product_id": "590312170", "url": "u", "title": "t", "filepath": video,
             "state": "done", "text": "Готово", "duration": 60},
            {"product_id": "610963253", "url": "u", "title": "t", "filepath": video,
             "state": "done", "text": same, "duration": 60},
            {"product_id": "580250272", "url": "v", "title": "t", "filepath": "",
             "state": "error", "text": "Приватне відео", "duration": None},
        ]
        path = report.write_report(os.path.join(self.tmp.name, "zvit.xlsx"), items)
        ws = load_workbook(path).active
        owner, other, failed = ([c.value for c in ws[r]] for r in (2, 3, 4))
        self.assertIn("спільне відео ще для: 610963253", owner[6])
        self.assertEqual(other[4], "590312170.mp4")
        self.assertEqual(other[5], "Те саме відео")
        self.assertEqual(other[6], same)
        self.assertEqual(owner[8], 1.0)
        self.assertIsNone(other[8])         # окремого файлу немає — і розміру теж
        fill = lambda r: ws.cell(r, 1).fill.fgColor.rgb
        self.assertEqual(fill(2), fill(3))
        self.assertNotEqual(fill(2), fill(4))

    def test_total_counts_shared_file_once(self):
        video = os.path.join(self.tmp.name, "590312170.mp4")
        open(video, "wb").close()
        items = [
            {"product_id": "590312170", "url": "u", "title": "t", "filepath": video,
             "state": "done", "text": "Готово", "duration": 100},
            {"product_id": "610963253", "url": "u", "title": "t", "filepath": video,
             "state": "done", "text": "Те саме відео, що й у 590312170 — файл 590312170.mp4",
             "duration": 100},
        ]
        ws = load_workbook(report.write_report(os.path.join(self.tmp.name, "z.xlsx"), items)).active
        self.assertEqual(ws.cell(ws.max_row, 8).value, "1,40")

    def test_total_is_sum_of_shown_rows(self):
        # Як у реальному звіті: рядки показують заокруглене (11,66 → «0,12»),
        # і «Разом» має бути сумою саме цих чисел — 1,40, а не 1,39.
        durations = [11.66, 13.6, 13.4, 11.9, 11.5, 14.7, 11.7, 11.0]
        items = [{"product_id": str(n), "url": f"u{n}", "title": "t",
                  "filepath": os.path.join(self.tmp.name, f"{n}.mp4"), "state": "done",
                  "text": "Готово", "duration": d} for n, d in enumerate(durations)]
        ws = load_workbook(report.write_report(os.path.join(self.tmp.name, "r.xlsx"), items)).active
        shown = sum(int(m) * 60 + int(s) for m, s in
                    (ws.cell(r, 8).value.split(",") for r in range(2, len(items) + 2)))
        total = ws.cell(ws.max_row, 8).value
        self.assertEqual(total, f"{shown // 60},{shown % 60:02d}")
        # Точна сума — 99,46 с («1,39»); показані рядки дають більше.
        self.assertNotEqual(total, "1,39")

    def test_default_name_is_latin(self):
        name = report.default_name(0)
        self.assertRegex(name, r"^zvit_\d{4}-\d\d-\d\d_\d{4}\.xlsx$")


if __name__ == "__main__":
    unittest.main()
