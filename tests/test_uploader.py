import json
import os
import queue
import tempfile
import time
import unittest
from unittest import mock

from rtube import ftpcat, ftpclient, ftpstate, uploader
from tests.fakeftp import Server

DIRS = ["/video/odyag_vzuttya_ta_aksesuari/odyag", "/video/odyag_vzuttya_ta_aksesuari/prikrasi",
        "/video/krasa_ta_zdorovya/apteka",
        "/video2/clothes_ahd_shoes/clothes", "/video2/bt",
        "/video3/odezhda_obuv_aksessuary/odezhda"]
HOODIE = {"crumbs_ua": ["Одяг, взуття та аксесуари", "Одяг", "Чоловічі худі"],
          "crumbs_ru": ["Одежда, обувь и аксессуары", "Одежда", "Мужские худи"],
          "slugs": ["shoes clothes", "clothes", "mugskie hudi"],
          "mpath": ["1162030", "2033137", "4637959"]}


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(ftpstate, "CONFIG_DIR", self.tmp.name)
        self.patch.start()
        self.no_base = mock.patch.object(ftpstate, "BASE_NAME", "nema_bazy.json")
        self.no_base.start()
        ftpstate.reset_cache()

    def tearDown(self):
        self.patch.stop()
        self.no_base.stop()
        ftpstate.reset_cache()
        self.tmp.cleanup()

    def test_tree_round_trip(self):
        self.assertIsNone(ftpstate.tree("video"))
        ftpstate.save_tree({"video": [["a"], ["a", "b"]]})
        ftpstate.reset_cache()
        self.assertEqual(ftpstate.tree("video")[("a",)], ["b"])
        self.assertIsNotNone(ftpstate.tree_age())

    def test_learn_and_remember_persist(self):
        ftpstate.learn("video", ["1", "2"], ("a", "b"))
        ftpstate.remember("video", ["1", "2"], ("a",))
        ftpstate.reset_cache()
        self.assertEqual(ftpstate.index("video")["2"], {"a/b": 1})
        self.assertEqual(ftpstate.index_size("video"), 1)
        self.assertEqual(ftpstate.rules("video"), {"2": ["a"]})

    def test_uploaded_tied_to_file_version(self):
        local = os.path.join(self.tmp.name, "1.mp4")
        with open(local, "wb") as f:
            f.write(b"x" * 10)
        ftpstate.mark_uploaded(local, "video/a/1.mp4")
        self.assertEqual(ftpstate.uploaded(local), "video/a/1.mp4")
        with open(local, "wb") as f:
            f.write(b"y" * 20)              # файл перекачали — це вже інший файл
        self.assertIsNone(ftpstate.uploaded(local))


class BatchProgressTest(unittest.TestCase):
    def test_batch_progress_and_text(self):
        mb = 1024 * 1024
        done = uploader.UploadTask("a.mp4", "1", state=uploader.UPLOADED, total=40 * mb)
        going = uploader.UploadTask("b.mp4", "2", state=uploader.UPLOADING, total=60 * mb,
                                    sent=20 * mb, speed=5 * mb)
        waiting = uploader.UploadTask("c.mp4", "3", state=uploader.QUEUED, total=20 * mb)
        skipped = uploader.UploadTask("d.mp4", "4", state=uploader.NEED_CHOICE, total=99 * mb)
        p = uploader.batch_progress([done, going, waiting, skipped])
        self.assertEqual(p[:4], (1, 3, 60 * mb, 120 * mb))
        self.assertAlmostEqual(p[5], 0.5)
        self.assertEqual(uploader.describe_batch(p),
                         "Заливається 2 з 3  ·  60 з 120 МБ  ·  5,0 МБ/с  ·  ще ~12 с")
        self.assertIsNone(uploader.batch_progress([skipped]))


class BaseTest(unittest.TestCase):
    """Вшита база + свій шар."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "base.json")
        self.patches = [mock.patch.object(ftpstate, "CONFIG_DIR", self.tmp.name),
                        mock.patch.object(ftpstate, "base_path", lambda: self.base)]
        for p in self.patches:
            p.start()
        self.write_base(100, index={"2": {"a/b": 3, "_": 0}, "_count": 3}, seen=["1", "2"])

    def tearDown(self):
        for p in self.patches:
            p.stop()
        ftpstate.reset_cache()
        self.tmp.cleanup()

    def write_base(self, built, index, seen):
        with open(self.base, "w", encoding="utf-8") as f:
            json.dump({"built_at": built, "tree": {"video": [["a"], ["a", "b"], ["c"]]},
                       "index": {"video": index}, "seen": {"video": seen}}, f)
        ftpstate.reset_cache()

    def test_base_used_without_local_data(self):
        self.assertEqual(ftpstate.tree("video")[("a",)], ["b"])
        self.assertEqual(ftpstate.index("video")["2"], {"a/b": 3, "_": 0})
        self.assertEqual(ftpstate.index_size("video"), 3)
        self.assertEqual(ftpstate.seen("video"), {"1", "2"})
        self.assertEqual(ftpstate.tree_age(), 100)

    def test_local_layer_on_top(self):
        ftpstate.learn("video", ["1", "2"], ("c",))
        ftpstate.mark_seen("video", ["3", "1"])
        ftpstate.reset_cache()
        self.assertEqual(ftpstate.index("video")["2"], {"a/b": 3, "_": 0, "c": 1})
        self.assertEqual(ftpstate.index_size("video"), 4)
        self.assertEqual(ftpstate.seen("video"), {"1", "2", "3"})
        ftpstate.save_tree({"video": [["d"]]})             # перечитане — замість бази
        self.assertEqual(ftpstate.tree("video")[()], ["d"])

    def test_newer_base_drops_local_layer(self):
        ftpstate.learn("video", ["1", "2"], ("c",))
        ftpstate.mark_seen("video", ["3"])
        ftpstate.remember("video", ["2"], ("a",))
        self.write_base(200, index={"2": {"c": 9}, "_count": 9}, seen=["1", "2", "3", "4"])
        self.assertEqual(ftpstate.index("video")["2"], {"c": 9})  # своє вже в новій базі
        self.assertEqual(ftpstate.seen("video"), {"1", "2", "3", "4"})
        self.assertEqual(ftpstate.rules("video"), {"2": ["a"]})   # ручні вибори — ваші, лишаються

    def test_export_round_trip(self):
        ftpstate.learn("video", ["1", "2"], ("c",))
        ftpstate.mark_seen("video", ["3"])
        out = os.path.join(self.tmp.name, "new_base.json")
        self.assertEqual(ftpstate.export_base(out), {"video": 4})
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["index"]["video"]["2"]["c"], 1)
        self.assertEqual(data["seen"]["video"], ["1", "2", "3"])
        self.assertGreater(data["built_at"], 100)


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(ftpstate, "CONFIG_DIR", self.tmp.name),
                        mock.patch.object(ftpstate, "BASE_NAME", "nema_bazy.json"),
                        mock.patch.object(uploader, "applog", mock.MagicMock()),
                        mock.patch.object(ftpclient, "applog", mock.MagicMock())]
        for p in self.patches:
            p.start()
        ftpstate.reset_cache()
        self.server = Server(dirs=DIRS)
        self.order = ["video", "video2", "video3"]
        self.info = {"590312170": HOODIE}
        self.manager = uploader.UploadManager(
            lambda: ftpclient.FtpClient("h", "u", "secret", factory=self.server.connect).connect(),
            lambda: self.order, product_info=self.info.get)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        ftpstate.reset_cache()
        self.tmp.cleanup()

    def file(self, name="590312170.mp4", size=50_000):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(os.urandom(size))
        return path

    def wait(self, task, states, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.manager.events.get(timeout=0.05)
            except queue.Empty:
                pass
            if task.state in states and not self.manager.is_busy():
                return
        self.fail(f"стан {task.state} ({task.note}), а чекали {states}")

    def test_plan_reads_tree_once_and_resolves(self):
        task = uploader.UploadTask(self.file(), "590312170")
        self.manager.plan([task])
        self.wait(task, (uploader.PLANNED,))
        self.assertEqual(task.folder, "video/odyag_vzuttya_ta_aksesuari/odyag")
        self.assertEqual(task.source, ftpcat.NAMES)
        self.assertIn("Одяг", task.site)
        self.assertIsNotNone(ftpstate.tree("video"))

    def test_upload_and_learn(self):
        task = uploader.UploadTask(self.file(), "590312170")
        self.manager.plan([task])
        self.wait(task, (uploader.PLANNED,))
        self.manager.upload([task])
        self.wait(task, (uploader.UPLOADED,))
        self.assertIn("/video/odyag_vzuttya_ta_aksesuari/odyag/590312170.mp4", self.server.files)
        self.assertEqual(ftpstate.index("video")["4637959"],
                         {"odyag_vzuttya_ta_aksesuari/odyag": 1})
        again = uploader.UploadTask(task.local, "590312170")
        self.manager.plan([again])
        self.wait(again, (uploader.ALREADY,))           # той самий файл удруге не заливається

    def test_full_section_moves_to_next(self):
        """video забитий → video2 (стара схема), там та сама категорія."""
        self.server.limits["/video"] = 10_000
        task = uploader.UploadTask(self.file(), "590312170")
        self.manager.plan([task])
        self.wait(task, (uploader.PLANNED,))
        self.manager.upload([task])
        self.wait(task, (uploader.UPLOADED, uploader.NEED_CHOICE, uploader.ERROR))
        self.assertEqual(task.state, uploader.UPLOADED, task.note)
        self.assertTrue(task.ftp_path.startswith("video2/clothes_ahd_shoes"), task.ftp_path)
        self.assertIn("video", self.manager.full_sections())
        self.assertFalse(any(k.startswith("/video/") for k in self.server.files))

    def test_all_full(self):
        for s in ("/video", "/video2", "/video3"):
            self.server.limits[s] = 1000
        task = uploader.UploadTask(self.file(), "590312170")
        self.manager.plan([task])
        self.wait(task, (uploader.PLANNED,))
        self.manager.upload([task])
        self.wait(task, (uploader.ERROR, uploader.NEED_CHOICE))
        self.assertIn("забиті", task.note)

    def test_unknown_product_needs_choice_and_rule_is_remembered(self):
        task = uploader.UploadTask(self.file("1.mp4"), "1")
        self.manager.plan([task])
        self.wait(task, (uploader.NEED_CHOICE,))
        with self.assertRaises(ValueError):
            self.manager.choose(task, "video", ())          # корінь — ні
        with self.assertRaises(ValueError):
            self.manager.choose(task, "video", ("nema",))
        task.mpath = ["777"]
        self.manager.choose(task, "video", ("krasa_ta_zdorovya", "apteka"))
        self.wait(task, (uploader.PLANNED,))
        self.assertEqual(ftpstate.rules("video"), {"777": ["krasa_ta_zdorovya", "apteka"]})

    def test_vanished_folder_rereads_tree(self):
        """Тека з бази на сервері зникла — дерево перечитується, тека визначається заново."""
        ftpstate.save_tree({"video": [["nema"], ["nema", "teky"]]})
        task = uploader.UploadTask(self.file(), "590312170", mpath=HOODIE["mpath"],
                                   product=HOODIE, section="video", path=("nema", "teky"),
                                   state=uploader.PLANNED)
        self.manager.upload([task])
        self.wait(task, (uploader.UPLOADED, uploader.ERROR))
        self.assertEqual(task.state, uploader.UPLOADED, task.note)
        self.assertEqual(task.folder, "video/odyag_vzuttya_ta_aksesuari/odyag")
        self.assertIsNone(ftpstate.tree("video").get(("nema",)))

    def test_existing_different_file_needs_confirm(self):
        local = self.file()
        self.server.files["/video/odyag_vzuttya_ta_aksesuari/odyag/590312170.mp4"] = b"other"
        task = uploader.UploadTask(local, "590312170")
        self.manager.plan([task])
        self.wait(task, (uploader.CONFIRM,))
        task.overwrite = True
        self.manager.upload([task])
        self.wait(task, (uploader.UPLOADED,))
        with open(local, "rb") as f:
            self.assertEqual(self.server.files["/" + task.ftp_path], f.read())

    def test_existing_same_size_is_already(self):
        local = self.file()
        with open(local, "rb") as f:
            self.server.files["/video/odyag_vzuttya_ta_aksesuari/odyag/590312170.mp4"] = f.read()
        task = uploader.UploadTask(local, "590312170")
        self.manager.plan([task])
        self.wait(task, (uploader.ALREADY,))

    def test_cancel_queued(self):
        task = uploader.UploadTask(self.file(), "590312170", path=("x",), section="video",
                                   state=uploader.PLANNED)
        with self.manager._cond:            # затримуємо потік, щоб завдання постояло в черзі
            self.manager._todo.append(("upload", task))
            task.state = uploader.QUEUED
            self.manager.cancel(task)
            self.manager._cond.notify_all()
        self.wait(task, (uploader.CANCELLED,))
        self.assertEqual(self.server.files, {})

    def test_bad_password(self):
        manager = uploader.UploadManager(
            lambda: ftpclient.FtpClient("h", "u", "wrong", factory=self.server.connect).connect(),
            lambda: self.order, product_info=self.info.get)
        self.manager = manager
        task = uploader.UploadTask(self.file(), "590312170")
        manager.plan([task])
        self.wait(task, (uploader.ERROR,))
        self.assertIn("пароль", task.note)


if __name__ == "__main__":
    unittest.main()
