import os
import tempfile
import time
import unittest
from unittest import mock

from rtube import ftpcat, ftpclient, ftphistory, ftpstate
from tests.fakeftp import Server


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(ftpstate, "CONFIG_DIR", self.tmp.name),
                        mock.patch.object(ftpstate, "BASE_NAME", "nema_bazy.json"),
                        mock.patch.object(ftphistory, "applog", mock.MagicMock()),
                        mock.patch.object(ftphistory.rozetka, "save_cache")]
        for p in self.patches:
            p.start()
        ftpstate.reset_cache()
        files = {f"/video/odyag_vzuttya_ta_aksesuari/prikrasi/{i}.mp4": b"x"
                 for i in range(100000001, 100000006)}
        files["/video/odyag_vzuttya_ta_aksesuari/prikrasi/readme.txt"] = b"x"
        files["/video/rich_content/80003/200000001.mp4"] = b"x"
        self.server = Server(dirs=["/video/odyag_vzuttya_ta_aksesuari/prikrasi",
                                   "/video/rich_content/80003"], files=files)
        self.calls = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        ftpstate.reset_cache()
        self.tmp.cleanup()

    def connect(self):
        return ftpclient.FtpClient("h", "u", "secret", factory=self.server.connect).connect()

    def mpath(self, pid):
        self.calls.append(pid)
        return ["1162030", "4630220", "4630370"]          # біжутерія

    def run_builder(self):
        builder = ftphistory.HistoryBuilder(self.connect, ["video"], product_mpath=self.mpath)
        builder.start().thread.join(10)
        self.assertFalse(builder.status["running"])
        self.assertIsNone(builder.status["error"], builder.status["text"])
        return builder

    def test_learns_where_people_put_things(self):
        self.run_builder()
        self.assertEqual(sorted(self.calls), [str(i) for i in range(100000001, 100000006)])
        self.assertEqual(ftpstate.index_size("video"), 5)
        tree = ftpstate.tree("video")
        r = ftpcat.resolve(tree, {"mpath": ["1162030", "4630220", "4630370", "4657902"],
                                  "crumbs_ua": ["Одяг, взуття та аксесуари", "Сумки та аксесуари"]},
                           ftpstate.index("video"))
        self.assertEqual((r.path, r.source), (("odyag_vzuttya_ta_aksesuari", "prikrasi"),
                                              ftpcat.HISTORY))

    def test_resume_skips_seen(self):
        self.run_builder()
        self.calls.clear()
        self.run_builder()
        self.assertEqual(self.calls, [])                  # усе вже оброблено
        self.assertEqual(ftpstate.index_size("video"), 5)

    def test_sample_limit(self):
        with mock.patch.object(ftphistory, "SAMPLE_PER_FOLDER", 2):
            client = self.connect()
            client.read_tree(["video"])
            ftpstate.save_tree(client.read_tree(["video"]))
            files = ftphistory.sample_files(client, "video", ftpstate.tree("video"), per_folder=2)
        self.assertEqual(len(files), 2)                   # rich_content пропущено

    def test_site_errors_not_marked_seen(self):
        """Сайт не відповів — товар не «оброблений»: наступного разу ще раз."""
        flaky = {"100000002"}

        def mpath(pid):
            if pid in flaky:
                raise ftphistory.rozetka.Unavailable("SSL")
            return ["1"]

        builder = ftphistory.HistoryBuilder(self.connect, ["video"], product_mpath=mpath).start()
        builder.thread.join(10)
        self.assertEqual(ftpstate.index_size("video"), 4)
        flaky.clear()
        self.run_builder()                                  # друга спроба — лише пропущений
        self.assertEqual(self.calls, ["100000002"])

    def test_site_down_stops(self):
        def down(pid):
            raise ftphistory.rozetka.Unavailable("offline")

        builder = ftphistory.HistoryBuilder(self.connect, ["video"], product_mpath=down).start()
        builder.thread.join(10)
        self.assertIn("не відповідає", builder.status["text"])
        self.assertEqual(ftpstate.index_size("video"), 0)

    def test_stop(self):
        def slow(pid):
            time.sleep(0.2)
            return ["1"]

        builder = ftphistory.HistoryBuilder(self.connect, ["video"], product_mpath=slow).start()
        time.sleep(0.3)
        builder.stop()
        builder.thread.join(5)
        self.assertIn("Зупинено", builder.status["text"])
        self.assertLess(ftpstate.index_size("video"), 5)


class PartialProductTest(unittest.TestCase):
    def test_ua_only_then_completed(self):
        from rtube import rozetka
        from tests.test_rozetka import RU, UA
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(rozetka, "CACHE_PATH", os.path.join(tmp, "p.json")), \
                mock.patch.object(rozetka, "CONFIG_DIR", tmp), \
                mock.patch.object(rozetka, "_cache", None):
            calls = []

            def fetch(url):
                calls.append(url)
                return UA if "lang=ua" in url else RU

            info = rozetka.product_info("5", fetch, languages=("ua",))
            self.assertTrue(info["partial"])
            self.assertEqual(len(calls), 1)               # лише один запит
            self.assertEqual(rozetka.product_info("5", fetch, languages=("ua",))["mpath"],
                             info["mpath"])
            self.assertEqual(len(calls), 1)               # з кешу
            full = rozetka.product_info("5", fetch)       # для плану — з російськими назвами
            self.assertFalse(full.get("partial"))
            self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
