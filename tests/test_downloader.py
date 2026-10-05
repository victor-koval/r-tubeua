"""Чисті функції завантажувача на справжньому info ролика з ШІ-дубляжем."""

import json
import os
import unittest
from unittest import mock

from rtube import downloader, formats

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "fixture_dubbed.json"), encoding="utf-8") as f:
    INFO = json.load(f)


def pairs(args):
    return list(zip(args[::2], args[1::2]))


class TrackMetadataTest(unittest.TestCase):
    def test_chosen_first_original_second(self):
        args = pairs(downloader.track_metadata_args(INFO, "137+140-19+140-20"))
        self.assertIn(("-metadata:s:a:0", "language=ukr"), args)
        self.assertIn(("-metadata:s:a:1", "language=eng"), args)
        self.assertIn(("-disposition:a:0", "default"), args)
        self.assertIn(("-disposition:a:1", "0"), args)
        titles = [v for k, v in args if k == "-metadata:s:a:0" and v.startswith("handler_name=")]
        self.assertEqual(titles, ["handler_name=Українська — ШІ-дубляж YouTube"])

    def test_video_stream_not_labelled(self):
        args = downloader.track_metadata_args(INFO, "137+140-19")
        self.assertFalse(any(a.startswith("-metadata:s:a:1") for a in args))

    def test_three_letter_code_kept(self):
        info = {"formats": [{"format_id": "a", "vcodec": "none", "acodec": "opus",
                             "language": "fil", "format_note": "Filipino"}]}
        self.assertIn("language=fil", downloader.track_metadata_args(info, "a"))


class PrefsTest(unittest.TestCase):
    """Завдання з пакета: якість і доріжка визначаються після аналізу."""

    def job(self, **prefs):
        return downloader.Job(url="u", title="t", out_dir=".", sub_key=None, prefs=prefs)

    def resolve(self, job, container="mp4"):
        downloader.apply_prefs(job, INFO)
        return formats.resolve_format(INFO, job.video_key, job.audio_lang, container,
                                      job.keep_original)

    def test_ukrainian_by_default(self):
        job = self.job(max_height=1080, audio="uk", subs="author_uk")
        self.assertEqual(self.resolve(job), "137+140-19")
        self.assertEqual(job.sub_key, ())      # є українська доріжка — субтитри не потрібні

    def test_original(self):
        job = self.job(max_height=1080, audio="orig")
        self.resolve(job)
        self.assertEqual(job.audio_lang, "en-US")

    def test_height_limit_and_best(self):
        self.assertEqual(self.resolve(self.job(max_height=720)).split("+")[0], "136")
        job = self.job(max_height=0)
        self.resolve(job)
        self.assertEqual(job.video_key[0], 1080)    # найвища в цьому ролику

    def test_audio_only(self):
        job = self.job(max_height=formats.AUDIO_ONLY)
        self.assertEqual(self.resolve(job), "140-19")
        self.assertTrue(job.audio_only)

    def test_summary_before_analysis(self):
        self.assertIn("до 720p", self.job(max_height=720).summary())


class ChildProcessTest(unittest.TestCase):
    def setUp(self):
        # Не писати «Скасування «t»…» у справжній лог програми.
        self.log = mock.patch.object(downloader, "applog", mock.MagicMock())
        self.log.start()

    def tearDown(self):
        self.log.stop()

    def test_cancel_kills_worker_children(self):
        manager = downloader.DownloadManager.__new__(downloader.DownloadManager)
        manager.events = __import__("queue").Queue()
        killed = []

        class FakeProc:
            def poll(self):
                return None

            def kill(self):
                killed.append(self)

        proc = FakeProc()
        ident = 424242
        downloader._children[ident] = {proc}
        try:
            job = downloader.Job(url="u", title="t", out_dir=".")
            job.state = "running"
            manager._running, manager._worker_ident = job, ident
            manager.cancel(job)
            self.assertEqual(killed, [proc])
            self.assertTrue(job.cancel_event.is_set())
        finally:
            downloader._children.pop(ident, None)

    def test_popen_is_tracked(self):
        import yt_dlp.utils
        self.assertTrue(getattr(yt_dlp.utils.Popen, "_rtube_tracked", False))


class IdTargetTest(unittest.TestCase):
    """Ім'я файлу з ID товару: 590312170.mp4, _2, «Уже є», перезапис."""

    URL = "https://www.youtube.com/watch?v=pn6mZ0Bcugo"
    OTHER = "https://www.youtube.com/watch?v=1G01ROKhAAw"

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.files = {}         # ім'я → (коментар, висота)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, comment, height=720):
        path = os.path.join(self.tmp.name, name)
        open(path, "wb").close()
        self.files[path] = (comment, height)

    def pick(self, url=URL, height=720):
        path, action = downloader.pick_id_target(
            self.tmp.name, "590312170", "mp4", url, height,
            comment_of=lambda p: self.files[p][0], height_of=lambda p: self.files[p][1])
        return os.path.basename(path), action

    def test_free(self):
        self.assertEqual(self.pick(), ("590312170.mp4", "new"))

    def test_same_video_same_quality_skips(self):
        self.put("590312170.mp4", self.URL)
        self.assertEqual(self.pick(), ("590312170.mp4", "skip"))

    def test_same_video_other_quality_overwrites(self):
        self.put("590312170.mp4", self.URL, height=1080)
        self.assertEqual(self.pick(height=720), ("590312170.mp4", "overwrite"))

    def test_other_video_gets_suffix(self):
        self.put("590312170.mp4", self.OTHER)
        self.assertEqual(self.pick(), ("590312170_2.mp4", "new"))
        self.put("590312170_2.mp4", None)          # старий файл без коментаря — «інший»
        self.assertEqual(self.pick(), ("590312170_3.mp4", "new"))

    def test_same_video_found_under_suffix(self):
        self.put("590312170.mp4", self.OTHER)
        self.put("590312170_2.mp4", self.URL)
        self.assertEqual(self.pick(), ("590312170_2.mp4", "skip"))

    def test_source_url_written_to_comment(self):
        args = downloader.track_metadata_args(INFO, "137+140-19", source_url=self.URL)
        self.assertEqual(args[:2], ["-metadata", f"comment={self.URL}"])
        self.assertNotIn("comment=", " ".join(downloader.track_metadata_args(INFO, "137+140-19")))

    def test_name_without_id_is_translit(self):
        job = downloader.Job(url=self.URL, title="t", out_dir=".", video_key=(1080, 30, "H.264"),
                             audio_lang="uk")
        runner = downloader._Runner(job, lambda *a: None)
        self.assertEqual(runner._default_name(INFO), "mirror_mi_fashion_dolls_smyths_toys_uk")
        self.assertEqual(runner._default_name(INFO, quality_in_name=True),
                         "mirror_mi_fashion_dolls_smyths_toys_uk_1080p")

    def test_summary_shows_id(self):
        job = downloader.Job(url=self.URL, title="t", out_dir=".", product_id="590312170")
        self.assertTrue(job.summary().startswith("ID 590312170"))
        self.assertEqual(job.clone().product_id, "590312170")


class DiskTest(unittest.TestCase):
    def test_needed_bytes(self):
        self.assertEqual(downloader.needed_bytes([1000, 0]), 2100)
        self.assertEqual(downloader.needed_bytes([]), 0)


if __name__ == "__main__":
    unittest.main()
