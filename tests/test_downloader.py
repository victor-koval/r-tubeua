"""Чисті функції завантажувача на справжньому info ролика з ШІ-дубляжем."""

import json
import os
import unittest

from rtube import downloader

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "fixture_dubbed.json"), encoding="utf-8") as f:
    INFO = json.load(f)


def pairs(args):
    return list(zip(args[::2], args[1::2]))


class TrackMetadataTest(unittest.TestCase):
    def test_chosen_first_original_second(self):
        args = pairs(downloader.track_metadata_args(INFO, "137+140-19+140-20"))
        self.assertIn(("-metadata:s:a:0", "language=ukr"), args)
        self.assertIn(("-metadata:s:a:1", "language=eng"), args)
        self.assertIn(("-disposition:a:0", "default"), args)
        self.assertIn(("-disposition:a:1", "0"), args)
        titles = [v for k, v in args if k == "-metadata:s:a:0" and v.startswith("handler_name=")]
        self.assertEqual(titles, ["handler_name=Українська — ШІ-дубляж YouTube"])

    def test_video_stream_not_labelled(self):
        args = downloader.track_metadata_args(INFO, "137+140-19")
        self.assertFalse(any(a.startswith("-metadata:s:a:1") for a in args))

    def test_three_letter_code_kept(self):
        info = {"formats": [{"format_id": "a", "vcodec": "none", "acodec": "opus",
                             "language": "fil", "format_note": "Filipino"}]}
        self.assertIn("language=fil", downloader.track_metadata_args(info, "a"))


class DiskTest(unittest.TestCase):
    def test_needed_bytes(self):
        self.assertEqual(downloader.needed_bytes([1000, 0]), 2100)
        self.assertEqual(downloader.needed_bytes([]), 0)


if __name__ == "__main__":
    unittest.main()
