"""Незавершені завантаження між запусками: %APPDATA%\\R-TubeUA\\queue.json.

Зберігаємо лише те, з чого завдання можна відтворити, — без info: посилання
на формати YouTube живуть кілька годин, тож після перезапуску ролик однаково
аналізується заново (downloader._Runner._prepare). Обраний video_key
зіставиться з новим списком форматів через фолбек resolve_format.
"""

import json
import os

from . import applog
from .downloader import Job
from .settings import CONFIG_DIR

QUEUE_PATH = os.path.join(CONFIG_DIR, "queue.json")

_FIELDS = ("url", "title", "out_dir", "container", "keep_original", "subs_mode",
           "video_key", "audio_lang", "audio_label", "sub_key", "prefs", "product_id", "same_as")


def job_to_dict(job):
    data = {name: getattr(job, name) for name in _FIELDS}
    for name in ("video_key", "sub_key"):
        if data[name] is not None:
            data[name] = list(data[name])
    return data


def dict_to_job(data):
    kwargs = {name: data.get(name) for name in _FIELDS if name in data}
    for name in ("video_key", "sub_key"):
        if kwargs.get(name) is not None:
            kwargs[name] = tuple(kwargs[name])
    kwargs["prefs"] = dict(kwargs.get("prefs") or {})
    if not kwargs.get("url") or not kwargs.get("out_dir"):
        raise ValueError("у записі черги немає url або теки")
    kwargs.setdefault("title", kwargs["url"])
    return Job(**kwargs)


def save(jobs, path=None):
    """Атомарно: спершу тимчасовий файл, потім заміна — обірваний запис не
    зіпсує чергу."""
    path = path or QUEUE_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([job_to_dict(j) for j in jobs], f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as exc:
        applog.error("Не вдалося зберегти чергу", exc)


def load(path=None):
    """Завдання з файлу; пошкоджений чи відсутній файл — порожня черга."""
    path = path or QUEUE_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as exc:
        applog.warning(f"Черга з {path} не читається, починаю з порожньої: {exc}")
        return []
    jobs = []
    for item in raw if isinstance(raw, list) else []:
        try:
            jobs.append(dict_to_job(item))
        except Exception as exc:
            applog.warning(f"Пропускаю запис черги {item!r}: {exc}")
    return jobs


def clear(path=None):
    try:
        os.remove(path or QUEUE_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        applog.warning(f"Не вдалося видалити файл черги: {exc}")


# ── що вже скачано: same_video_key → файл ───────────────────────────────
# Щоб після перезапуску другий товар з тим самим роликом послався на вже
# скачаний файл, а не качав удруге (downloader._Runner._use_sibling). Разом
# зі шляхом пишемо розмір і час зміни: якщо файл відтоді замінили чи
# переписали, запис не довіряємо — інакше товар послався б на чуже відео.
DONE_PATH = os.path.join(CONFIG_DIR, "done.json")
DONE_LIMIT = 2000


def _tuplify(value):
    return tuple(_tuplify(v) for v in value) if isinstance(value, list) else value


def save_done(done, path=None):
    path = path or DONE_PATH
    items = []
    for key, file in list(done.items())[-DONE_LIMIT:]:
        try:
            st = os.stat(file)
        except OSError:
            continue
        items.append({"key": key, "path": file, "size": st.st_size, "mtime": st.st_mtime})
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:
        applog.error("Не вдалося зберегти список скачаного", exc)


def load_done(path=None):
    """{same_video_key: шлях} лише для файлів, що відтоді не змінились."""
    path = path or DONE_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        applog.warning(f"Список скачаного з {path} не читається: {exc}")
        return {}
    done = {}
    for item in raw if isinstance(raw, list) else []:
        try:
            st = os.stat(item["path"])
            if st.st_size == item["size"] and abs(st.st_mtime - item["mtime"]) < 0.01:
                done[_tuplify(item["key"])] = item["path"]
        except Exception:
            continue
    return done
