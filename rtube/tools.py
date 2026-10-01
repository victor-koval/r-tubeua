"""Зовнішні програми й дрібні утиліти: ffmpeg, JS-рантайм, чистка посилань."""

import glob
import os
import re
import shutil
import sys
from urllib.parse import parse_qs, urlparse

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
    YouTube віддає все вище 360p. Шукаємо: поруч із програмою, у PATH, у
    теці пакета winget (його PATH підхоплюється не всюди, доки не
    перезайти в систему).
    """
    local = os.path.join(_app_dir(), "ffmpeg.exe")
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
    for path in (r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"):
        if os.path.isfile(path):
            return path
    return None


def find_js_runtimes():
    """JS-рантайми для yt-dlp у форматі параметра js_runtimes.

    Без жодного з них YouTube віддає лише оригінальну доріжку: усі
    дубляжі (зокрема ШІ-озвучка українською) приходять через web-клієнт,
    якому yt-dlp мусить розв'язати JS-челендж. Типово yt-dlp шукає тільки
    deno, тому node, який зазвичай уже стоїть, треба вказати явно.
    """
    runtimes = {}
    for name in ("deno", "node", "bun", "qjs"):
        local = os.path.join(_app_dir(), name + ".exe")
        path = local if os.path.isfile(local) else shutil.which(name)
        if path:
            runtimes["quickjs" if name == "qjs" else name] = {"path": path}
    return runtimes
