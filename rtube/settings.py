"""Налаштування, що переживають перезапуск: %APPDATA%\\R-TubeUA\\settings.json."""

import json
import os
import threading

_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
CONFIG_DIR = os.path.join(_APPDATA, "R-TubeUA")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")


def _known_downloads():
    """Справжня тека «Завантаження» з Windows.

    ~/Downloads не годиться: теку часто переносять на інший диск або в
    OneDrive, і тоді там порожньо або її взагалі немає.
    """
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        raw = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}").bytes_le  # FOLDERID_Downloads
        guid = GUID.from_buffer_copy(raw)
        path_ptr = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None,
                                                      ctypes.byref(path_ptr)) == 0:
            path = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception:
        pass
    return None


def _default_download_dir():
    for path in (_known_downloads(), os.path.join(os.path.expanduser("~"), "Downloads")):
        if path and os.path.isdir(path):
            return path
    return os.path.expanduser("~")


DEFAULTS = {
    "download_dir": "",        # куди зберігати; порожньо — «Завантаження» користувача
    "max_height": 1080,        # якість, яку обирати за замовчуванням (і нижче, якщо такої немає)
    "container": "mp4",        # mp4 / mkv для відео
    "audio_container": "m4a",  # m4a / mp3 для режиму «лише звук»
    "preferred_audio": "uk",   # мова доріжки за замовчуванням
    "keep_original": False,    # додати оригінальну доріжку другою
    "subs_mode": "embed",      # embed — вшити у відео, file — окремим .srt
    "theme": "Темна",
    "geometry": "",
}

_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except Exception:
        pass
    _cache = data
    return _cache


def get(key):
    value = _load().get(key, DEFAULTS.get(key))
    if key == "download_dir" and (not value or not os.path.isdir(value)):
        return _default_download_dir()
    return value


def set_many(**values):
    data = _load()
    changed = False
    for key, value in values.items():
        if key in DEFAULTS and data.get(key) != value:
            data[key] = value
            changed = True
    if changed:
        save()


def save():
    with _lock:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(_load(), f, ensure_ascii=False, indent=4)
        except Exception:
            pass
