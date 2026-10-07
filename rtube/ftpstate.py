"""Що програма знає про FTP між запусками — файли в %APPDATA%\\R-TubeUA:

- ftp_tree.json — дерево тек розділів (читається кнопкою, не щоразу: video2 —
  тисячі тек);
- ftp_index.json — історія: розділ → категорія сайту → {тека: скільки товарів};
- ftp_rules.json — ручні вибори в плані: розділ → категорія → тека;
- ftp_uploaded.json — що вже залито: локальний файл (шлях, розмір, час) → шлях на FTP.
"""

import json
import os
import threading
import time

from . import applog, ftpcat
from .settings import CONFIG_DIR

TREE, INDEX, RULES, UPLOADED = "ftp_tree.json", "ftp_index.json", "ftp_rules.json", \
    "ftp_uploaded.json"
UPLOADED_LIMIT = 20000

_lock = threading.RLock()
_cache = {}


def _path(name):
    return os.path.join(CONFIG_DIR, name)


def _load(name):
    with _lock:
        if name not in _cache:
            try:
                with open(_path(name), "r", encoding="utf-8") as f:
                    data = json.load(f)
                _cache[name] = data if isinstance(data, dict) else {}
            except FileNotFoundError:
                _cache[name] = {}
            except Exception as exc:
                applog.warning(f"{name} не читається, починаю з порожнього: {exc}")
                _cache[name] = {}
        return _cache[name]


def _save(name):
    """Атомарно: тимчасовий файл → заміна."""
    with _lock:
        data = json.dumps(_load(name), ensure_ascii=False)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = _path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, _path(name))
    except Exception as exc:
        applog.error(f"Не вдалося записати {name}", exc)


def reset_cache():
    """Для тестів: перечитати файли."""
    with _lock:
        _cache.clear()


# ── дерево ──
def tree(section):
    """{шлях: [діти]} розділу або None, якщо дерево ще не читали."""
    paths = (_load(TREE).get("sections") or {}).get(section)
    return ftpcat.tree_from_paths(paths) if paths is not None else None


def tree_age():
    """Коли читали дерево (time.time()) або None."""
    return _load(TREE).get("read_at")


def save_tree(sections):
    """sections — {розділ: [[тека], [тека, підтека], …]} (ftpclient.read_tree)."""
    with _lock:
        data = _load(TREE)
        data.setdefault("sections", {}).update(sections)
        data["read_at"] = time.time()
    _save(TREE)


# ── історія й ручні вибори ──
def index(section):
    with _lock:
        return _load(INDEX).setdefault(section, {})


def learn(section, mpath, folder, save=True):
    """Товар із категоріями mpath тепер лежить у folder (шлях усередині розділу)."""
    if not mpath or not folder:
        return
    with _lock:
        idx = index(section)
        ftpcat.add_to_index(idx, mpath, folder)
        idx[COUNT] = idx.get(COUNT, 0) + 1
    if save:
        _save(INDEX)


def save_index():
    _save(INDEX)


COUNT = "_count"        # службовий ключ у індексі розділу: скільки товарів у ньому


def index_size(section):
    """Скільки товарів у історії розділу."""
    return index(section).get(COUNT, 0)


def rules(section):
    with _lock:
        return _load(RULES).setdefault(section, {})


def remember(section, mpath, folder):
    """Ручний вибір у плані: товари цієї категорії — у folder."""
    if not mpath or not folder:
        return
    with _lock:
        rules(section)[str(mpath[-1])] = list(folder)
    _save(RULES)


# ── що вже залито ──
def _key(local):
    try:
        st = os.stat(local)
    except OSError:
        return None
    return f"{os.path.normcase(os.path.abspath(local))}|{st.st_size}|{int(st.st_mtime)}"


def uploaded(local):
    """Шлях на FTP, якщо саме цей файл (той самий розмір і час) уже залито."""
    key = _key(local)
    return _load(UPLOADED).get(key) if key else None


def mark_uploaded(local, ftp_path):
    key = _key(local)
    if not key:
        return
    with _lock:
        data = _load(UPLOADED)
        data[key] = ftp_path
        if len(data) > UPLOADED_LIMIT:
            for old in list(data)[:len(data) - UPLOADED_LIMIT]:
                del data[old]
    _save(UPLOADED)
