import os
import tempfile
import unittest
from unittest import mock

from rtube import credentials, ftpclient
from tests.fakeftp import Server


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.local = os.path.join(self.tmp.name, "590312170.mp4")
        with open(self.local, "wb") as f:
            f.write(os.urandom(700 * 1024))
        self.server = Server(dirs=["/video/odyag_vzuttya_ta_aksesuari/odyag",
                                   "/video/tegi", "/video2/bt"])
        self.client = ftpclient.FtpClient("h", "u", "secret", factory=self.server.connect)
        self.patch = mock.patch.object(ftpclient, "applog", mock.MagicMock())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def data(self):
        with open(self.local, "rb") as f:
            return f.read()

    def test_bad_password(self):
        bad = ftpclient.FtpClient("h", "u", "wrong", factory=self.server.connect)
        with self.assertRaises(Exception):
            bad.connect()

    def test_read_tree_dirs_only(self):
        self.server.files["/video/readme.txt"] = b"x"
        tree = self.client.read_tree(["video", "video2"])
        self.assertIn(["odyag_vzuttya_ta_aksesuari", "odyag"], tree["video"])
        self.assertEqual(tree["video2"], [["bt"]])
        self.assertNotIn(["readme.txt"], tree["video"])

    def test_upload_via_temp_name(self):
        parts = ["video", "odyag_vzuttya_ta_aksesuari", "odyag"]
        done = self.client.upload(self.local, parts, "590312170.mp4")
        self.assertEqual(done, "video/odyag_vzuttya_ta_aksesuari/odyag/590312170.mp4")
        self.assertEqual(self.server.files["/" + done], self.data())
        stor = [e for e in self.server.log if e[0] == "STOR"]
        self.assertTrue(stor[0][1].endswith(".rtube-part"))
        self.assertFalse(any(k.endswith(".rtube-part") for k in self.server.files))

    def test_never_section_root(self):
        with self.assertRaises(ftpclient.RootForbidden):
            self.client.upload(self.local, ["video"], "590312170.mp4")
        with self.assertRaises(ftpclient.RootForbidden):
            self.client.upload(self.local, [], "590312170.mp4")
        self.assertEqual(self.server.files, {})

    def test_existing_file_not_overwritten(self):
        self.server.files["/video/tegi/590312170.mp4"] = b"old"
        with self.assertRaises(ftpclient.FtpError):
            self.client.upload(self.local, ["video", "tegi"], "590312170.mp4")
        self.assertEqual(self.server.files["/video/tegi/590312170.mp4"], b"old")

    def test_resume_after_drop(self):
        """Обірване з'єднання — перепідключення й докачування з того місця."""
        self.server.drop_next_stor = True
        self.client.upload(self.local, ["video", "tegi"], "590312170.mp4")
        self.assertEqual(self.server.files["/video/tegi/590312170.mp4"], self.data())
        stors = [e for e in self.server.log if e[0] == "STOR"]
        self.assertEqual(len(stors), 2)
        self.assertGreater(stors[1][2], 0)          # друга спроба — з місця обриву
        self.assertEqual(self.server.connections, 2)

    def test_resume_leftover_part(self):
        """Від минулої спроби лишився шматок — заливаємо лише решту."""
        half = self.data()[:300 * 1024]
        self.server.files["/video/tegi/590312170.mp4.rtube-part"] = half
        self.client.upload(self.local, ["video", "tegi"], "590312170.mp4")
        self.assertEqual(self.server.log[0], ("STOR", "/video/tegi/590312170.mp4.rtube-part",
                                              len(half)))
        self.assertEqual(self.server.files["/video/tegi/590312170.mp4"], self.data())

    def test_full_leftover_part_only_renamed(self):
        """Тимчасовий уже повний (обірвалось перед перейменуванням) — не заливаємо з нуля."""
        self.server.files["/video/tegi/590312170.mp4.rtube-part"] = self.data()
        self.client.upload(self.local, ["video", "tegi"], "590312170.mp4")
        self.assertEqual([e for e in self.server.log if e[0] == "STOR"], [])
        self.assertEqual(self.server.files["/video/tegi/590312170.mp4"], self.data())

    def test_no_space(self):
        self.server.limits["/video"] = 100 * 1024
        with self.assertRaises(ftpclient.NoSpace):
            self.client.upload(self.local, ["video", "tegi"], "590312170.mp4")
        self.assertEqual(self.server.files, {})         # свій недолитий шматок прибрано

    def test_cancel(self):
        with self.assertRaises(ftpclient.Cancelled):
            self.client.upload(self.local, ["video", "tegi"], "590312170.mp4", cancel=lambda: True)
        self.assertNotIn("/video/tegi/590312170.mp4", self.server.files)

    def test_progress(self):
        seen = []
        self.client.upload(self.local, ["video", "tegi"], "590312170.mp4",
                           progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen[-1], (700 * 1024, 700 * 1024))

    def test_is_no_space(self):
        self.assertTrue(ftpclient.is_no_space(Exception("452 Error during write to file")))
        self.assertTrue(ftpclient.is_no_space(Exception("552 Quota exceeded")))
        self.assertTrue(ftpclient.is_no_space(Exception("451 No space left on device")))
        self.assertFalse(ftpclient.is_no_space(Exception("550 Permission denied")))

    def test_cyrillic_names_round_trip(self):
        raw = "tokeni_і_smart_karti".encode("utf-8").decode("latin-1")
        self.server.dirs |= {"/video/" + raw}
        names = [n for n, is_dir, _ in self.client.list_dir(["video"])]
        self.assertIn("tokeni_і_smart_karti", names)
        self.client.upload(self.local, ["video", "tokeni_і_smart_karti"], "1.mp4")
        self.assertIn(f"/video/{raw}/1.mp4", self.server.files)


class CredentialsTest(unittest.TestCase):
    TARGET = "R-TubeUA FTP (тест)"

    def tearDown(self):
        credentials.delete(self.TARGET)

    def test_round_trip(self):
        self.assertIsNone(credentials.load(self.TARGET))
        credentials.save("video", "пароль-ghY", self.TARGET)
        self.assertEqual(credentials.load(self.TARGET), ("video", "пароль-ghY"))
        self.assertTrue(credentials.delete(self.TARGET))
        self.assertIsNone(credentials.load(self.TARGET))
        self.assertFalse(credentials.delete(self.TARGET))


if __name__ == "__main__":
    unittest.main()
