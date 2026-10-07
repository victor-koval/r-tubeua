"""Куди на FTP класти відео товару: розділ (video, video2…) і тека категорії в ньому.

Чиста логіка, без мережі: дерево тек, хлібні крихти товару (rozetka.py) і
накопичене знання — ручні вибори й те, куди такі товари вже клали люди.

Порядок: ручне правило → історія розділу → назви тек → «потрібен вибір».
Корінь розділу (video/, video2/…) не повертається ніколи — лише теки на
глибині ≥ 1 всередині нього.

Зіставлення назв перевірене на 8,5 тис. файлах, які люди розклали в video:
92 % збігів на реальному потоці (з одним правилом «Біжутерія → prikrasi»).
"""

import difflib
import re
from dataclasses import dataclass

# Не категорії: службові теки, «багатий контент» з номерами категорій, теги.
SKIP = {"rich_content", "tegi", "lost+found", ".snap", "drivers", "drajvera", "manuals",
        "other_content", "pdf", "price", "scripts"}
THRESHOLD = 0.62
ROOT_THRESHOLD = 0.45   # перший рівень: «Подарунки та сувеніри» ~ podarunki_ta_tovari_dlya_svyat
ROOT_MARGIN = 0.15      # … але лише з явним відривом від другого кандидата
LEVEL_PENALTY = 0.03    # за кожен пропущений рівень сайту: за рівних — ближчий до кореня
STRONG_WORD = 0.9       # без жодного такого збігу слова тека не підходить
# Історія: тека, куди найчастіше клали товари тієї ж категорії, — лише з
# більшістю голосів і щонайменше з другого рівня категорій (не «весь розділ сайту»).
HISTORY_SHARE = 0.5
HISTORY_MIN_LEVEL = 2
# Синоніми, яких транслітерація не впіймає: назва на сайті → як зветься тека.
SYNONYMS = {"біжутерія": "прикраси", "бижутерия": "украшения"}

RULE, HISTORY, NAMES = "rule", "history", "names"
SOURCE_LABELS = {RULE: "ваш вибір", HISTORY: "як раніше", NAMES: "за назвою", None: "—"}


# ── дерево тек ─────────────────────────────────────────────────────────
def tree_from_paths(paths):
    """[[тека], [тека, підтека], …] (шляхи всередині розділу) → {шлях: [діти]}."""
    tree = {(): []}
    for path in sorted(tuple(p) for p in paths if p):
        for depth in range(1, len(path) + 1):
            node = path[:depth]
            if node not in tree:
                tree[node] = []
                tree[node[:-1]].append(node[-1])
    return tree


def usable(name):
    return name not in SKIP and not name.isdigit()


def folder_exists(tree, path):
    return bool(path) and tuple(path) in tree and all(usable(p) for p in path)


# ── нормалізація назв ──────────────────────────────────────────────────
_LETTERS = dict(zip("абвгґдезиіїйклмнопрстуфхцчшщьюяєыэёъ",
                    ["a", "b", "v", "g", "g", "d", "e", "z", "i", "i", "yi", "j", "k", "l", "m",
                     "n", "o", "p", "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "",
                     "yu", "ya", "ye", "y", "e", "e", ""]))
_LETTERS["ж"] = "zh"
STOP = {"ta", "i", "j", "y", "do", "dla", "z", "na", "v", "u", "ot", "po", "a", "the", "and",
        "for", "of", "bez", "brendu", "brenda"}


def translit(text):
    """Транслітерація, якою названо теки на FTP: «Проєкційне» → «proyekcijne»."""
    text = text.lower().replace("'", "").replace("’", "").replace("ʼ", "")
    return "".join(_LETTERS.get(ch, ch) for ch in text)


def skeleton(word):
    """Зводить різні транслітерації до одного вигляду: dlya/dlia,
    gospodarstvo/hospodarstvo, elektroniki/elektroniky, priladdya/pryladdia."""
    w = word
    for a, b in (("shch", "s"), ("sch", "s"), ("kh", "h"), ("zh", "z"), ("ch", "c"), ("sh", "s"),
                 ("ts", "c"), ("ya", "a"), ("ia", "a"), ("yu", "u"), ("iu", "u"), ("ye", "e"),
                 ("ie", "e"), ("yi", "i"), ("yo", "o")):
        w = w.replace(a, b)
    w = w.replace("g", "h").replace("y", "i").replace("j", "i").replace("x", "ks")
    return re.sub(r"(.)\1+", r"\1", w)


def tokens(text):
    words = re.split(r"[^a-z0-9]+", translit(text))
    return [skeleton(w) for w in words if w and w not in STOP]


def word_sim(a, b):
    if a == b:
        return 1.0
    n = min(len(a), len(b), 5)
    if n >= 4 and a[:n] == b[:n]:
        return 0.9                          # той самий корінь, різні закінчення
    return difflib.SequenceMatcher(None, a, b).ratio()


def score(folder, crumb):
    """Наскільки назва теки відповідає назві категорії (0…1). Без жодного сильного
    збігу слова — 0: «instrument» не робить «Інструмент для плитки» запчастинами
    до бензоінструменту."""
    low = crumb.lower()
    for word, alias in SYNONYMS.items():
        if word in low:
            crumb += " " + alias
    f, c = tokens(folder), tokens(crumb)
    if not f or not c:
        return 0.0
    sims = [max(word_sim(x, y) for y in c) for x in f]
    if max(sims) < STRONG_WORD:
        return 0.0
    f2c = sum(sims) / len(f)
    c2f = sum(max(word_sim(y, x) for x in f) for y in c) / len(c)
    return 0.7 * f2c + 0.3 * c2f


# ── товар ──────────────────────────────────────────────────────────────
def levels_of(product):
    """Рівні сайту для порівняння з теками: на кожному — українська й російська
    назви та англійське слово з адреси категорії (для старої схеми video2)."""
    ua = product.get("crumbs_ua") or []
    ru = product.get("crumbs_ru") or []
    slugs = product.get("slugs") or []
    levels = []
    for i, title in enumerate(ua):
        texts = [title]
        if i < len(ru) and ru[i]:
            texts.append(ru[i])
        if i < len(slugs) and slugs[i]:
            texts.append(slugs[i])
        levels.append(texts)
    return levels


def choose(tree, levels):
    """Спуск від кореня розділу: на кожному рівні — підтека, найсхожіша на будь-який
    із ще не використаних рівнів сайту. Повертає (шлях, впевненість)."""
    path, start, steps = (), 0, []
    while True:
        scored = []
        for child in tree.get(path, []):
            if not usable(child):
                continue
            top = (0.0, None)
            for i in range(start, len(levels)):
                s = max(score(child, t) for t in levels[i] if t) - LEVEL_PENALTY * (i - start)
                if s > top[0]:
                    top = (s, i)
            if top[1] is not None:
                scored.append((top[0], child, top[1]))
        scored.sort(reverse=True)
        best = scored[0] if scored else (0.0, None, None)
        second = scored[1][0] if len(scored) > 1 else 0.0
        accept = best[1] is not None and (
            best[0] >= THRESHOLD or
            (not path and best[0] >= ROOT_THRESHOLD and best[0] - second >= ROOT_MARGIN))
        if not accept:
            break
        path += (best[1],)
        start = best[2] + 1
        steps.append(best[0])
    return path, (min(steps) if steps else 0.0)


def rule_lookup(rules, mpath, tree):
    """Ручний вибір для найглибшої категорії товару (або її батьків), якщо така тека є."""
    for cat in reversed(mpath or []):
        path = rules.get(str(cat))
        if path and folder_exists(tree, path):
            return tuple(path)
    return None


def history_lookup(index, mpath, tree):
    """Тека, куди найчастіше клали товари найближчої спільної категорії:
    index — {категорія: {"тека/підтека": кількість}} для цього розділу.
    Повертає (шлях, частка голосів) або None."""
    for level in range(len(mpath or []), HISTORY_MIN_LEVEL - 1, -1):
        votes = index.get(str(mpath[level - 1])) or {}
        votes = {k: v for k, v in votes.items() if folder_exists(tree, k.split("/"))}
        total = sum(votes.values())
        if not total:
            continue
        folder, count = max(votes.items(), key=lambda kv: kv[1])
        if count / total >= HISTORY_SHARE:
            return tuple(folder.split("/")), count / total
    return None


@dataclass
class Resolution:
    path: tuple             # шлях усередині розділу; () — потрібен вибір
    source: str = None      # RULE / HISTORY / NAMES / None
    confidence: float = 0.0


def resolve(tree, product, index=None, rules=None):
    """Тека для товару в одному розділі."""
    mpath = product.get("mpath") or []
    path = rule_lookup(rules or {}, mpath, tree)
    if path:
        return Resolution(path, RULE, 1.0)
    found = history_lookup(index or {}, mpath, tree)
    if found:
        return Resolution(found[0], HISTORY, found[1])
    path, confidence = choose(tree, levels_of(product))
    if path:
        return Resolution(path, NAMES, confidence)
    return Resolution(())


def add_to_index(index, mpath, folder):
    """Товар із цими категоріями лежить у теці folder (шлях усередині розділу)."""
    key = "/".join(folder)
    for cat in mpath or []:
        votes = index.setdefault(str(cat), {})
        votes[key] = votes.get(key, 0) + 1
