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
