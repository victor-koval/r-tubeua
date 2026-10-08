"""Встановлення JS-рантайму одним кліком — у %APPDATA%\\R-TubeUA\\js.

Без нього yt-dlp ходить до YouTube обхідним клієнтом, і частину роликів
(перевірено 08.10.2026 на дитячих — «Майнкрафт — …») YouTube так не віддає:
«This video is not available», хоча ролик публічний. З рантаймом — ті самі
ролики качаються. Просити колег ставити Node.js через winget — марна справа
(як і з ffmpeg), тож програма ставить його сама, без прав адміністратора.

Спершу Deno (його yt-dlp і так бере за замовчуванням), запасне джерело — Node.js
LTS з nodejs.org: корпоративна мережа може не пускати до одного з них. Архів
звіряється з sha256, з нього дістається один .exe, і він має запуститися.
"""

import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from . import applog, tools
from .ffinstall import Background, InstallCancelled, InstallError, _download, _open, \
    parse_checksum

JS_DIR = tools.JS_DIR
APPROX_SIZE_MB = 45

DENO_ZIP = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
NODE_DIST = "https://nodejs.org/dist/latest-v24.x/"
_NODE_ZIP = re.compile(r"node-v[\d.]+-win-x64\.zip")


def _deno_source():
    """(посилання на архів, sha256) для Deno: сума лежить поруч, у виводі Get-FileHash."""
    with _open(DENO_ZIP + ".sha256sum") as resp:
        return DENO_ZIP, parse_checksum(resp.read().decode("utf-8", "replace"))


def _node_source():
    """(посилання, sha256) для Node.js LTS: ім'я архіву з версією — із SHASUMS256.txt."""
    with _open(NODE_DIST + "SHASUMS256.txt") as resp:
        text = resp.read().decode("utf-8", "replace")
    names = [line.split()[-1] for line in text.splitlines()
             if line.split() and _NODE_ZIP.fullmatch(line.split()[-1])]
    if not names:
        raise InstallError("у SHASUMS256.txt немає архіву для Windows")
    return NODE_DIST + names[0], parse_checksum(text, names[0])


# (назва для статусу, функція → (архів, sha256), що дістати з архіву)
SOURCES = [
    ("github.com/denoland/deno", _deno_source, "deno.exe"),
    ("nodejs.org", _node_source, "node.exe"),
]


def extract_exe(zip_path, exe, target):
    """Дістає exe з будь-якої теки архіву в target. Повертає шлях."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.rsplit("/", 1)[-1].lower() == exe:
                dest = os.path.join(target, exe)
                with zf.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                return dest
    raise InstallError(f"в архіві немає {exe}")


def check_runtime(path):
    try:
        result = subprocess.run([path, "--version"], capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        raise InstallError(f"{os.path.basename(path)} не запускається: {exc}") from exc
    if result.returncode != 0:
        raise InstallError(f"{os.path.basename(path)} не запускається")


def install(progress=lambda fraction, text: None, cancel=None):
    """Ставить JS-рантайм у JS_DIR і повертає шлях до .exe. Джерела — по черзі;
    скасування не переходить до наступного."""
    os.makedirs(os.path.dirname(JS_DIR), exist_ok=True)
    errors = []
    for label, source, exe in SOURCES:
        work = tempfile.mkdtemp(prefix="js-", dir=os.path.dirname(JS_DIR))
        try:
            progress(None, f"Перевірка {label}…")
            url, expected = source()
            archive = os.path.join(work, "runtime.zip")
            _download(url, archive, expected, progress, label, cancel=cancel, what="JS-рантайму")
            if cancel is not None and cancel.is_set():
                raise InstallCancelled()
            progress(None, "Розпакування JS-рантайму…")
            staged = os.path.join(work, "js")
            os.makedirs(staged)
            check_runtime(extract_exe(archive, exe, staged))
            shutil.rmtree(JS_DIR, ignore_errors=True)
            os.replace(staged, JS_DIR)
            path = os.path.join(JS_DIR, exe)
            applog.info(f"JS-рантайм {exe} встановлено з {label} у {JS_DIR}")
            return path
        except InstallCancelled:
            raise
        except Exception as exc:
            applog.warning(f"JS-рантайм з {label} не встановився: {exc}")
            errors.append(f"{label}: {exc}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    raise InstallError("не вдалося завантажити JS-рантайм (" + "; ".join(errors) + ")")


_background = Background("JS-рантайм", lambda progress, cancel: install(progress, cancel))
status, in_progress, wait = _background.status, _background.in_progress, _background.wait
start, cancel = _background.start, _background.cancel
