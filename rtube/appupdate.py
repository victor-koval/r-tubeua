"""Оновлення самої програми з релізів GitHub.

yt-dlp оновлюється сам (ytupdate.py), а .exe колегам доводилось роздавати
руками — і виправлення до них не доходили. Тепер при кожному запуску (якщо
так налаштовано) і кнопкою «Перевірити зараз» програма питає GitHub про
останній реліз; новіший R-TubeUA.exe тихо завантажує в
%APPDATA%\\R-TubeUA\\update, звіряє розмір і sha256 (GitHub дає їх для
кожного файлу релізу) і перевіряє окремим процесом (--app-selftest).
Унизу вікна тоді з'являється «Оновити й перезапустити».

Заміна: працюючий .exe Windows видалити не дає, а перейменувати — так.
Старий стає R-TubeUA.exe.old (прибирається при наступному запуску), новий
кладеться на його місце й запускається. Будь-яка помилка — лише в лог:
програма працює далі на тому, що є.

Лише для зібраного .exe: з python main.py оновлювати нічого.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

from . import applog, settings
from .ytupdate import child_env, is_newer

REPO = "victor-koval/r-tubeua"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "R-TubeUA.exe"
DOWNLOAD_HOSTS = ("github.com",)
UPDATE_DIR = os.path.join(settings.CONFIG_DIR, "update")

# Завантажене й перевірене оновлення, що чекає на «Оновити й перезапустити».
state = {"version": None, "path": None}


class UpdateError(Exception):
    pass


def enabled():
    return bool(getattr(sys, "frozen", False))


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "R-TubeUA",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_release(data):
    """Відповідь GitHub про реліз → {version, url, size, sha256} або None.

    Чернетки й попередні версії не беремо; файл без sha256 — теж: без
    перевірки .exe не підмінюємо.
    """
    if data.get("draft") or data.get("prerelease"):
        return None
    version = (data.get("tag_name") or "").lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+){1,2}", version):
        return None
    for asset in data.get("assets") or []:
        if asset.get("name") != ASSET_NAME:
            continue
        digest = asset.get("digest") or ""
        if not digest.startswith("sha256:"):
            return None
        return {"version": version, "url": asset.get("browser_download_url") or "",
                "size": int(asset.get("size") or 0), "sha256": digest[len("sha256:"):]}
    return None


def fetch_latest():
    return parse_release(json.loads(_get(LATEST_API).decode("utf-8")))


def _check_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in DOWNLOAD_HOSTS:
        raise UpdateError(f"неочікуване джерело оновлення: {url}")


def download(release, target_dir=None, fetch=None):
    """Завантажує й звіряє .exe. Повертає шлях до перевіреного файлу."""
    _check_url(release["url"])
    target_dir = target_dir or UPDATE_DIR
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"R-TubeUA-{release['version']}.exe")
    if os.path.isfile(path) and _sha256_file(path) == release["sha256"].lower():
        return path         # уже завантажено раніше
    data = (fetch or (lambda url: _get(url, timeout=600)))(release["url"])
    if release["size"] and len(data) != release["size"]:
        raise UpdateError(f"розмір {len(data)} ≠ {release['size']}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != release["sha256"].lower():
        raise UpdateError(f"sha256 не збігся: {actual} ≠ {release['sha256']}")
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def selftest(path, version):
    """Чи запускається новий .exe і чи він тієї версії (окремим процесом, без вікна)."""
    try:
        result = subprocess.run([path, "--app-selftest", version], env=child_env(),
                                timeout=180, capture_output=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        applog.error(f"Самоперевірка R-TubeUA {version} не запустилась", exc)
        return False
    if result.returncode != 0:
        applog.warning(f"Самоперевірка R-TubeUA {version} не пройшла (код {result.returncode})")
    return result.returncode == 0


def selftest_main(version):
    """Точка входу «--app-selftest»: модулі вікна імпортуються, версія та сама."""
    try:
        from .app import APP_VERSION
        import yt_dlp  # noqa: F401
        return 0 if APP_VERSION == version else 2
    except Exception:
        return 1


def check_and_download(current):
    """Новий реліз → завантажений і перевірений файл. Повертає версію або None.
    Викликається при запуску програми (якщо так налаштовано) і кнопкою
    «Перевірити зараз»."""
    if not enabled():
        return None
    release = fetch_latest()
    if not release or not is_newer(release["version"], current):
        applog.info(f"R-TubeUA актуальна ({current}; останній реліз "
                    f"{release['version'] if release else '—'})")
        return None
    if release["version"] in (settings.get("app_bad") or []):
        return None
    applog.info(f"Оновлення R-TubeUA {current} → {release['version']}")
    path = download(release)
    if not selftest(path, release["version"]):
        settings.set_many(app_bad=list(settings.get("app_bad") or []) + [release["version"]])
        raise UpdateError(f"R-TubeUA {release['version']} не пройшла самоперевірку")
    state.update(version=release["version"], path=path)
    applog.info(f"R-TubeUA {release['version']} готова до встановлення: {path}")
    return release["version"]


def apply(new_path, exe=None):
    """Ставить new_path на місце працюючого .exe. Старий — поруч як .old."""
    exe = exe or sys.executable
    old = exe + ".old"
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)
    try:
        shutil.copy2(new_path, exe)
    except Exception:
        os.replace(old, exe)
        raise
    applog.info(f"R-TubeUA замінено: {exe} (попередня — {old})")
    return exe


def cleanup(current, exe=None):
    """Після оновлення: прибрати .old і вже встановлені завантаження."""
    if not enabled() and exe is None:
        return
    exe = exe or sys.executable
    try:
        if os.path.exists(exe + ".old"):
            os.remove(exe + ".old")
    except OSError:
        pass            # стара копія ще закривається — приберемо наступного разу
    try:
        names = os.listdir(UPDATE_DIR)
    except OSError:
        return
    for name in names:
        match = re.fullmatch(r"R-TubeUA-([\d.]+)\.exe(\.part)?", name)
        if match and not is_newer(match.group(1), current):
            try:
                os.remove(os.path.join(UPDATE_DIR, name))
            except OSError:
                pass
