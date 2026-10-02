"""Пошук JS-рантаймів: застарілі не передаються в yt-dlp."""

import unittest
from unittest import mock

from rtube import tools


class UrlListTest(unittest.TestCase):
    def test_several_links_with_text(self):
        text = ("Ось відео: https://www.youtube.com/watch?v=1zElSYng0Xg&t=4s,\n"
                "https://youtu.be/pn6mZ0Bcugo?si=x і ще (https://www.youtube.com/shorts/ZNMNKI4xPbY)\n"
                "повтор https://m.youtube.com/watch?v=1zElSYng0Xg "
                "плейлист https://www.youtube.com/playlist?list=PL1")
        self.assertEqual(tools.extract_video_urls(text), [
            "https://www.youtube.com/watch?v=1zElSYng0Xg",
            "https://www.youtube.com/watch?v=pn6mZ0Bcugo",
            "https://www.youtube.com/watch?v=ZNMNKI4xPbY"])

    def test_bare_id(self):
        self.assertEqual(tools.extract_video_urls("1zElSYng0Xg"),
                         ["https://www.youtube.com/watch?v=1zElSYng0Xg"])

    def test_collections(self):
        cases = {
            "https://www.youtube.com/playlist?list=PL123": "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/@Shop": "https://www.youtube.com/@Shop/videos",
            "youtube.com/@Shop/shorts": "https://www.youtube.com/@Shop/shorts",
            "https://www.youtube.com/channel/UCx/featured": "https://www.youtube.com/channel/UCx/videos",
            "https://www.youtube.com/watch?v=1zElSYng0Xg&list=PL123": None,
            "https://youtu.be/1zElSYng0Xg": None,
            "https://vimeo.com/123": None,
            "https://www.youtube.com/@a https://www.youtube.com/@b": None,
        }
        for text, expected in cases.items():
            self.assertEqual(tools.collection_url(text), expected, text)


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
