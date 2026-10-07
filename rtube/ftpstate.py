"""Що програма знає про FTP: два шари.

1. **Вшита база** — assets/ftp_base.json у збірці: дерево тек розділів,
   історія розкладання (категорія сайту → тека) й оброблені ID. Готується
   перед кожним релізом (`python -m rtube.ftpstate --export`, його викликає
   build.ps1 -Release), тож новий користувач нічого не збирає.
2. **Свої дані** в %APPDATA%\\R-TubeUA — поверх бази:
   - ftp_tree.json — дерево, перечитане вручну («Оновити базу»);
   - ftp_index.json — що додалось до історії (свої заливання, оновлення бази);
   - ftp_history_seen.json — які ID уже оброблено поверх бази;
   - ftp_rules.json — ручні вибори в плані (база їх не містить: це ваше);
   - ftp_uploaded.json — що вже залито: файл (шлях, розмір, час) → шлях на FTP.

Коли приходить реліз зі свіжішою базою, свої історія й оброблені ID
скидаються: свіжа база вже містить усе зібране до неї.
"""

import copy
import json
import os
import sys
import threading
import time

from . import applog, ftpcat
from .settings import CONFIG_DIR

TREE, INDEX, RULES, UPLOADED, SEEN = "ftp_tree.json", "ftp_index.json", "ftp_rules.json", \
    "ftp_uploaded.json", "ftp_history_seen.json"
BASE_NAME = os.path.join("assets", "ftp_base.json")
UPLOADED_LIMIT = 20000
COUNT = "_count"        # службовий ключ у індексі розділу: скільки товарів у ньому
META = "_meta"          # у ftp_index.json: від якої бази рахується свій шар

_lock = threading.RLock()
_cache = {}
_merged = {}            # розділ → база + свій шар (для ftpcat.history_lookup)
_base = None


def _path(name):
    return os.path.join(CONFIG_DIR, name)


def base_path():
    """assets/ftp_base.json — у збірці (_MEIPASS) чи поруч із кодом."""
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, BASE_NAME)


def base():
    """Вшита база: {built_at, tree, index, seen}; порожня, якщо файлу немає."""
    global _base
    with _lock:
        if _base is None:
            try:
                with open(base_path(), "r", encoding="utf-8") as f:
                    data = json.load(f)
                _base = data if isinstance(data, dict) else {}
            except FileNotFoundError:
                _base = {}
            except Exception as exc:
                applog.warning(f"Вшита база FTP не читається: {exc}")
                _base = {}
        return _base


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
            if name == INDEX:
                _drop_if_base_newer()
        return _cache[name]


def _drop_if_base_newer():
    """Свій шар історії рахується від певної бази; прийшла новіша — скидаємо."""
    built = base().get("built_at") or 0
    local = _cache[INDEX]
    if (local.get(META) or {}).get("base", 0) < built:
        had = any(k != META for k in local)
        local.clear()
        local[META] = {"base": built}
        _cache[SEEN] = {}
        if had:
            applog.info("Прийшла свіжіша вшита база FTP — свій шар історії скинуто")
            _save(INDEX)
            _save(SEEN)


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
    """Для тестів і після підміни бази: перечитати файли."""
    global _base
    with _lock:
        _cache.clear()
        _merged.clear()
        _base = None


# ── дерево ──
def _tree_paths(section):
    local = (_load(TREE).get("sections") or {}).get(section)
    if local is not None:
        return local
    return (base().get("tree") or {}).get(section)


def tree(section):
    """{шлях: [діти]} розділу (своє перечитане або з бази) або None."""
    paths = _tree_paths(section)
    return ftpcat.tree_from_paths(paths) if paths is not None else None


def tree_age():
    """Коли читали дерево (time.time()): своє перечитане чи з бази; None — ніколи."""
    return _load(TREE).get("read_at") or base().get("built_at")


def save_tree(sections):
    """sections — {розділ: [[тека], [тека, підтека], …]} (ftpclient.read_tree)."""
    with _lock:
        data = _load(TREE)
        data.setdefault("sections", {}).update(sections)
        data["read_at"] = time.time()
    _save(TREE)


# ── історія ──
def _local_index(section):
    with _lock:
        return _load(INDEX).setdefault(section, {})


def index(section):
    """Історія розділу: вшита база + свій шар (для ftpcat)."""
    with _lock:
        if section not in _merged:
            merged = copy.deepcopy((base().get("index") or {}).get(section) or {})
            for cat, votes in _local_index(section).items():
                if cat == COUNT:
                    merged[COUNT] = merged.get(COUNT, 0) + votes
                    continue
                into = merged.setdefault(cat, {})
                for folder, n in votes.items():
                    into[folder] = into.get(folder, 0) + n
            _merged[section] = merged
        return _merged[section]


def learn(section, mpath, folder, save=True):
    """Товар із категоріями mpath тепер лежить у folder (шлях усередині розділу)."""
    if not mpath or not folder:
        return
    with _lock:
        for idx in (_local_index(section), index(section)):
            ftpcat.add_to_index(idx, mpath, folder)
            idx[COUNT] = idx.get(COUNT, 0) + 1
    if save:
        _save(INDEX)


def save_index():
    _save(INDEX)


def index_size(section):
    """Скільки відео в історії розділу (база + свої)."""
    return index(section).get(COUNT, 0)


# ── оброблені ID (для «Оновити базу»: щоб не питати сайт двічі) ──
def seen(section):
    """Множина вже оброблених ID розділу — з бази й своїх. Змінювати через mark_seen."""
    with _lock:
        _load(INDEX)                # спершу — чи не скинуто свій шар через нову базу
        local = _load(SEEN).setdefault(section, [])
        return set((base().get("seen") or {}).get(section) or []) | set(local)


def mark_seen(section, pids, save=True):
    with _lock:
        _load(INDEX)
        local = _load(SEEN).setdefault(section, [])
        known = set(local) | set((base().get("seen") or {}).get(section) or [])
        local.extend(p for p in pids if p not in known)
    if save:
        _save(SEEN)


def save_seen():
    _save(SEEN)


# ── ручні вибори ──
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


# ── вивантаження бази для релізу ──
def export_base(path, sections=None):
    """База + свої дані → новий ftp_base.json (дерево, історія, оброблені ID).
    Повертає {розділ: товарів в історії}."""
    sections = sections or sorted(set((base().get("tree") or {}))
                                  | set(_load(TREE).get("sections") or {})
                                  | set((base().get("index") or {}))
                                  | {k for k in _load(INDEX) if k != META})
    data = {"built_at": time.time(), "tree": {}, "index": {}, "seen": {}}
    for section in sections:
        paths = _tree_paths(section)
        if paths is not None:
            data["tree"][section] = paths
        idx = index(section)
        if idx:
            data["index"][section] = idx
        ids = seen(section)
        if ids:
            data["seen"][section] = sorted(ids)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)
    return {s: data["index"].get(s, {}).get(COUNT, 0) for s in sections}


if __name__ == "__main__":
    # build.ps1 -Release: python -m rtube.ftpstate --export assets\ftp_base.json
    if len(sys.argv) == 3 and sys.argv[1] == "--export":
        counts = export_base(sys.argv[2])
        age = _load(TREE).get("read_at")
        print("База FTP: " + ", ".join(f"{s} {n}" for s, n in counts.items()) +
              (f"; теки читались {time.strftime('%d.%m.%Y', time.localtime(age))}" if age else ""))
    else:
        print("python -m rtube.ftpstate --export <шлях до ftp_base.json>")
