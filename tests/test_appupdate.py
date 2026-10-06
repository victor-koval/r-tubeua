"""Оновлення програми з релізів GitHub: розбір відповіді, перевірка, заміна .exe."""

import hashlib
import os
import tempfile
import unittest
from unittest import mock

from rtube import appupdate

DATA = b"MZ fake exe" * 1000
SHA = hashlib.sha256(DATA).hexdigest()


def release(**over):
    data = {"tag_name": "v1.4.0", "draft": False, "prerelease": False,
            "assets": [{"name": "R-TubeUA.exe", "size": len(DATA), "digest": f"sha256:{SHA}",
                        "browser_download_url":
                            "https://github.com/victor-koval/r-tubeua/releases/download/"
                            "v1.4.0/R-TubeUA.exe"}]}
    data.update(over)
    return data


class ParseTest(unittest.TestCase):
    def test_release(self):
        r = appupdate.parse_release(release())
        self.assertEqual(r["version"], "1.4.0")
        self.assertEqual(r["sha256"], SHA)
        self.assertEqual(r["size"], len(DATA))

    def test_skips_drafts_and_prereleases(self):
        self.assertIsNone(appupdate.parse_release(release(draft=True)))
        self.assertIsNone(appupdate.parse_release(release(prerelease=True)))

    def test_requires_digest(self):
        data = release()
        del data["assets"][0]["digest"]
        self.assertIsNone(appupdate.parse_release(data))

    def test_odd_tag(self):
        self.assertIsNone(appupdate.parse_release(release(tag_name="nightly")))


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_verified_download(self):
        r = appupdate.parse_release(release())
        path = appupdate.download(r, self.tmp.name, fetch=lambda url: DATA)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), DATA)
        # Другий раз не качає: файл уже є й збігається.
        self.assertEqual(appupdate.download(r, self.tmp.name, fetch=lambda url: 1 / 0), path)

    def test_bad_hash_rejected(self):
        r = appupdate.parse_release(release())
        with self.assertRaises(appupdate.UpdateError):
            appupdate.download(r, self.tmp.name, fetch=lambda url: DATA[:-1] + b"X")
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_foreign_host_rejected(self):
        r = dict(appupdate.parse_release(release()), url="https://evil.example/R-TubeUA.exe")
        with self.assertRaises(appupdate.UpdateError):
            appupdate.download(r, self.tmp.name, fetch=lambda url: DATA)


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exe = os.path.join(self.tmp.name, "R-TubeUA.exe")
        with open(self.exe, "wb") as f:
            f.write(b"old")
        self.new = os.path.join(self.tmp.name, "new.exe")
        with open(self.new, "wb") as f:
            f.write(b"new")

    def tearDown(self):
        self.tmp.cleanup()

    def test_swap_keeps_old_copy(self):
        appupdate.apply(self.new, exe=self.exe)
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"new")
        with open(self.exe + ".old", "rb") as f:
            self.assertEqual(f.read(), b"old")

    def test_failed_copy_restores_old(self):
        with mock.patch.object(appupdate.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                appupdate.apply(self.new, exe=self.exe)
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"old")

    def test_cleanup(self):
        appupdate.apply(self.new, exe=self.exe)
        update_dir = os.path.join(self.tmp.name, "update")
        os.makedirs(update_dir)
        for name in ("R-TubeUA-1.3.0.exe", "R-TubeUA-1.4.0.exe", "R-TubeUA-1.5.0.exe"):
            open(os.path.join(update_dir, name), "wb").close()
        with mock.patch.object(appupdate, "UPDATE_DIR", update_dir):
            appupdate.cleanup("1.4.0", exe=self.exe)
        self.assertFalse(os.path.exists(self.exe + ".old"))
        self.assertEqual(os.listdir(update_dir), ["R-TubeUA-1.5.0.exe"])


class CheckTest(unittest.TestCase):
    def test_not_frozen_does_nothing(self):
        with mock.patch.object(appupdate, "fetch_latest", side_effect=AssertionError):
            self.assertIsNone(appupdate.check_and_download("1.3.1"))

    def test_newer_release_downloaded_and_tested(self):
        r = appupdate.parse_release(release())
        with mock.patch.object(appupdate, "enabled", return_value=True), \
                mock.patch.object(appupdate, "fetch_latest", return_value=r), \
                mock.patch.object(appupdate, "download", return_value="x.exe") as dl, \
                mock.patch.object(appupdate, "selftest", return_value=True), \
                mock.patch.object(appupdate.settings, "set_many"), \
                mock.patch.object(appupdate, "applog"):
            self.assertEqual(appupdate.check_and_download("1.3.1"), "1.4.0")
            self.assertEqual(appupdate.state["path"], "x.exe")
            appupdate.state.update(version=None, path=None)
            self.assertIsNone(appupdate.check_and_download("1.4.0"))
            dl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
