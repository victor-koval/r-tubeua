"""Встановлення JS-рантайму без мережі: суми Deno й Node, запасне джерело, підказка в помилці."""

import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from rtube import downloader, ffinstall, jsinstall

SHA = "a" * 64
# Так Deno кладе суму поруч з архівом — вивід PowerShell Get-FileHash.
DENO_SUM = f"\r\nAlgorithm : SHA256\r\nHash      : {SHA.upper()}\r\nPath      : deno.zip\r\n"
NODE_SUMS = (f"{'b' * 64}  node-v24.21.0-win-arm64.zip\n"
             f"{SHA}  node-v24.21.0-win-x64.zip\n"
             f"{'c' * 64}  node-v24.21.0-win-x64.7z\n")


def write_zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"MZ fake " + name.encode())


class SourceTest(unittest.TestCase):
    def test_deno_get_filehash_format(self):
        self.assertEqual(ffinstall.parse_checksum(DENO_SUM), SHA)
        with mock.patch.object(jsinstall, "_open", return_value=io.BytesIO(DENO_SUM.encode())):
            self.assertEqual(jsinstall._deno_source(), (jsinstall.DENO_ZIP, SHA))

    def test_node_picks_win_x64_zip(self):
        with mock.patch.object(jsinstall, "_open", return_value=io.BytesIO(NODE_SUMS.encode())):
            url, sha = jsinstall._node_source()
        self.assertEqual(url, jsinstall.NODE_DIST + "node-v24.21.0-win-x64.zip")
        self.assertEqual(sha, SHA)

    def test_node_without_windows_zip(self):
        with mock.patch.object(jsinstall, "_open", return_value=io.BytesIO(b"x  y.tar.gz\n")):
            with self.assertRaises(ffinstall.InstallError):
                jsinstall._node_source()


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.js = os.path.join(self.tmp.name, "R-TubeUA", "js")
        self.patches = [mock.patch.object(jsinstall, "JS_DIR", self.js),
                        mock.patch.object(jsinstall, "applog", mock.MagicMock()),
                        mock.patch.object(jsinstall, "check_runtime")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_falls_back_to_node(self):
        def fake_open(url, timeout=60):
            if url.endswith(".sha256sum"):
                raise OSError("github.com заблоковано")
            return io.BytesIO(NODE_SUMS.encode())

        def fake_download(url, dest, expected, progress, label, cancel=None, what=""):
            self.assertEqual((url, expected), (jsinstall.NODE_DIST + "node-v24.21.0-win-x64.zip", SHA))
            write_zip(dest, ["node-v24.21.0-win-x64/node.exe", "node-v24.21.0-win-x64/npm.cmd"])

        with mock.patch.object(jsinstall, "_open", side_effect=fake_open), \
                mock.patch.object(jsinstall, "_download", side_effect=fake_download):
            path = jsinstall.install()
        self.assertEqual(path, os.path.join(self.js, "node.exe"))
        self.assertEqual(os.listdir(self.js), ["node.exe"])
        self.assertEqual(os.listdir(os.path.dirname(self.js)), ["js"])     # тимчасове прибрано

    def test_deno_first(self):
        def fake_download(url, dest, expected, progress, label, cancel=None, what=""):
            write_zip(dest, ["deno.exe"])

        with mock.patch.object(jsinstall, "_open", return_value=io.BytesIO(DENO_SUM.encode())), \
                mock.patch.object(jsinstall, "_download", side_effect=fake_download):
            self.assertEqual(jsinstall.install(), os.path.join(self.js, "deno.exe"))

    def test_all_sources_fail(self):
        with mock.patch.object(jsinstall, "_open", side_effect=OSError("немає мережі")):
            with self.assertRaises(ffinstall.InstallError) as ctx:
                jsinstall.install()
        self.assertIn("nodejs.org", str(ctx.exception))
        self.assertFalse(os.path.exists(self.js))

    def test_found_by_tools(self):
        from rtube import tools
        os.makedirs(self.js)
        open(os.path.join(self.js, "deno.exe"), "wb").close()
        with mock.patch.object(tools, "JS_DIR", self.js), \
                mock.patch.object(tools.shutil, "which", return_value=None):
            self.assertEqual(tools._runtime_paths(), [("deno", os.path.join(self.js, "deno.exe"))])


class NoJsErrorTest(unittest.TestCase):
    ERR = Exception("ERROR: [youtube] iHPrDirD9LI: This video is not available")

    def test_hint_to_install_runtime(self):
        with mock.patch.object(downloader.tools, "find_js_runtimes", return_value={}):
            self.assertEqual(downloader.humanize_error(self.ERR), downloader.NO_JS_ERROR)

    def test_with_runtime_really_unavailable(self):
        with mock.patch.object(downloader.tools, "find_js_runtimes",
                               return_value={"node": {"path": "node.exe"}}):
            self.assertIn("недоступне", downloader.humanize_error(self.ERR))
        # «Video unavailable» — справді видалене, рантайм тут ні до чого.
        with mock.patch.object(downloader.tools, "find_js_runtimes", return_value={}):
            self.assertIn("недоступне", downloader.humanize_error(
                Exception("ERROR: [youtube] x: Video unavailable. This video has been removed")))


if __name__ == "__main__":
    unittest.main()
