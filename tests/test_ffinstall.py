"""Встановлення ffmpeg без мережі: суми, розпакування, запасне джерело."""

import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from rtube import downloader, ffinstall

SHA = "a" * 64


def write_zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"MZ fake " + name.encode())


class ChecksumTest(unittest.TestCase):
    def test_single_hash_file(self):  # gyan.dev: лише сума
        self.assertEqual(ffinstall.parse_checksum(SHA.upper() + "\n"), SHA)

    def test_listing(self):  # yt-dlp/FFmpeg-Builds: «сума  ім'я» на кожен архів
        text = f"{'b' * 64}  ffmpeg-master-latest-win64-gpl-shared.zip\n{SHA}  ffmpeg-master-latest-win64-gpl.zip\n"
        self.assertEqual(ffinstall.parse_checksum(text, "ffmpeg-master-latest-win64-gpl.zip"), SHA)
        with self.assertRaises(ffinstall.InstallError):
            ffinstall.parse_checksum(text, "інший.zip")


class ExtractTest(unittest.TestCase):
    def test_finds_exes_in_any_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "a.zip")
            write_zip(archive, ["ffmpeg-8.0-essentials_build/bin/ffmpeg.exe",
                                "ffmpeg-8.0-essentials_build/bin/ffprobe.exe",
                                "ffmpeg-8.0-essentials_build/bin/ffplay.exe",
                                "ffmpeg-8.0-essentials_build/doc/ffmpeg.html"])
            out = os.path.join(tmp, "bin")
            os.makedirs(out)
            found = ffinstall.extract_exes(archive, out)
            self.assertEqual(sorted(os.listdir(out)), ["ffmpeg.exe", "ffprobe.exe"])
            self.assertEqual(sorted(found), ["ffmpeg.exe", "ffprobe.exe"])

    def test_missing_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "a.zip")
            write_zip(archive, ["x/bin/ffprobe.exe"])
            with self.assertRaises(ffinstall.InstallError):
                ffinstall.extract_exes(archive, tmp)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = os.path.join(self.tmp.name, "R-TubeUA", "bin")
        self.patches = [mock.patch.object(ffinstall, "BIN_DIR", self.bin),
                        mock.patch.object(ffinstall, "applog", mock.MagicMock()),
                        mock.patch.object(ffinstall, "check_ffmpeg")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_falls_back_to_second_source(self):
        first, second = ffinstall.SOURCES[0][2], ffinstall.SOURCES[1][2]

        def fake_open(url, timeout=60):
            if url == first:
                raise OSError("gyan.dev заблоковано")
            self.assertEqual(url, second)
            return io.BytesIO(f"{SHA}  {ffinstall.SOURCES[1][3]}\n".encode())

        def fake_download(url, dest, expected, progress, label):
            self.assertEqual(expected, SHA)
            write_zip(dest, ["ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe",
                             "ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe"])

        with mock.patch.object(ffinstall, "_open", side_effect=fake_open), \
                mock.patch.object(ffinstall, "_download", side_effect=fake_download):
            path = ffinstall.install()
        self.assertEqual(path, os.path.join(self.bin, "ffmpeg.exe"))
        self.assertEqual(sorted(os.listdir(self.bin)), ["ffmpeg.exe", "ffprobe.exe"])
        # Тимчасові теки прибрано — поруч із bin нічого зайвого.
        self.assertEqual(os.listdir(os.path.dirname(self.bin)), ["bin"])

    def test_all_sources_fail(self):
        with mock.patch.object(ffinstall, "_open", side_effect=OSError("немає мережі")):
            with self.assertRaises(ffinstall.InstallError) as ctx:
                ffinstall.install()
        self.assertIn("gyan.dev", str(ctx.exception))
        self.assertFalse(os.path.exists(self.bin))


class NeedsFfmpegTest(unittest.TestCase):
    def job(self, video_key, container, sub_key=()):
        return downloader.Job(url="u", title="t", info={}, video_key=video_key, audio_lang="uk",
                              audio_label="", out_dir=".", container=container, sub_key=sub_key)

    def test_rules(self):
        self.assertTrue(downloader.needs_ffmpeg(self.job((1080, 30, "H.264"), "mp4")))
        self.assertFalse(downloader.needs_ffmpeg(self.job(("audio",), "m4a")))
        self.assertTrue(downloader.needs_ffmpeg(self.job(("audio",), "mp3")))
        self.assertFalse(downloader.needs_ffmpeg(self.job((360, 30, "H.264"), "mp4"), progressive=True))
        self.assertTrue(downloader.needs_ffmpeg(self.job((360, 30, "H.264"), "mp4", ("uk", True)),
                                                progressive=True))


if __name__ == "__main__":
    unittest.main()
