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

    def test_failed_write_keeps_old_file(self):
        settings.set_many(theme="Світла")
        with mock.patch.object(settings.json, "dump", side_effect=OSError("disk full")):
            settings.set_many(theme="Темна")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["theme"], "Світла")
        self.assertFalse(os.path.exists(self.path + ".tmp") and os.path.getsize(self.path) == 0)


if __name__ == "__main__":
    unittest.main()
