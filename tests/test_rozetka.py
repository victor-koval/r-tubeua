import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from rtube import rozetka

UA = {"data": {"title": "Худі", "mpath": ".1162030.2033137.4637959.",
               "breadcrumbs": [
                   {"title": "Одяг, взуття та аксесуари",
                    "href": "https://rozetka.com.ua/ua/shoes_clothes/c1162030/"},
                   {"title": "Одяг", "href": "https://rozetka.com.ua/ua/clothes/c2033137/"},
                   {"title": "Чоловічі худі", "href": "https://rozetka.com.ua/ua/mugskie-hudi/c4637959/"}]}}
RU = {"data": {"breadcrumbs": [{"title": "Одежда, обувь и аксессуары"}, {"title": "Одежда"},
                               {"title": "Мужские худи"}]}}


class ParseTest(unittest.TestCase):
    def test_parse(self):
        info = rozetka.parse(UA, RU)
        self.assertEqual(info["mpath"], ["1162030", "2033137", "4637959"])
        self.assertEqual(info["crumbs_ru"][1], "Одежда")
        self.assertEqual(info["slugs"], ["shoes clothes", "clothes", "mugskie hudi"])

    def test_slug(self):
        self.assertEqual(rozetka.slug("https://rozetka.com.ua/ua/headphones/c80027/"), "headphones")
        self.assertEqual(rozetka.slug("https://bt.rozetka.com.ua/ua/"), "")

    def test_mpath_from_hrefs_when_missing(self):
        data = json.loads(json.dumps(UA))
        data["data"]["mpath"] = ""
        self.assertEqual(rozetka.parse(data)["mpath"], ["1162030", "2033137", "4637959"])

    def test_no_crumbs(self):
        self.assertIsNone(rozetka.parse({"data": {}}))


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(rozetka, "CACHE_PATH", os.path.join(self.tmp.name, "p.json")),
                        mock.patch.object(rozetka, "CONFIG_DIR", self.tmp.name),
                        mock.patch.object(rozetka, "_cache", None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_fetch_once_then_cache(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return UA if "lang=ua" in url else RU

        self.assertEqual(rozetka.product_info("570377014", fetch)["crumbs_ru"][2], "Мужские худи")
        self.assertEqual(rozetka.product_info("570377014", fetch)["mpath"][0], "1162030")
        self.assertEqual(len(calls), 2)                     # ua + ru, далі — з кешу
        rozetka._cache = None                               # «перезапуск» — кеш з файлу
        rozetka.product_info("570377014", fetch)
        self.assertEqual(len(calls), 2)

    def test_missing_product_cached_shortly(self):
        def fetch(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"success":false}'))

        self.assertIsNone(rozetka.product_info("1", fetch))
        with mock.patch.object(rozetka, "_get_json", side_effect=AssertionError("знову в мережу")):
            self.assertIsNone(rozetka.product_info("1", rozetka._get_json))

    def test_network_error_not_cached(self):
        self.assertIsNone(rozetka.product_info("5", mock.Mock(side_effect=OSError("offline"))))
        self.assertIsNotNone(rozetka.product_info("5", lambda url: UA if "lang=ua" in url else RU))

    def test_bad_id(self):
        self.assertIsNone(rozetka.product_info("abc", mock.Mock(side_effect=AssertionError)))


if __name__ == "__main__":
    unittest.main()
