"""Оновлювач yt-dlp: версії, колеса, активація з відкатом — без мережі.

Запуск: .venv\\Scripts\\python.exe -m unittest discover tests
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from rtube import ytupdate


class FakeSettings:
    def __init__(self, **values):
        self.values = {"ytdlp_bad": [], "ytdlp_checked_at": 0, **values}

    def get(self, key):
        return self.values.get(key)

    def set_many(self, **values):
        self.values.update(values)


def make_wheel(files, metadata=""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
        zf.writestr("yt_dlp-2099.1.1.dist-info/METADATA", metadata)
    return buf.getvalue()


def write_fake_package(root, version, body=""):
    pkg = os.path.join(root, "yt_dlp")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as f:
        f.write(body)
    with open(os.path.join(pkg, "version.py"), "w", encoding="utf-8") as f:
        f.write(f"__version__ = {version!r}\n")


@contextlib.contextmanager
def isolated_imports():
    """Справжній yt_dlp міг уже бути імпортований іншими тестами —
    ховаємо його на час тесту й повертаємо після."""
    saved = {k: v for k, v in sys.modules.items()
             if k == "yt_dlp" or k.startswith(("yt_dlp.", "yt_dlp_ejs"))}
    for k in saved:
        del sys.modules[k]
    path_before = list(sys.path)
    try:
        yield
    finally:
        for k in [k for k in sys.modules if k == "yt_dlp" or k.startswith(("yt_dlp.", "yt_dlp_ejs"))]:
            del sys.modules[k]
        sys.modules.update(saved)
        sys.path[:] = path_before


class VersionTest(unittest.TestCase):
    def test_numeric_compare(self):
        self.assertTrue(ytupdate.is_newer("2026.8.19", "2026.7.4"))
        self.assertTrue(ytupdate.is_newer("2026.10.1", "2026.9.30"))
        self.assertTrue(ytupdate.is_newer("2026.08.20", "2026.8.19"))
        self.assertFalse(ytupdate.is_newer("2026.8.19", "2026.8.19"))
        self.assertTrue(ytupdate.is_newer("2026.1.1", None))


class WheelTest(unittest.TestCase):
    def test_ejs_pin(self):
        wheel = make_wheel({}, "Name: yt-dlp\nRequires-Dist: yt-dlp-ejs==0.9.1; extra == 'default'\n")
        self.assertEqual(ytupdate.ejs_pin(wheel), "0.9.1")
        self.assertIsNone(ytupdate.ejs_pin(make_wheel({}, "Name: yt-dlp\n")))

    def test_sha256_mismatch(self):
        with self.assertRaises(ytupdate.UpdateError):
            ytupdate.verify_sha256(b"abc", hashlib.sha256(b"abd").hexdigest())
        ytupdate.verify_sha256(b"abc", hashlib.sha256(b"abc").hexdigest().upper())

    def test_foreign_host_rejected_before_download(self):
        with mock.patch.object(ytupdate, "_get") as get:
            with self.assertRaises(ytupdate.UpdateError):
                ytupdate.fetch_wheel("https://evil.example/yt_dlp.whl", "00")
            get.assert_not_called()

    def test_pick_wheel(self):
        data = {"urls": [
            {"packagetype": "sdist", "filename": "yt_dlp-1.tar.gz", "url": "x"},
            {"packagetype": "bdist_wheel", "filename": "yt_dlp-1-py3-none-any.whl",
             "url": "https://files.pythonhosted.org/a.whl", "digests": {"sha256": "ff"}}]}
        self.assertEqual(ytupdate.pick_wheel(data), ("https://files.pythonhosted.org/a.whl", "ff"))

    def test_extract_only_packages(self):
        wheel = make_wheel({"yt_dlp/__init__.py": "", "yt_dlp/utils/x.py": "",
                            "yt_dlp_ejs/yt/solver.js": "", "bin/yt-dlp": ""})
        with tempfile.TemporaryDirectory() as tmp:
            ytupdate.extract_packages(wheel, tmp)
            found = sorted(os.path.relpath(os.path.join(d, f), tmp).replace(os.sep, "/")
                           for d, _, fs in os.walk(tmp) for f in fs)
        self.assertEqual(found, ["yt_dlp/__init__.py", "yt_dlp/utils/x.py",
                                 "yt_dlp_ejs/yt/solver.js"])

    def test_zip_slip_rejected(self):
        wheel = make_wheel({"yt_dlp/../../evil.py": ""})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ytupdate.UpdateError):
                ytupdate.extract_packages(wheel, os.path.join(tmp, "lib"))


class ActivateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self.tmp.name, "lib")
        self.settings = FakeSettings()
        self.patches = [
            mock.patch.object(ytupdate, "LIB_DIR", self.lib),
            mock.patch.object(ytupdate, "CURRENT_PATH", os.path.join(self.lib, "current.json")),
            mock.patch.object(ytupdate, "settings", self.settings),
            mock.patch.object(ytupdate, "bundled_version", lambda: "2026.8.19"),
            mock.patch.dict(ytupdate.state),
            # Не смітити в справжній лог програми записами про фіктивні версії.
            mock.patch.object(ytupdate, "applog", mock.MagicMock()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def install(self, version, body=""):
        write_fake_package(os.path.join(self.lib, version), version, body)
        ytupdate._write_current(version)

    def test_newer_lib_activated(self):
        self.install("2099.1.1")
        with isolated_imports():
            ytupdate.activate()
            import yt_dlp
            self.assertTrue(yt_dlp.__file__.startswith(os.path.join(self.lib, "2099.1.1")))
        self.assertEqual((ytupdate.state["source"], ytupdate.state["active"]), ("lib", "2099.1.1"))

    def test_zero_padded_version_accepted(self):
        # Справжній yt-dlp пише «2026.08.19», а PyPI і current.json — «2026.8.19».
        write_fake_package(os.path.join(self.lib, "2099.1.1"), "2099.01.01")
        ytupdate._write_current("2099.1.1")
        with isolated_imports():
            ytupdate.activate()
        self.assertEqual(ytupdate.state["source"], "lib")
        self.assertTrue(ytupdate.same_version("2026.08.19", "2026.8.19"))

    def test_older_than_bundled_ignored(self):
        self.install("2020.1.1")
        with isolated_imports():
            ytupdate.activate()
            self.assertNotIn(os.path.join(self.lib, "2020.1.1"), sys.path)
        self.assertEqual(ytupdate.state["source"], "bundled")

    def test_bad_version_ignored(self):
        self.install("2099.1.1")
        self.settings.values["ytdlp_bad"] = ["2099.1.1"]
        with isolated_imports():
            ytupdate.activate()
        self.assertEqual(ytupdate.state["source"], "bundled")

    def test_broken_lib_rolls_back(self):
        self.install("2099.1.1", body="raise ImportError('зламано')\n")
        path = os.path.join(self.lib, "2099.1.1")
        with isolated_imports():
            ytupdate.activate()
            self.assertNotIn(path, sys.path)
            self.assertNotIn("yt_dlp", sys.modules)
        self.assertEqual(ytupdate.state["source"], "bundled")
        self.assertIn("2099.1.1", self.settings.values["ytdlp_bad"])


class InstallTest(ActivateTest):
    """check_and_install з підміненим PyPI."""

    def fake_pypi(self, selftest_ok=True):
        yt = make_wheel({"yt_dlp/__init__.py": "", "yt_dlp/version.py": "__version__ = '2099.1.1'\n"},
                        "Requires-Dist: yt-dlp-ejs==0.9.1; extra == 'default'\n")
        ejs = make_wheel({"yt_dlp_ejs/__init__.py": ""})

        def entry(data, name):
            return {"urls": [{"packagetype": "bdist_wheel", "filename": f"{name}-py3-none-any.whl",
                              "url": f"https://files.pythonhosted.org/{name}.whl",
                              "digests": {"sha256": hashlib.sha256(data).hexdigest()}}]}

        jsons = {
            ytupdate.PYPI_JSON.format(name="yt-dlp"): {**entry(yt, "yt_dlp"),
                                                       "info": {"version": "2099.1.1"}},
            ytupdate.PYPI_VERSION_JSON.format(name="yt-dlp-ejs", version="0.9.1"): entry(ejs, "ejs"),
        }
        blobs = {"https://files.pythonhosted.org/yt_dlp.whl": yt,
                 "https://files.pythonhosted.org/ejs.whl": ejs}
        return [mock.patch.object(ytupdate, "_get_json", side_effect=lambda u: jsons[u]),
                mock.patch.object(ytupdate, "_get", side_effect=lambda u, timeout=30: blobs[u]),
                mock.patch.object(ytupdate, "run_selftest", return_value=selftest_ok)]

    def test_installs_both_packages(self):
        ytupdate.state.update(active="2026.8.19", source="bundled", pending=None)
        with contextlib.ExitStack() as stack:
            for p in self.fake_pypi():
                stack.enter_context(p)
            self.assertEqual(ytupdate.check_and_install(), "2099.1.1")
        target = os.path.join(self.lib, "2099.1.1")
        self.assertTrue(os.path.isfile(os.path.join(target, "yt_dlp", "version.py")))
        self.assertTrue(os.path.isfile(os.path.join(target, "yt_dlp_ejs", "__init__.py")))
        self.assertFalse(os.path.exists(target + ".tmp"))
        with open(os.path.join(self.lib, "current.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["version"], "2099.1.1")

    def test_failed_selftest_marks_bad(self):
        ytupdate.state.update(active="2026.8.19", source="bundled", pending=None)
        with contextlib.ExitStack() as stack:
            for p in self.fake_pypi(selftest_ok=False):
                stack.enter_context(p)
            with self.assertRaises(ytupdate.UpdateError):
                ytupdate.check_and_install()
        self.assertIn("2099.1.1", self.settings.values["ytdlp_bad"])
        self.assertFalse(os.path.exists(os.path.join(self.lib, "2099.1.1")))
        self.assertIsNone(ytupdate.read_current())

    def test_unknown_state_uses_loaded_version(self):
        # state порожній, але працюючий yt-dlp уже найсвіжіший — нічого не ставимо.
        ytupdate.state.update(active=None, source="bundled", pending=None)
        with contextlib.ExitStack() as stack:
            for p in self.fake_pypi():
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(ytupdate, "_loaded_version",
                                                  return_value="2099.01.01"))
            self.assertIsNone(ytupdate.check_and_install())
        self.assertFalse(os.path.exists(os.path.join(self.lib, "2099.1.1")))

    def test_no_timer_checks_every_time(self):
        # Раніше перевірка пропускалась, якщо була менш ніж 12 год тому.
        self.settings.values["ytdlp_checked_at"] = __import__("time").time()
        with contextlib.ExitStack() as stack:
            for p in self.fake_pypi():
                stack.enter_context(p)
            self.assertEqual(ytupdate.check_and_install(), "2099.1.1")


if __name__ == "__main__":
    unittest.main()
