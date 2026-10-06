"""Список завантажень: рядки звіту для товарів з тим самим відео й сума тривалостей."""

import os
import unittest

from rtube import downloader, jobs


def job(pid, state="done", filepath="", duration=60, also_for=(), status="Готово"):
    j = downloader.Job(url="https://www.youtube.com/watch?v=pn6mZ0Bcugo", title="t", out_dir=".",
                       product_id=pid, info={"duration": duration}, also_for=list(also_for))
    j.state, j.filepath, j.status = state, filepath, status
    return j


class ReportItemsTest(unittest.TestCase):
    def test_passengers_get_own_rows_with_owner_file(self):
        owner = job("590312170", filepath=os.path.join("x", "590312170.mp4"),
                    also_for=["610963253", "580250272"])
        items = jobs.report_items([owner])
        self.assertEqual([i["product_id"] for i in items], ["590312170", "610963253", "580250272"])
        self.assertEqual({i["filepath"] for i in items}, {owner.filepath})
        self.assertEqual(items[1]["text"], "Те саме відео, що й у 590312170 — файл 590312170.mp4")

    def test_failed_owner_fails_passengers(self):
        owner = job("590312170", state="error", status="Приватне відео", also_for=["610963253"])
        items = jobs.report_items([owner])
        self.assertEqual(items[1]["state"], "error")
        self.assertEqual(items[1]["text"], "Приватне відео (те саме відео, що й у 590312170)")
        self.assertEqual(items[1]["filepath"], "")


class UniqueSecondsTest(unittest.TestCase):
    def test_same_file_counted_once(self):
        shared = os.path.join("x", "590312170.mp4")
        done = [job("1", filepath=shared, duration=100), job("2", filepath=shared, duration=100),
                job("3", filepath=os.path.join("x", "3.mp4"), duration=30),
                job("4", state="error", duration=500)]
        self.assertEqual(jobs.unique_seconds(done), 130)


class SummaryTest(unittest.TestCase):
    def test_summary_mentions_passengers(self):
        self.assertIn("ID 590312170 + ще 2 з тим самим відео",
                      job("590312170", also_for=["1", "2"]).summary())


if __name__ == "__main__":
    unittest.main()
