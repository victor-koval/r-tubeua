"""Налаштування: запис не має губити файл."""

import json
import os
import tempfile
import unittest
from unittest import mock

from rtube import settings


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "settings.json")
        self.patches = [mock.patch.object(settings, "SETTINGS_PATH", self.path),
                        mock.patch.object(settings, "CONFIG_DIR", self.tmp.name),
                        mock.patch.object(settings, "_cache", dict(settings.DEFAULTS))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_ftp_sections_known_and_ordered(self):
        settings.set_many(ftp_sections="video4, video, vidoe3, /video2/")
        self.assertEqual(settings.ftp_sections(), ["video", "video2", "video4"])
        settings.set_many(ftp_sections="щось не те")
        self.assertEqual(settings.ftp_sections(), list(settings.FTP_SECTIONS))

    def test_failed_write_keeps_old_file(self):
        settings.set_many(theme="Світла")
        with mock.patch.object(settings.json, "dump", side_effect=OSError("disk full")):
            settings.set_many(theme="Темна")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["theme"], "Світла")
        self.assertFalse(os.path.exists(self.path + ".tmp") and os.path.getsize(self.path) == 0)


class MigrationTest(unittest.TestCase):
    """Два старі перемикачі «Автоматично оновлювати…» → один «при запуску»."""

    def load(self, stored):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stored, f)
        with mock.patch.object(settings, "SETTINGS_PATH", path),                 mock.patch.object(settings, "_cache", None):
            return settings.get("check_updates_on_start")

    def test_default_on(self):
        self.assertTrue(self.load({}))

    def test_both_old_switches_off_keeps_it_off(self):
        self.assertFalse(self.load({"ytdlp_autoupdate": False, "app_autoupdate": False}))

    def test_one_old_switch_on_means_on(self):
        self.assertTrue(self.load({"ytdlp_autoupdate": True, "app_autoupdate": False}))


if __name__ == "__main__":
    unittest.main()
