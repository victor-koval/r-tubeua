"""Зовнішні програми й дрібні утиліти: ffmpeg, JS-рантайм, чистка посилань."""

import functools
import glob
import os
import re
import shutil
import sys
from urllib.parse import parse_qs, urlparse

from .settings import CONFIG_DIR

# Куди ffmpeg ставить сама програма (див. ffinstall.py).
FFMPEG_DIR = os.path.join(CONFIG_DIR, "bin")

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


def clean_url(text):
    """Зводить будь-яке посилання на ролик до «чистого» watch?v=ID.

    З cmd доводилося вручну відрізати &list=, &t=, &si= тощо: із &list=
    yt-dlp береться за весь плейлист. Тут те саме робиться автоматично,
    а голий 11-символьний ID теж приймається.
    """
    text = (text or "").strip().strip('"').strip("'").strip()
    if not text:
        return ""
    if _VIDEO_ID.match(text):
        return f"https://www.youtube.com/watch?v={text}"
    if not re.match(r"^[a-z]+://", text, re.I):
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return text
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _YT_HOSTS):
        return text

    video_id = ""
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    else:
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v", "e"):
                video_id = parts[1]
    if _VIDEO_ID.match(video_id or ""):
        return f"https://www.youtube.com/watch?v={video_id}"
    return text


_URL_TOKEN = re.compile(r"(?:https?://|www\.|m\.|youtu\.be/|youtube\.com/)\S+", re.I)
_CHANNEL_TABS = ("videos", "shorts", "streams")


def _is_youtube(host):
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in _YT_HOSTS)


def collection_url(text):
    """Посилання на плейлист або канал → канонічне посилання, інакше None.

    Канал без вкладки (youtube.com/@назва) yt-dlp віддає як список вкладок
    («Відео», «Shorts»…), а не роликів, тож додаємо /videos. Посилання на ролик
    у плейлисті (watch?v=…&list=…) — це ролик, а не плейлист.
    """
    text = (text or "").strip().strip('"').strip("'").strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    if not re.match(r"^[a-z]+://", text, re.I):
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if not _is_youtube(parsed.hostname) or parsed.hostname.lower().endswith("youtu.be"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if parts[:1] == ["playlist"]:
        list_id = (parse_qs(parsed.query).get("list") or [""])[0]
        return f"https://www.youtube.com/playlist?list={list_id}" if list_id else None
    if parts and (parts[0].startswith("@") or (parts[0] in ("channel", "c", "user") and len(parts) > 1)):
        base = parts[:1] if parts[0].startswith("@") else parts[:2]
        rest = parts[len(base):]
        tab = rest[0] if rest and rest[0] in _CHANNEL_TABS else "videos"
        return "https://www.youtube.com/" + "/".join(base + [tab])
    return None


def extract_video_urls(text):
    """Усі посилання на ролики з тексту (кілька рядків, через пробіл, разом
    із підписами) — канонічні й без повторів. Плейлисти й канали не входять:
    їх розгортає expand_collection."""
    text = (text or "").strip()
    tokens = _URL_TOKEN.findall(text)
    if not tokens and _VIDEO_ID.match(text):
        tokens = [text]
    urls = []
    for token in tokens:
        token = token.rstrip(".,;)]}>»\"'")
        if collection_url(token):
            continue
        url = clean_url(token)
        if "watch?v=" in url and url not in urls:
            urls.append(url)
    return urls


_ID_IN_LINE = re.compile(r"(?<!\d)\d{5,12}(?!\d)")


def extract_id_pairs(text):
    """Рядки «ID_товару посилання» → [(ID або None, посилання), …].

    Формат — як у списках, з якими працюють на заливанні відео:
    «580250272 https://youtube.com/shorts/…?si=… ;». ID може стояти й після
    посилання, роздільник — пробіл, таб (рядок з Excel) або «;». Число всередині
    самого посилання (?si=…, ID ролика) за ID товару не вважається.
    """
    pairs = []
    for line in (text or "").splitlines():
        tokens = _URL_TOKEN.findall(line)
        if not tokens:
            continue
        rest = line
        for token in tokens:
            rest = rest.replace(token, " ")
        ids = _ID_IN_LINE.findall(rest)
        product_id = ids[0] if ids else None
        for token in tokens:
            token = token.rstrip(".,;)]}>»\"'")
            if collection_url(token):
                continue
            url = clean_url(token)
            if "watch?v=" in url:
                pairs.append((product_id, url))
    return pairs


def short_url(url):
    """https://www.youtube.com/watch?v=pn6mZ0Bcugo → youtu.be/pn6mZ0Bcugo — для таблиць
    і рядків, поки справжньої назви ролика ще немає."""
    parsed = urlparse(url)
    video_id = (parse_qs(parsed.query).get("v") or [""])[0]
    if video_id:
        return f"youtu.be/{video_id}"
    return (parsed.netloc + parsed.path) or url


def count_lines_without_links(text):
    """Скільки непорожніх рядків тексту без жодного посилання (шапка таблиці,
    рядок лише з ID…) — щоб сказати, що їх пропущено, а не мовчки загубити."""
    return sum(1 for line in (text or "").splitlines()
               if line.strip(" 	;,") and not _URL_TOKEN.findall(line))


# ── транслітерація для імен файлів ──────────────────────────────────────
# Офіційна українська транслітерація (постанова КМУ № 55 від 2010 р.) — як у
# «Інструменти → Транслітерація» утиліти, якою готують відео до FTP. Плюс
# російські літери: назви на YouTube бувають і російською.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "y", "і": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
    "ы": "y", "э": "e", "ё": "io", "ъ": "",
}
# На початку слова — одне, усередині — інше (Єнакієве → Yenakiieve).
_TRANSLIT_START = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}
_TRANSLIT_INNER = {"є": "ie", "ї": "i", "й": "i", "ю": "iu", "я": "ia"}
_APOSTROPHES = "'’ʼ`"


def translit_name(text, limit=120):
    """«Навушники Gelius HP-009 White/Yellow (2099901012913)» →
    «navushnyky_gelius_hp_009_white_yellow_2099901012913».

    Лише a-z, 0-9 і «_»: так файли називають для заливання на FTP.
    """
    import unicodedata
    src = (text or "").lower()
    out = []
    in_word = False
    i = 0
    while i < len(src):
        ch = src[i]
        if ch == "з" and src[i + 1:i + 2] == "г":
            out.append("zgh")          # «зг» — окремо, щоб не читалось як «zh»
            i += 2
            in_word = True
            continue
        if ch in _TRANSLIT_START:
            out.append(_TRANSLIT_INNER[ch] if in_word else _TRANSLIT_START[ch])
        elif ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch in _APOSTROPHES:
            pass                        # м'ясо → miaso: апостроф просто зникає
        else:
            plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(plain if plain else " ")
        in_word = ch.isalpha() or (ch in _APOSTROPHES and in_word)
        i += 1
    name = re.sub(r"[^a-z0-9]+", "_", "".join(out)).strip("_")
    if len(name) > limit:
        cut = name[:limit]
        # Обрізаємо по межі слова, якщо вона не надто далеко.
        name = cut.rsplit("_", 1)[0] if "_" in cut[limit // 2:] else cut
    return name.strip("_")


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_ffmpeg():
    """Шлях до ffmpeg.exe або None.

    Без ffmpeg yt-dlp не зможе склеїти відео з окремою доріжкою — а саме так
    YouTube віддає все вище 360p. Шукаємо: поруч із програмою, там, куди його
    ставить сама програма (ffinstall.py), у PATH, у теці пакета winget (його
    PATH підхоплюється не всюди, доки не перезайти в систему), у Scoop і
    Chocolatey.
    """
    for local in (os.path.join(_app_dir(), "ffmpeg.exe"), os.path.join(FFMPEG_DIR, "ffmpeg.exe")):
        if os.path.isfile(local):
            return local
    found = shutil.which("ffmpeg")
    if found:
        return found
    winget = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    for pattern in ("*FFmpeg*/**/bin/ffmpeg.exe", "*ffmpeg*/**/ffmpeg.exe"):
        hits = sorted(glob.glob(os.path.join(winget, pattern), recursive=True), reverse=True)
        if hits:
            return hits[0]
    for path in (os.path.join(os.path.expanduser("~"), "scoop", "shims", "ffmpeg.exe"),
                 os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                              "chocolatey", "bin", "ffmpeg.exe"),
                 r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"):
        if os.path.isfile(path):
            return path
    return None


def find_ffprobe():
    """ffprobe лежить поруч із ffmpeg у всіх збірках, які ми знаходимо або ставимо."""
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        sibling = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.isfile(sibling):
            return sibling
    return shutil.which("ffprobe")


def find_js_runtimes():
    """JS-рантайми для yt-dlp у форматі параметра js_runtimes.

    Дубляжі (зокрема ШІ-озвучка українською) надійно приходять через
    web-клієнт, якому yt-dlp мусить розв'язати JS-челендж. Без рантайму
    yt-dlp 2026.08 поки що бере їх через інший клієнт, але сам називає цей
    шлях застарілим. Типово yt-dlp шукає тільки deno, тому node, який
    зазвичай уже стоїть, треба вказати явно.

    Застарілі версії (node < 22 тощо) не передаємо: yt-dlp їх однаково
    відкине, а нам важливо чесно показати це в рядку статусу.
    """
    return {name: {"path": path} for name, path, _version, ok in probe_js_runtimes() if ok}


def _runtime_paths():
    found = []
    for exe in ("deno", "node", "bun", "qjs"):
        local = os.path.join(_app_dir(), exe + ".exe")
        path = local if os.path.isfile(local) else shutil.which(exe)
        if path:
            found.append(("quickjs" if exe == "qjs" else exe, path))
    return found


@functools.lru_cache(maxsize=None)
def _runtime_info(name, path):
    """(версія, чи підходить) — за правилами самого yt-dlp (мінімальні
    версії живуть у ньому, а не тут). Кешуємо: це запуск «node --version»."""
    try:
        import yt_dlp  # noqa: F401  — реєструє класи рантаймів
        from yt_dlp.globals import supported_js_runtimes
        info = supported_js_runtimes.value[name](path).info
    except Exception:
        return "?", True        # не вдалося перевірити — довіряємо, хай вирішує yt-dlp
    if info is None:
        return None, False      # файл є, але не запускається
    return info.version, bool(info.supported)


def probe_js_runtimes():
    """[(назва, шлях, версія, чи підходить)] для всіх знайдених JS-рантаймів."""
    return [(name, path, *_runtime_info(name, path)) for name, path in _runtime_paths()]
