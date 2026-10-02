"""Логіка вибору форматів — на справжньому info ролика з ШІ-дубляжем.

Запуск: .venv\\Scripts\\python.exe -m unittest discover tests
"""

import copy
import json
import os
import unittest

from rtube import formats, tools

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "fixture_dubbed.json"), encoding="utf-8") as f:
    INFO = json.load(f)


class CleanUrlTest(unittest.TestCase):
    def test_variants(self):
        canon = "https://www.youtube.com/watch?v=1zElSYng0Xg"
        for raw in ("https://www.youtube.com/watch?v=1zElSYng0Xg&list=RDx&t=4s",
                    '"https://youtu.be/1zElSYng0Xg?si=abc"',
                    "youtube.com/shorts/1zElSYng0Xg",
                    "https://m.youtube.com/watch?feature=share&v=1zElSYng0Xg",
                    "1zElSYng0Xg"):
            self.assertEqual(tools.clean_url(raw), canon, raw)

    def test_other_sites_untouched(self):
        self.assertEqual(tools.clean_url("https://vimeo.com/123"), "https://vimeo.com/123")


class ChoicesTest(unittest.TestCase):
    def setUp(self):
        self.choices = formats.build_choices(INFO)

    def test_ukrainian_audio_is_default_even_if_ai(self):
        audio = self.choices.audios[self.choices.default_audio]
        self.assertEqual(audio.lang, "uk")
        self.assertIn("ШІ", audio.label)

    def test_original_detected(self):
        originals = [a for a in self.choices.audios if a.is_original]
        self.assertEqual([a.lang for a in originals], ["en-US"])

    def test_single_track_not_called_dub(self):
        # Як у «Килимок для миши "Морський"» (pn6mZ0Bcugo): одна доріжка «en»
        # з language_preference = -1 і без позначки original.
        info = copy.deepcopy(INFO)
        info["formats"] = [f for f in info["formats"]
                           if not formats.is_audio_only(f) or f["format_id"] in ("140-20", "251-20")]
        for f in info["formats"]:
            if formats.is_audio_only(f):
                f.update(language="en", language_preference=-1, format_note="medium")
        c = formats.build_choices(info)
        self.assertEqual([(a.label, a.is_original) for a in c.audios],
                         [("Англійська — єдина доріжка", True)])

    def test_drc_audio_only_as_fallback(self):
        info = copy.deepcopy(INFO)
        drc = dict(next(f for f in info["formats"] if f["format_id"] == "140-19"))
        drc.update(format_id="140-19-drc", abr=200)      # навіть «кращий» за бітрейтом
        info["formats"].append(drc)
        self.assertEqual(formats.pick_audio_format(info, "uk", "m4a")["format_id"], "140-19")

    def test_default_quality_1080_h264(self):
        self.assertEqual(self.choices.videos[self.choices.default_video].key, (1080, 30, "H.264"))

    def test_max_height_respected(self):
        c = formats.build_choices(INFO, max_height=720)
        self.assertEqual(c.videos[c.default_video].height, 720)

    def test_no_subs_when_ukrainian_audio(self):
        self.assertEqual(self.choices.subs[self.choices.default_sub].key, ())

    def test_auto_translation_not_default_but_offered(self):
        # Автопереклад YouTube зараз здебільшого віддає 429 — сам не вмикається.
        info = copy.deepcopy(INFO)
        info["formats"] = [f for f in info["formats"] if f.get("language") != "uk"]
        c = formats.build_choices(info)
        self.assertEqual(c.audios[c.default_audio].lang, "en-US")
        self.assertEqual(c.subs[c.default_sub].key, ())
        self.assertIn(("uk", True), [s.key for s in c.subs])

    def test_author_ukrainian_subs_default_when_no_ukrainian_audio(self):
        info = copy.deepcopy(INFO)
        info["formats"] = [f for f in info["formats"] if f.get("language") != "uk"]
        info["subtitles"] = {"uk": [{"ext": "vtt"}]}
        c = formats.build_choices(info)
        self.assertEqual(c.subs[c.default_sub].key, ("uk", False))

    def test_only_real_original_orig_subs(self):
        orig = [s.key[0] for s in self.choices.subs if s.key and s.key[0].endswith("-orig")]
        self.assertEqual(orig, ["en-orig"])

    def test_resolve_like_manual_cmd(self):
        # Те, що раніше підбиралося очима з `yt-dlp -F`: 137 (1080p mp4) + українська m4a.
        self.assertEqual(formats.resolve_format(INFO, (1080, 30, "H.264"), "uk", "mp4"),
                         "137+140-19")

    def test_resolve_with_original_in_mkv(self):
        fmt = formats.resolve_format(INFO, (1080, 30, "H.264"), "uk", "mkv", keep_original=True)
        video, uk, orig = fmt.split("+")
        langs = {f["format_id"]: f.get("language") for f in INFO["formats"]}
        self.assertEqual((video, langs[uk], langs[orig]), ("137", "uk", "en-US"))

    def test_audio_only(self):
        self.assertEqual(formats.resolve_format(INFO, (formats.AUDIO_ONLY,), "uk",
                                                audio_ext="m4a"), "140-19")

    def test_lost_codec_falls_back_to_same_height(self):
        self.assertEqual(formats.resolve_format(INFO, (1080, 30, "HEVC"), "uk").split("+")[0],
                         "137")

    def test_ukrainian_sort(self):
        names = [formats.lang_name(a.lang) for a in self.choices.audios[2:]]
        self.assertEqual(names, sorted(names, key=formats.sort_key))
        self.assertLess(names.index("Арабська"), names.index("Іврит"))


if __name__ == "__main__":
    unittest.main()
