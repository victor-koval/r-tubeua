import unittest

from rtube import ftpcat

# Частина справжнього дерева video (жовтень 2026) — шляхи всередині розділу.
VIDEO = ftpcat.tree_from_paths([
    ["odyag_vzuttya_ta_aksesuari", "odyag"], ["odyag_vzuttya_ta_aksesuari", "prikrasi"],
    ["odyag_vzuttya_ta_aksesuari", "sumki_ta_aksesuari"], ["odyag_vzuttya_ta_aksesuari", "vzuttya"],
    ["podarunki_ta_tovari_dlya_svyat"],
    ["pobutova_tehnika", "dribna_pobutova_tehnika"], ["pobutova_tehnika", "klimatichna_tehnika"],
    ["krasa_ta_zdorovya", "apteka"], ["krasa_ta_zdorovya", "kosmetika_ta_parfumeriya"],
    ["instrumenti_j_obladnannya", "elektroinstrument"],
    ["instrumenti_j_obladnannya", "zapchastyny dlia elektro ta benzoinstrumentu"],
    ["instrumenti_j_obladnannya", "ruchnij_instrument"],
    ["rich_content", "80003", "12"], ["tegi"],
])


def product(ua, ru=(), slugs=(), mpath=()):
    return {"crumbs_ua": list(ua), "crumbs_ru": list(ru), "slugs": list(slugs),
            "mpath": [str(m) for m in mpath]}


HOODIE = product(["Одяг, взуття та аксесуари", "Одяг", "Одяг для чоловіків",
                  "Чоловічі кофти, худі, толстовки", "Чоловічі худі", "Чоловічі худі Без бренду"],
                 mpath=[1162030, 2033137, 1162070, 4637743, 4637959])
GIFTS = product(["Подарунки та сувеніри", "Антистреси", "Антистреси Без бренду"],
                mpath=[80260, 4627374])
BIJOU = product(["Одяг, взуття та аксесуари", "Сумки та аксесуари", "Біжутерія",
                 "Біжутерні браслети", "Біжутерні браслети LeBijou"],
                mpath=[1162030, 4630220, 4630370, 4657902])
TOOTHBRUSH = product(["Побутова техніка, інтер'єр", "Дрібна побутова техніка",
                      "Краса, здоров'я, догляд", "Електричні зубні щітки, іригатори та насадки"],
                     mpath=[80076, 80077, 435969, 437994])
TILES = product(["Інструменти й обладнання", "Інструменти для оздоблювальних робіт",
                 "Інструмент для плитки та скла"], mpath=[2577232, 4676128, 185340])


class NamesTest(unittest.TestCase):
    def test_translit_matches_folder_scheme(self):
        self.assertEqual(ftpcat.translit("Проєкційне обладнання"), "proyekcijne obladnannya")
        self.assertEqual(ftpcat.translit("Дитячі іграшки"), "dityachi igrashki")

    def test_skeleton_unifies_transliterations(self):
        self.assertEqual(ftpcat.skeleton("dlya"), ftpcat.skeleton("dlia"))
        self.assertEqual(ftpcat.skeleton("gospodarstvo"), ftpcat.skeleton("hospodarstvo"))
        self.assertEqual(ftpcat.skeleton("elektroniki"), ftpcat.skeleton("elektroniky"))

    def test_hoodie_goes_to_clothes(self):
        r = ftpcat.resolve(VIDEO, HOODIE)
        self.assertEqual(r.path, ("odyag_vzuttya_ta_aksesuari", "odyag"))
        self.assertEqual(r.source, ftpcat.NAMES)

    def test_root_level_with_partial_name(self):
        """«Подарунки та сувеніри» ~ podarunki_ta_tovari_dlya_svyat — лише перше слово,
        але з явним відривом від решти."""
        self.assertEqual(ftpcat.resolve(VIDEO, GIFTS).path, ("podarunki_ta_tovari_dlya_svyat",))

    def test_upper_levels_win_ties(self):
        """«Побутова техніка → … → Краса, здоров'я, догляд» — у побутову техніку, а не в красу."""
        self.assertEqual(ftpcat.resolve(VIDEO, TOOTHBRUSH).path,
                         ("pobutova_tehnika", "dribna_pobutova_tehnika"))

    def test_weak_word_is_not_enough(self):
        """«Інструмент для плитки» не має йти в запчастини до бензоінструменту —
        лише в батьківську теку інструментів."""
        self.assertEqual(ftpcat.resolve(VIDEO, TILES).path, ("instrumenti_j_obladnannya",))

    def test_never_section_root(self):
        unknown = product(["Щось зовсім інше", "Невідома категорія"], mpath=[1, 2])
        r = ftpcat.resolve(VIDEO, unknown)
        self.assertEqual(r.path, ())
        self.assertIsNone(r.source)

    def test_service_folders_skipped(self):
        tree = ftpcat.tree_from_paths([["rich_content", "80003"], ["tegi"]])
        self.assertEqual(ftpcat.resolve(tree, product(["Теги", "Rich content"])).path, ())


class KnowledgeTest(unittest.TestCase):
    def test_rule_beats_names(self):
        rules = {"4630370": ["odyag_vzuttya_ta_aksesuari", "prikrasi"]}       # Біжутерія
        r = ftpcat.resolve(VIDEO, BIJOU, rules=rules)
        self.assertEqual((r.path, r.source), (("odyag_vzuttya_ta_aksesuari", "prikrasi"),
                                              ftpcat.RULE))

    def test_rule_for_missing_folder_ignored(self):
        rules = {"4630370": ["vydalena_teka"]}
        self.assertEqual(ftpcat.resolve(VIDEO, BIJOU, rules=rules).source, ftpcat.NAMES)

    def test_history_learns_convention(self):
        """Люди клали біжутерію в prikrasi, хоч на сайті вона в «Сумки та аксесуари»."""
        index = {}
        for _ in range(3):
            ftpcat.add_to_index(index, ["1162030", "4630220", "4630370", "4657999"],
                                ("odyag_vzuttya_ta_aksesuari", "prikrasi"))
        r = ftpcat.resolve(VIDEO, BIJOU, index=index)
        self.assertEqual((r.path, r.source), (("odyag_vzuttya_ta_aksesuari", "prikrasi"),
                                              ftpcat.HISTORY))

    def test_history_needs_majority_and_second_level(self):
        index = {}
        # Збіг лише на першому рівні сайту — замало, щоб довіряти.
        ftpcat.add_to_index(index, ["1162030", "999"], ("odyag_vzuttya_ta_aksesuari", "vzuttya"))
        self.assertEqual(ftpcat.resolve(VIDEO, HOODIE, index=index).source, ftpcat.NAMES)
        # Без більшості голосів — теж ні.
        for folder in (("odyag_vzuttya_ta_aksesuari", "vzuttya"),
                       ("odyag_vzuttya_ta_aksesuari", "prikrasi"),
                       ("odyag_vzuttya_ta_aksesuari", "odyag")):
            ftpcat.add_to_index(index, ["1162030", "2033137", "5"], folder)
        self.assertIsNone(ftpcat.history_lookup(index, HOODIE["mpath"], VIDEO))

    def test_history_skips_root_and_vanished_folders(self):
        index = {"2033137": {"": 5, "nema_takoyi": 9, "odyag_vzuttya_ta_aksesuari/odyag": 1}}
        self.assertEqual(ftpcat.history_lookup(index, HOODIE["mpath"], VIDEO)[0],
                         ("odyag_vzuttya_ta_aksesuari", "odyag"))

    def test_folder_exists(self):
        self.assertFalse(ftpcat.folder_exists(VIDEO, ()))
        self.assertFalse(ftpcat.folder_exists(VIDEO, ("rich_content", "80003")))
        self.assertFalse(ftpcat.folder_exists(VIDEO, ("tegi",)))
        self.assertTrue(ftpcat.folder_exists(VIDEO, ("krasa_ta_zdorovya", "apteka")))


if __name__ == "__main__":
    unittest.main()
