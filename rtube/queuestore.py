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
           "video_key", "audio_lang", "audio_label", "sub_key", "prefs", "product_id")


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
