"""Черга між запусками: збереження, читання, стійкість до пошкодженого файлу."""

import os
import tempfile
import unittest
from unittest import mock

from rtube import downloader, queuestore


class QueueStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "queue.json")
        self.log = mock.patch.object(queuestore, "applog", mock.MagicMock())
        self.log.start()

    def tearDown(self):
        self.log.stop()
        self.tmp.cleanup()

    def test_roundtrip_keeps_choices_and_prefs(self):
        exact = downloader.Job(url="https://www.youtube.com/watch?v=1zElSYng0Xg", title="Ляльки",
                               out_dir=r"C:\v", info={"huge": "info"}, video_key=(1080, 30, "H.264"),
                               audio_lang="uk", audio_label="Українська", container="mkv",
                               keep_original=True, sub_key=("uk", False), subs_mode="file")
        batch = downloader.Job(url="https://www.youtube.com/watch?v=pn6mZ0Bcugo", title="Килимок",
                               out_dir=r"C:\v", sub_key=None, product_id="590312170",
                               prefs={"max_height": 720, "audio": "uk", "subs": "author_uk"})
        queuestore.save([exact, batch], self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertNotIn("huge", f.read())        # info не зберігаємо
        a, b = queuestore.load(self.path)
        self.assertEqual((a.video_key, a.sub_key, a.container, a.keep_original, a.info),
                         ((1080, 30, "H.264"), ("uk", False), "mkv", True, None))
        self.assertEqual((b.video_key, b.audio_lang, b.sub_key, b.prefs["max_height"]),
                         (None, None, None, 720))
        self.assertEqual((a.product_id, b.product_id), ("", "590312170"))
        self.assertNotEqual(a.id, exact.id)            # нові завдання, а не ті самі

    def test_broken_file_is_empty_queue(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{не json")
        self.assertEqual(queuestore.load(self.path), [])

    def test_bad_record_skipped(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('[{"title": "без url"}, {"url": "https://x", "out_dir": "C:/v"}]')
        jobs = queuestore.load(self.path)
        self.assertEqual([j.url for j in jobs], ["https://x"])

    def test_missing_file_and_clear(self):
        self.assertEqual(queuestore.load(self.path), [])
        queuestore.save([downloader.Job(url="https://x", title="x", out_dir=".")], self.path)
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        queuestore.clear(self.path)
        self.assertFalse(os.path.exists(self.path))


class AlsoForTest(unittest.TestCase):
    def test_passengers_survive_restart(self):
        job = downloader.Job(url="u", title="t", out_dir=".", product_id="1",
                             also_for=["2", "3"])
        restored = queuestore.dict_to_job(queuestore.job_to_dict(job))
        self.assertEqual(restored.also_for, ["2", "3"])
        self.assertEqual(queuestore.dict_to_job({"url": "u", "out_dir": "."}).also_for, [])


class DoneStoreTest(unittest.TestCase):
    """Що вже скачано — між запусками, але лише для незмінених файлів."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "done.json")
        self.video = os.path.join(self.tmp.name, "590312170.mp4")
        with open(self.video, "wb") as f:
            f.write(b"video")
        job = downloader.Job(url="https://www.youtube.com/watch?v=pn6mZ0Bcugo", title="t",
                             out_dir=self.tmp.name, video_key=(1080, 30, "avc1"),
                             audio_lang="uk", sub_key=("uk", False))
        self.key = downloader.same_video_key(job)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        queuestore.save_done({self.key: self.video}, self.path)
        self.assertEqual(queuestore.load_done(self.path), {self.key: self.video})

    def test_changed_or_missing_file_dropped(self):
        queuestore.save_done({self.key: self.video}, self.path)
        with open(self.video, "ab") as f:
            f.write(b" replaced")
        self.assertEqual(queuestore.load_done(self.path), {})
        os.remove(self.video)
        self.assertEqual(queuestore.load_done(self.path), {})

    def test_missing_or_broken_store(self):
        self.assertEqual(queuestore.load_done(self.path), {})
        with open(self.path, "w") as f:
            f.write("{oops")
        with mock.patch.object(queuestore, "applog", mock.MagicMock()):
            self.assertEqual(queuestore.load_done(self.path), {})

if __name__ == "__main__":
    unittest.main()
