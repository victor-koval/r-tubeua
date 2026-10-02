"""Пошук JS-рантаймів: застарілі не передаються в yt-dlp."""

import unittest
from unittest import mock

from rtube import tools


class JsRuntimeTest(unittest.TestCase):
    def probe(self, infos):
        paths = [(name, fr"C:\{name}.exe") for name in infos]
        with mock.patch.object(tools, "_runtime_paths", return_value=paths), \
                mock.patch.object(tools, "_runtime_info", side_effect=lambda n, p: infos[n]):
            return tools.find_js_runtimes(), tools.probe_js_runtimes()

    def test_outdated_node_excluded(self):
        usable, probed = self.probe({"node": ("20.18.0", False)})
        self.assertEqual(usable, {})
        self.assertEqual(probed, [("node", r"C:\node.exe", "20.18.0", False)])

    def test_supported_passed_with_path(self):
        usable, _ = self.probe({"node": ("24.16.0", True), "deno": ("1.40.0", False)})
        self.assertEqual(usable, {"node": {"path": r"C:\node.exe"}})


if __name__ == "__main__":
    unittest.main()
