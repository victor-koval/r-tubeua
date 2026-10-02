"""Оновлення yt-dlp без перезбірки .exe.

YouTube ламає yt-dlp приблизно раз на кілька тижнів, а у .exe він вшитий.
Щоб не перезбирати й не роздавати програму щоразу, свіжий yt-dlp (разом із
yt-dlp-ejs тієї версії, яку він вимагає) кладемо в %APPDATA%\\R-TubeUA\\lib\\<версія>
і на старті ставимо цю теку першою в sys.path. У PyInstaller 6 вшиті модулі
знаходяться через sys.path_hooks, тож тека з початку sys.path має пріоритет.

Нову версію спершу перевіряємо окремим процесом (--selftest): так ловляться
і зламані залежності, і модулі stdlib, яких у .exe немає. Будь-яка помилка
лише пишеться в лог — програма працює далі на тому, що вже є.
"""

import hashlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

from . import applog, settings

LIB_DIR = os.path.join(settings.CONFIG_DIR, "lib")
CURRENT_PATH = os.path.join(LIB_DIR, "current.json")
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION_JSON = "https://pypi.org/pypi/{name}/{version}/json"
WHEEL_HOST = "files.pythonhosted.org"
CHECK_INTERVAL = 12 * 3600
PACKAGES = ("yt_dlp/", "yt_dlp_ejs/")

# Що зараз працює: вшита версія чи з lib, і чи чекає застосування новіша.
state = {"bundled": None, "active": None, "source": "bundled", "pending": None}


class UpdateError(Exception):
    pass


def parse_version(text):
    """«2026.08.19» → (2026, 8, 19). Порівнювати рядками не можна: «2026.10.1» < «2026.9.30»."""
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def same_version(a, b):
    """yt-dlp пише про себе «2026.08.19», а PyPI віддає нормалізоване «2026.8.19»."""
    return parse_version(a) == parse_version(b)


def is_newer(candidate, than):
    return bool(candidate) and (not than or parse_version(candidate) > parse_version(than))


def bundled_version():
    try:
        return importlib.metadata.version("yt-dlp")
    except Exception:
        return None


def _loaded_version():
    """Версія yt-dlp, що вже працює, — якщо state порожній (не знайшлося
    метаданих вшитого пакета або activate() не викликали). Без цього кожна
    перевірка вважала б поточну версію невідомою й ставила ту саму заново."""
    try:
        import yt_dlp.version
        return yt_dlp.version.__version__
    except Exception:
        return None


def _bad_versions():
    return list(settings.get("ytdlp_bad") or [])


def mark_bad(version):
    bad = _bad_versions()
    if version not in bad:
        settings.set_many(ytdlp_bad=bad + [version])


def read_current():
    try:
        with open(CURRENT_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("version")
    except Exception:
        return None


def _write_current(version):
    os.makedirs(LIB_DIR, exist_ok=True)
    tmp = CURRENT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": version, "installed_at": int(time.time())}, f)
    os.replace(tmp, CURRENT_PATH)


def _purge_modules():
    for name in list(sys.modules):
        if name == "yt_dlp" or name.startswith(("yt_dlp.", "yt_dlp_ejs")):
            del sys.modules[name]


# ── старт програми ──────────────────────────────────────────────────────

def activate():
    """Підключає yt-dlp з lib, якщо він новіший за вшитий. Викликати ДО
    першого import yt_dlp. Якщо імпорт падає — відкат на вшитий."""
    bundled = bundled_version()
    state.update(bundled=bundled, active=bundled, source="bundled")
    version = read_current()
    path = os.path.join(LIB_DIR, version) if version else None
    if not version or not os.path.isdir(path) or version in _bad_versions():
        return
    if not is_newer(version, bundled):
        return      # .exe перезібрали з новішим yt-dlp — lib уже застарів
    sys.path.insert(0, path)
    try:
        import yt_dlp
        import yt_dlp.version
        if not os.path.abspath(yt_dlp.__file__).startswith(os.path.abspath(path)):
            raise UpdateError(f"yt_dlp імпортовано не з {path}")
        if not same_version(yt_dlp.version.__version__, version):
            raise UpdateError(f"у теці {yt_dlp.version.__version__}, а не {version}")
    except Exception as exc:
        applog.error(f"yt-dlp {version} з {path} не запустився — працюю на вшитому {bundled}", exc)
        sys.path.remove(path)
        _purge_modules()
        mark_bad(version)
        return
    state.update(active=version, source="lib")
    applog.info(f"yt-dlp {version} з {path} (вшитий {bundled})")


# ── фонова перевірка ────────────────────────────────────────────────────

def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "R-TubeUA"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_json(url):
    return json.loads(_get(url).decode("utf-8"))


def pick_wheel(data):
    """Чисте Python-колесо з відповіді PyPI: (url, sha256)."""
    for item in data.get("urls") or []:
        name = item.get("filename") or ""
        if item.get("packagetype") == "bdist_wheel" and name.endswith("-py3-none-any.whl"):
            return item["url"], (item.get("digests") or {}).get("sha256")
    raise UpdateError("на PyPI немає py3-none-any колеса")


def fetch_wheel(url, sha256):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != WHEEL_HOST:
        raise UpdateError(f"неочікуване джерело колеса: {url}")
    if not sha256:
        raise UpdateError("PyPI не дав sha256 — без перевірки не ставимо")
    data = _get(url, timeout=120)
    verify_sha256(data, sha256)
    return data


def verify_sha256(data, expected):
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != (expected or "").lower():
        raise UpdateError(f"sha256 не збігся: {actual} ≠ {expected}")


def ejs_pin(wheel_bytes):
    """Версія yt-dlp-ejs, яку вимагає колесо yt-dlp (Requires-Dist ...==X)."""
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        meta = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
        if not meta:
            return None
        text = zf.read(meta).decode("utf-8", "replace")
    match = re.search(r"^Requires-Dist:\s*yt-dlp-ejs\s*==\s*([\w.]+)", text, re.M)
    return match.group(1) if match else None


def extract_packages(wheel_bytes, target):
    """Розпаковує лише yt_dlp/ і yt_dlp_ejs/ (без dist-info), з захистом від
    шляхів, що виходять за межі теки."""
    root = os.path.abspath(target)
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        for name in zf.namelist():
            if not name.startswith(PACKAGES) or name.endswith("/"):
                continue
            dest = os.path.abspath(os.path.join(root, name))
            if not dest.startswith(root + os.sep):
                raise UpdateError(f"підозрілий шлях у колесі: {name}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def _relaunch_cmd(*args):
    """Команда, що запускає цю ж програму: .exe або python main.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return [sys.executable, main, *args]


def child_env():
    """Оточення для незалежної копії onefile-програми: без цього вона
    вирішила б, що вона частина нашого процесу, і взяла б нашу тимчасову
    теку, яку PyInstaller видалить, щойно ми закриємось."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    # Вивід --selftest читаємо як UTF-8; без цього кирилиця в лозі ламається.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_selftest(path, version):
    try:
        result = subprocess.run(
            _relaunch_cmd("--selftest", path, version), env=child_env(), timeout=180,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        applog.error(f"Самоперевірка yt-dlp {version} не запустилась", exc)
        return False
    if result.returncode != 0:
        applog.warning(f"Самоперевірка yt-dlp {version} не пройшла (код {result.returncode}): "
                       f"{(result.stdout + result.stderr).strip()[-2000:]}")
    return result.returncode == 0


def selftest_main(path, version):
    """Точка входу «--selftest»: чи імпортується yt-dlp з теки й чи вантажиться
    екстрактор YouTube. Код виходу 0 — усе гаразд."""
    sys.path.insert(0, path)
    try:
        import yt_dlp
        import yt_dlp.version
        from yt_dlp.extractor.youtube import YoutubeIE  # noqa: F401
        if not os.path.abspath(yt_dlp.__file__).startswith(os.path.abspath(path)):
            raise UpdateError("yt_dlp імпортовано не з теки оновлення")
        if not same_version(yt_dlp.version.__version__, version):
            raise UpdateError(f"версія {yt_dlp.version.__version__}, очікувалась {version}")
        if os.path.isdir(os.path.join(path, "yt_dlp_ejs")):
            import yt_dlp_ejs  # noqa: F401
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            ydl.get_info_extractor("Youtube")
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    print(f"OK {version}")
    return 0


def _prune(keep):
    """Лишає активну й нову версії — є куди відкотитись, а місце не росте."""
    try:
        names = os.listdir(LIB_DIR)
    except OSError:
        return
    for name in names:
        path = os.path.join(LIB_DIR, name)
        if os.path.isdir(path) and name not in keep and re.match(r"^[\d.]+(\.tmp)?$", name):
            shutil.rmtree(path, ignore_errors=True)


def check_and_install(force=False):
    """Ставить свіжий yt-dlp у lib. Повертає встановлену версію або None.

    Застосується після перезапуску: модуль yt_dlp у пам'яті вже завантажено.
    """
    if not force and time.time() - float(settings.get("ytdlp_checked_at") or 0) < CHECK_INTERVAL:
        return None
    settings.set_many(ytdlp_checked_at=time.time())

    latest = _get_json(PYPI_JSON.format(name="yt-dlp"))
    version = latest["info"]["version"]
    current = state["pending"] or state["active"] or _loaded_version()
    if not is_newer(version, current) or version in _bad_versions():
        applog.info(f"yt-dlp актуальний ({current}; на PyPI {version})")
        return None

    applog.info(f"Оновлення yt-dlp {current} → {version}")
    wheel = fetch_wheel(*pick_wheel(latest))
    wheels = [wheel]
    pin = ejs_pin(wheel)
    if pin:
        ejs = _get_json(PYPI_VERSION_JSON.format(name="yt-dlp-ejs", version=pin))
        wheels.append(fetch_wheel(*pick_wheel(ejs)))

    final = os.path.join(LIB_DIR, version)
    tmp = final + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        for w in wheels:
            extract_packages(w, tmp)
        if not run_selftest(tmp, version):
            mark_bad(version)
            raise UpdateError(f"yt-dlp {version} не пройшов самоперевірку")
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _write_current(version)
    keep = {version}
    if state["source"] == "lib" and state["active"]:
        keep.add(state["active"])
    _prune(keep)
    state["pending"] = version
    applog.info(f"yt-dlp {version} встановлено в {final} (yt-dlp-ejs {pin})")
    return version


def relaunch():
    """Запускає нову копію програми (вона підхопить свіжий yt-dlp)."""
    subprocess.Popen(_relaunch_cmd(), env=child_env(), close_fds=True)
