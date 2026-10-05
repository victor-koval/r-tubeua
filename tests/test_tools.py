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


class TranslitTest(unittest.TestCase):
    def test_example_from_video(self):
        # Дослівно з «Інструменти → Транслітерація» утиліти, якою готують відео до FTP.
        self.assertEqual(
            tools.translit_name("Навушники Gelius MIAOSpace GP HP-009 White/Yellow (2099901012913)"),
            "navushnyky_gelius_miaospace_gp_hp_009_white_yellow_2099901012913")

    def test_kmu_2010_rules(self):
        cases = {
            "Єнакієве": "yenakiieve", "Їжакевич": "yizhakevych", "Йосипівка": "yosypivka",
            "Юрій": "yurii", "Яготин": "yahotyn", "Згорани": "zghorany",
            "м'ясо Знам’янка": "miaso_znamianka", "Щастя Ґанок": "shchastia_ganok",
            'Килимок для миши "Морський"': "kylymok_dlia_myshy_morskyi",
            "Нужные Вещи — эхо съёмка": "nuzhnye_veshchy_ekho_siomka",
            "#іграшки!!!": "ihrashky", "": "",
        }
        for text, expected in cases.items():
            self.assertEqual(tools.translit_name(text), expected, text)

    def test_only_safe_chars_and_limit(self):
        name = tools.translit_name("Дуже " * 60 + "довга назва", limit=50)
        self.assertLessEqual(len(name), 50)
        self.assertRegex(name, r"^[a-z0-9_]+$")
        self.assertFalse(name.endswith("_"))


class IdPairsTest(unittest.TestCase):
    def test_lines_like_in_video(self):
        text = ("580250272 https://youtube.com/shorts/IAlRuoMApic?si=glwKx_Tezs8zDeT9 ;\n"
                "610122062 https://youtube.com/shorts/FTBLOAo6tHU?si=0xFBemISn6xyD52y ;\n\n"
                "https://youtu.be/pn6mZ0Bcugo\t593505175\n"                 # з Excel, ID після
                "512333429;https://www.youtube.com/watch?v=1zElSYng0Xg&t=4s\n"
                "https://www.youtube.com/watch?v=ZNMNKI4xPbY\n")            # без ID
        self.assertEqual(tools.extract_id_pairs(text), [
            ("580250272", "https://www.youtube.com/watch?v=IAlRuoMApic"),
            ("610122062", "https://www.youtube.com/watch?v=FTBLOAo6tHU"),
            ("593505175", "https://www.youtube.com/watch?v=pn6mZ0Bcugo"),
            ("512333429", "https://www.youtube.com/watch?v=1zElSYng0Xg"),
            (None, "https://www.youtube.com/watch?v=ZNMNKI4xPbY")])

    def test_numbers_inside_url_are_not_ids(self):
        self.assertEqual(tools.extract_id_pairs("https://youtu.be/pn6mZ0Bcugo?t=123456"),
                         [(None, "https://www.youtube.com/watch?v=pn6mZ0Bcugo")])


class MinSecTest(unittest.TestCase):
    def test_format(self):
        from rtube import uikit
        cases = {207: "3,27", 65: "1,05", 59.6: "1,00", 0: "0,00", None: "0,00",
                 4503: "75,03", 3600: "60,00"}
        for seconds, expected in cases.items():
            self.assertEqual(uikit.format_min_sec(seconds), expected, seconds)


if __name__ == "__main__":
    unittest.main()
