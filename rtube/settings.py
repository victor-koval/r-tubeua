"""Налаштування, що переживають перезапуск: %APPDATA%\\R-TubeUA\\settings.json."""

import json
import os
import threading

_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
# RTUBE_HOME — інша тека для налаштувань, логу, черги й оновлень. Ставлять
# тести (tests/__init__.py), щоб не писати в справжній лог і done.json.
CONFIG_DIR = os.environ.get("RTUBE_HOME") or os.path.join(_APPDATA, "R-TubeUA")
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
    "max_height": 1080,        # якість за замовчуванням (і нижче, якщо такої немає); 0 — найкраща
    "container": "mp4",        # mp4 / mkv для відео
    "audio_container": "m4a",  # m4a / mp3 для режиму «лише звук»
    "preferred_audio": "uk",   # uk — українська, якщо є; orig — оригінальна доріжка
    "keep_original": False,    # додати оригінальну доріжку другою
    "subs_mode": "embed",      # embed — вшити у відео, file — окремим .srt
    "theme": "Темна",
    "notify_done": True,       # сповіщення Windows, коли все завантажено
    "taskbar_progress": True,  # прогрес на іконці в панелі задач
    "resume_queue": True,      # продовжувати незавершене після перезапуску
    "auto_report": True,       # звіт xlsx сам, коли пакет завантажився
    "check_updates_on_start": True,  # при запуску питати про нову версію програми й yt-dlp
    "watch_clipboard": False,  # скопійоване посилання на YouTube саме йде на аналіз
    # FTP: сервер і логін зберігаються лише з «Запам'ятати» (пароль — у
    # Диспетчері облікових даних Windows, не тут); без нього — до закриття програми.
    "ftp_remember": False,
    "ftp_host": "",
    "ftp_user": "",
    "ftp_sections": "video, video2, video3, video4, video5",   # у які розділи можна заливати
    "app_bad": [],             # версії програми, що не пройшли самоперевірку
    "geometry": "",
    "window_pos": "",          # де закрили вікно: «x,y» у справжніх пікселях
    "ytdlp_bad": [],           # версії yt-dlp, що не пройшли самоперевірку або не запустились
}

# Те, що показано у вікні налаштувань і що скидає «Скинути до стандартних».
# Службове (розмір вікна, стан оновлювача) сюди не входить.
USER_KEYS = ("download_dir", "max_height", "container", "audio_container", "preferred_audio",
             "keep_original", "subs_mode", "theme", "notify_done", "taskbar_progress",
             "resume_queue", "auto_report", "check_updates_on_start", "watch_clipboard",
             "ftp_sections")

# Розділи FTP — у цьому порядку: забитий — далі наступний відмічений.
FTP_SECTIONS = ("video", "video2", "video3", "video4", "video5")


def ftp_sections():
    """Відмічені розділи FTP у сталому порядку; лише відомі, хоча б один."""
    raw = get("ftp_sections") or ""
    chosen = {s.strip().strip("/") for s in raw.replace(";", ",").split(",")}
    return [s for s in FTP_SECTIONS if s in chosen] or list(FTP_SECTIONS)


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
            if "check_updates_on_start" not in stored and \
                    stored.get("ytdlp_autoupdate") is False and stored.get("app_autoupdate") is False:
                data["check_updates_on_start"] = False      # з версій до 1.7
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


def reset_user():
    """«Скинути до стандартних»: лише те, що є у вікні налаштувань."""
    set_many(**{key: DEFAULTS[key] for key in USER_KEYS})


def save():
    """Атомарно: спершу тимчасовий файл, потім заміна — збій посеред запису
    (вимкнули світло, закрили процес) не обнулить налаштування."""
    with _lock:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = SETTINGS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_load(), f, ensure_ascii=False, indent=4)
            os.replace(tmp, SETTINGS_PATH)
        except Exception:
            pass
