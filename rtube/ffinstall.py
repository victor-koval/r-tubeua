"""Встановлення ffmpeg одним кліком — у %APPDATA%\\R-TubeUA\\bin.

Без ffmpeg не склеїти відео зі звуком, а YouTube віддає окремо все вище
360p. Просити колег ставити його через winget виявилося марною справою, тож
програма вміє поставити його сама: завантажує архів, звіряє sha256,
дістає ffmpeg.exe і ffprobe.exe і перевіряє, що ffmpeg запускається.

Працює у фоновому потоці. Інтерфейс читає status(), а черга завантажень
може дочекатися кінця встановлення через wait().
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import zipfile

from . import applog, tools

BIN_DIR = tools.FFMPEG_DIR
EXES = ("ffmpeg.exe", "ffprobe.exe")
APPROX_SIZE_MB = 110

# (назва, архів, файл із sha256, ім'я архіву в цьому файлі або None — якщо в
# ньому одна сума). gyan.dev — той самий білд, що ставить winget
# Gyan.FFmpeg; запасне джерело — збірки від самих yt-dlp на GitHub.
SOURCES = [
    ("gyan.dev",
     "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
     "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256",
     None),
    ("github.com/yt-dlp/FFmpeg-Builds",
     "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
     "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/checksums.sha256",
     "ffmpeg-master-latest-win64-gpl.zip"),
]

CHUNK = 1024 * 1024


class InstallError(Exception):
    pass


_lock = threading.Lock()
_done = threading.Event()
_done.set()
_state = {"running": False, "fraction": None, "text": "", "error": "", "path": None}


def status():
    with _lock:
        return dict(_state)


def in_progress():
    return not _done.is_set()


def wait(timeout=None):
    """True — встановлення завершилось (успішно чи ні)."""
    return _done.wait(timeout)


def start():
    """Запускає встановлення у фоні; повторний виклик під час роботи нічого не робить."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, fraction=0.0, text="Підготовка…", error="", path=None)
        _done.clear()
    threading.Thread(target=_run, daemon=True).start()
    return True


def _set(**values):
    with _lock:
        _state.update(values)


def _run():
    try:
        path = install(lambda fraction, text: _set(fraction=fraction, text=text))
        _set(path=path, text="ffmpeg встановлено")
    except Exception as exc:
        applog.error("Встановлення ffmpeg не вдалося", exc)
        _set(error=str(exc) or type(exc).__name__)
    finally:
        _set(running=False)
        _done.set()


# ── саме встановлення ───────────────────────────────────────────────────

def _open(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "R-TubeUA"})
    return urllib.request.urlopen(req, timeout=timeout)


def parse_checksum(text, filename=None):
    """sha256 з файлу сум: «<hex>» або рядки «<hex>  <ім'я>»."""
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts or len(parts[0]) != 64:
            continue
        if filename is None or (len(parts) > 1 and parts[-1].lstrip("*") == filename):
            return parts[0].lower()
    raise InstallError("у файлі контрольних сум немає потрібного архіву")


def _download(url, dest, expected_sha, progress, label):
    digest = hashlib.sha256()
    with _open(url, timeout=120) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            fraction = done / total if total else None
            text = f"Завантаження ffmpeg з {label}: {done / CHUNK:.0f}"
            text += f" з {total / CHUNK:.0f} МБ" if total else " МБ"
            progress(fraction, text)
    if digest.hexdigest().lower() != expected_sha:
        raise InstallError(f"контрольна сума архіву з {label} не збіглася")


def extract_exes(zip_path, target):
    """Дістає ffmpeg.exe і ffprobe.exe з будь-якої теки архіву."""
    found = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1].lower()
            if base in EXES and base not in found:
                dest = os.path.join(target, base)
                with zf.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                found[base] = dest
    if "ffmpeg.exe" not in found:
        raise InstallError("в архіві немає ffmpeg.exe")
    return found


def check_ffmpeg(path):
    try:
        result = subprocess.run([path, "-version"], capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        raise InstallError(f"ffmpeg не запускається: {exc}") from exc
    if result.returncode != 0 or b"ffmpeg version" not in result.stdout:
        raise InstallError("ffmpeg не запускається")


def install(progress=lambda fraction, text: None):
    """Ставить ffmpeg у BIN_DIR і повертає шлях до ffmpeg.exe. Пробує
    джерела по черзі: корпоративна мережа може не пускати до одного з них."""
    os.makedirs(os.path.dirname(BIN_DIR), exist_ok=True)
    errors = []
    for label, url, sums_url, filename in SOURCES:
        work = tempfile.mkdtemp(prefix="ffmpeg-", dir=os.path.dirname(BIN_DIR))
        try:
            progress(None, f"Перевірка {label}…")
            with _open(sums_url) as resp:
                expected = parse_checksum(resp.read().decode("utf-8", "replace"), filename)
            archive = os.path.join(work, "ffmpeg.zip")
            _download(url, archive, expected, progress, label)
            progress(None, "Розпакування ffmpeg…")
            staged = os.path.join(work, "bin")
            os.makedirs(staged)
            extract_exes(archive, staged)
            check_ffmpeg(os.path.join(staged, "ffmpeg.exe"))
            shutil.rmtree(BIN_DIR, ignore_errors=True)
            os.replace(staged, BIN_DIR)
            path = os.path.join(BIN_DIR, "ffmpeg.exe")
            applog.info(f"ffmpeg встановлено з {label} у {BIN_DIR}")
            return path
        except Exception as exc:
            applog.warning(f"ffmpeg з {label} не встановився: {exc}")
            errors.append(f"{label}: {exc}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    raise InstallError("не вдалося завантажити ffmpeg (" + "; ".join(errors) + ")")
