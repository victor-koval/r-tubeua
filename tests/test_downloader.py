"""Чисті функції завантажувача на справжньому info ролика з ШІ-дубляжем."""

import json
import os
import queue
import time
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


class SharedVideoTest(unittest.TestCase):
    """Один ролик для кількох товарів: качається раз, файл — за першим ID, решта
    товарів на нього посилається (без копій)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.other_dir = tempfile.TemporaryDirectory()
        self.log = mock.patch.object(downloader, "applog", mock.MagicMock())
        self.log.start()
        self.first = os.path.join(self.tmp.name, "610963253.mp4")
        with open(self.first, "wb") as f:
            f.write(b"video")
        with open(os.path.join(self.tmp.name, "610963253.uk.srt"), "w") as f:
            f.write("1")
        self.done = {downloader.same_video_key(self.job("610963253")): self.first}

    def tearDown(self):
        self.log.stop()
        self.tmp.cleanup()
        self.other_dir.cleanup()

    def job(self, pid, out_dir=None):
        return downloader.Job(url="https://www.youtube.com/watch?v=pn6mZ0Bcugo", title="t",
                              out_dir=out_dir or self.tmp.name, video_key=(720, 30, "H.264"),
                              audio_lang="en", product_id=pid)

    def test_same_folder_refers_to_first_file(self):
        second = self.job("610963232")
        note = downloader._Runner(second, lambda *a: None, self.done)._use_sibling()
        self.assertEqual(note, "Те саме відео, що й у 610963253 — файл 610963253.mp4")
        self.assertEqual(second.filepath, self.first)
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["610963253.mp4", "610963253.uk.srt"])

    def test_other_folder_gets_its_own_copy(self):
        second = self.job("610963232", out_dir=self.other_dir.name)
        target = os.path.join(self.other_dir.name, "610963232.mp4")
        with mock.patch.object(downloader, "pick_id_target", return_value=(target, "new")):
            note = downloader._Runner(second, lambda *a: None, self.done)._use_sibling()
        self.assertIn("копія 610963253.mp4", note)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"video")
        self.assertTrue(os.path.isfile(os.path.join(self.other_dir.name, "610963232.uk.srt")))

    def test_other_quality_is_downloaded(self):
        other = self.job("610963232")
        other.video_key = (1080, 30, "H.264")
        self.assertIsNone(downloader._Runner(other, lambda *a: None, self.done)._use_sibling())

    def test_same_product_again_is_already_have(self):
        runner = downloader._Runner(self.job("610963253"), lambda *a: None, self.done)
        with self.assertRaises(downloader.AlreadyHave):
            runner._use_sibling()


class DiskTest(unittest.TestCase):
    def test_needed_bytes(self):
        self.assertEqual(downloader.needed_bytes([1000, 0]), 2100)
        self.assertEqual(downloader.needed_bytes([]), 0)


class QueueTest(unittest.TestCase):
    """Черга: авто-повтор при обмеженні YouTube і пауза — з підміненим _Runner."""

    def setUp(self):
        self.patches = [mock.patch.object(downloader, "applog", mock.MagicMock()),
                        mock.patch.object(downloader, "RATE_LIMIT_WAITS", (0.05, 0.05, 0.05))]
        for p in self.patches:
            p.start()
        self.manager = downloader.DownloadManager()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def use_runner(self, run):
        class FakeRunner:
            def __init__(self, job, *args, **kwargs):
                self.job = job

            def run(self):
                return run(self.job)

        patch = mock.patch.object(downloader, "_Runner", FakeRunner)
        patch.start()
        self.addCleanup(patch.stop)

    def events_until(self, job, states, timeout=5):
        """Події завдання, доки воно не перейде в один зі станів states."""
        seen = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                kind, job_id, payload = self.manager.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if job_id != job.id:
                continue
            seen.append((kind, payload))
            if kind == "state" and payload[0] in states:
                return seen
        self.fail(f"не дочекались {states}: {seen}")

    def test_rate_limit_detected(self):
        self.assertTrue(downloader.is_rate_limited(
            downloader.DownloadError("HTTP Error 429: Too Many Requests")))
        self.assertTrue(downloader.is_rate_limited(
            Exception("Sign in to confirm you're not a bot")))
        self.assertFalse(downloader.is_rate_limited(Exception("Private video")))

    def test_retries_after_rate_limit(self):
        calls = []

        def run(job):
            calls.append(1)
            if len(calls) < 3:
                raise downloader.DownloadError("HTTP Error 429: Too Many Requests")
            return "Готово"

        self.use_runner(run)
        job = downloader.Job(url="u", title="t", out_dir=".")
        self.manager.submit(job)
        seen = self.events_until(job, ("done", "error"))
        self.assertEqual(seen[-1][1][0], "done")
        self.assertEqual(len(calls), 3)
        self.assertTrue(any(k == "progress" and "обмежив" in p[1] for k, p in seen))

    def test_gives_up_after_all_waits(self):
        self.use_runner(lambda job: (_ for _ in ()).throw(
            downloader.DownloadError("HTTP Error 429")))
        job = downloader.Job(url="u", title="t", out_dir=".")
        self.manager.submit(job)
        self.assertEqual(self.events_until(job, ("done", "error"))[-1][1][0], "error")

    def test_cancel_during_rate_limit_wait(self):
        downloader.RATE_LIMIT_WAITS = (30,)
        self.use_runner(lambda job: (_ for _ in ()).throw(
            downloader.DownloadError("HTTP Error 429")))
        job = downloader.Job(url="u", title="t", out_dir=".")
        self.manager.submit(job)
        self.events_until(job, ("running",))
        # Перший відлік — ролик чекає; скасування має спрацювати одразу.
        while True:
            kind, job_id, payload = self.manager.events.get(timeout=5)
            if kind == "progress" and job_id == job.id:
                break
        started = time.monotonic()
        self.manager.cancel(job)
        self.assertEqual(self.events_until(job, ("cancelled", "error"))[-1][1][0], "cancelled")
        self.assertLess(time.monotonic() - started, 3)

    def test_pause_requeues_current_first(self):
        order = []

        def run(job):
            order.append(job.title)
            if job.title == "a" and order.count("a") == 1:
                job.cancel_event.wait(5)        # «качається», доки не натиснуть паузу
                raise downloader.Cancelled()
            return "Готово"

        self.use_runner(run)
        a = downloader.Job(url="a", title="a", out_dir=".")
        b = downloader.Job(url="b", title="b", out_dir=".")
        self.manager.submit(a)
        self.manager.submit(b)
        self.events_until(a, ("running",))
        self.manager.pause()
        seen = self.events_until(a, ("queued",))
        self.assertIn("Пауза", seen[-1][1][1])
        self.assertTrue(a.keep_partial is False and not a.cancel_event.is_set())
        time.sleep(0.3)
        self.assertEqual(order, ["a"])          # на паузі нічого нового не починається
        self.manager.resume()
        self.events_until(b, ("done",))
        self.assertEqual(order, ["a", "a", "b"])
        self.assertEqual(a.state, "done")

    def test_user_cancel_is_not_pause(self):
        def run(job):
            job.cancel_event.wait(5)
            raise downloader.Cancelled()

        self.use_runner(run)
        job = downloader.Job(url="u", title="t", out_dir=".")
        self.manager.submit(job)
        self.events_until(job, ("running",))
        self.manager.cancel(job)
        self.assertEqual(self.events_until(job, ("cancelled", "queued"))[-1][1][0], "cancelled")

    def test_batch_gap(self):
        with mock.patch.object(downloader, "BATCH_GAP", 0.3):
            job = downloader.Job(url="u", title="t", out_dir=".", prefs={"max_height": 720})
            self.manager._last_network = time.monotonic()
            started = time.monotonic()
            self.manager._throttle(job)
            self.assertGreaterEqual(time.monotonic() - started, 0.25)
            single = downloader.Job(url="u", title="t", out_dir=".")
            started = time.monotonic()
            self.manager._throttle(single)
            self.assertLess(time.monotonic() - started, 0.1)

class QuietCancelLogTest(unittest.TestCase):
    def test_errors_after_cancel_are_info(self):
        from rtube import applog
        job = downloader.Job(url="u", title="t", out_dir=".")
        downloader._current.job = job
        try:
            logger = applog.YtdlpLogger(quiet_errors=downloader._cancelling)
            with mock.patch.object(applog, "error") as error,                     mock.patch.object(applog, "info") as info:
                logger.error("ERROR: Postprocessing: Press [q] to stop")
                error.assert_called_once()
                job.cancel_event.set()
                logger.error("ERROR: Postprocessing: Press [q] to stop")
                error.assert_called_once()
                info.assert_called_once()
        finally:
            downloader._current.job = None

    def test_done_files_saved(self):
        saved = []
        with mock.patch.object(downloader, "applog", mock.MagicMock()):
            manager = downloader.DownloadManager(done_files={("k",): "old.mp4"},
                                                 on_done=saved.append)
            job = downloader.Job(url="u", title="t", out_dir=".")

            class FakeRunner:
                def __init__(self, job, *args, **kwargs):
                    self.job = job

                def run(self):
                    self.job.filepath = __file__
                    return "Готово"

            with mock.patch.object(downloader, "_Runner", FakeRunner):
                manager._process(job)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][("k",)], "old.mp4")
        self.assertIn(__file__, saved[0].values())

class SharedVideoQueueTest(unittest.TestCase):
    """Справжня черга: ролик для трьох товарів качається раз, під першим ID."""

    def test_one_download_named_by_first_id(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        downloads = []

        def fake_download(runner, info, with_subs):
            name = os.path.join(runner.job.out_dir, runner.job.product_id + ".mp4")
            downloads.append(runner.job.product_id)
            with open(name, "wb") as f:
                f.write(b"video")
            return {"requested_downloads": [{"filepath": name}]}

        patches = [mock.patch.object(downloader, "applog", mock.MagicMock()),
                   mock.patch.object(downloader, "analyze", return_value=INFO),
                   mock.patch.object(downloader._Runner, "_download", fake_download)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        manager = downloader.DownloadManager()
        url = INFO["webpage_url"]
        jobs = [downloader.Job(url=url, title="t", out_dir=tmp.name, product_id=pid,
                               prefs={"max_height": 720}, sub_key=None)
                for pid in ("590312170", "610963253", "580250272")]
        other = downloader.Job(url=url + "&x=1", title="t", out_dir=tmp.name,
                               product_id="111111111", prefs={"max_height": 720}, sub_key=None)
        with mock.patch.object(downloader, "BATCH_GAP", 0):
            for job in jobs + [other]:
                manager.submit(job)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and any(
                    j.state not in ("done", "error") for j in jobs + [other]):
                time.sleep(0.05)
        self.assertEqual([j.state for j in jobs + [other]], ["done"] * 4)
        self.assertEqual(downloads, ["590312170", "111111111"])
        first = os.path.join(tmp.name, "590312170.mp4")
        self.assertEqual([j.filepath for j in jobs], [first] * 3)
        self.assertEqual(sorted(os.listdir(tmp.name)), ["111111111.mp4", "590312170.mp4"])
        states = {}
        while not manager.events.empty():
            kind, job_id, payload = manager.events.get_nowait()
            if kind == "state":
                states[job_id] = payload[1]
        self.assertEqual(states[jobs[1].id],
                         "Те саме відео, що й у 590312170 — файл 590312170.mp4")

class QuickSkipTest(unittest.TestCase):
    """Файл уже в теці — «Уже є» без аналізу на YouTube і без паузи."""

    URL = "https://www.youtube.com/watch?v=pn6mZ0Bcugo"

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.probes = {}
        self.network = []
        for patch in (mock.patch.object(downloader, "applog", mock.MagicMock()),
                      mock.patch.object(downloader, "analyze", side_effect=AssertionError("мережа")),
                      mock.patch.object(downloader, "probe_file",
                                        side_effect=lambda path: self.probes.get(path, {}))):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, comment, quality, duration=95.0):
        path = os.path.join(self.tmp.name, name)
        open(path, "wb").close()
        self.probes[path] = {"comment": comment, "quality": quality, "duration": duration}
        return path

    def run_job(self, limit=1080):
        job = downloader.Job(url=self.URL, title="t", out_dir=self.tmp.name, product_id="590312170",
                             prefs={"max_height": limit}, sub_key=None)
        runner = downloader._Runner(job, lambda *a: None, on_network=lambda: self.network.append(1))
        return job, runner

    def test_same_video_same_quality_skips_without_network(self):
        path = self.file("590312170.mp4", self.URL, 1080)
        job, runner = self.run_job()
        self.assertEqual(runner.run(), downloader.ALREADY_NOTE)
        self.assertEqual(job.filepath, path)
        self.assertEqual(job.duration, 95.0)
        self.assertEqual(self.network, [])

    def test_other_quality_goes_the_long_way(self):
        self.file("590312170.mp4", self.URL, 720)
        job, runner = self.run_job()
        self.assertIsNone(runner._quick_skip())

    def test_best_quality_needs_analysis(self):
        self.file("590312170.mp4", self.URL, 1080)
        job, runner = self.run_job(limit=0)
        self.assertIsNone(runner._quick_skip())

    def test_finds_same_video_under_suffix(self):
        self.file("590312170.mp4", "https://www.youtube.com/watch?v=other000000", 1080)
        path = self.file("590312170_2.mp4", self.URL, 1080)
        job, runner = self.run_job()
        with self.assertRaises(downloader.AlreadyHave):
            runner._quick_skip()
        self.assertEqual(job.filepath, path)

    def test_no_file_goes_the_long_way(self):
        job, runner = self.run_job()
        self.assertIsNone(runner._quick_skip())

if __name__ == "__main__":
    unittest.main()
