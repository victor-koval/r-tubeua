"""Категорія товару на rozetka.com.ua за його ID — для вибору теки на FTP (ftpcat).

Публічний product-api сайту віддає хлібні крихти (українською й російською),
адреси категорій і mpath — ID категорій від кореня. Відповіді кешуються в
products.json: категорії товарів змінюються рідко, а пакет буває на сотні ID.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import applog
from .settings import CONFIG_DIR

CACHE_PATH = os.path.join(CONFIG_DIR, "products.json")
CACHE_TTL = 30 * 24 * 3600          # категорія товару — місяць
MISSING_TTL = 24 * 3600             # товару немає на сайті — спробуємо знову завтра
CACHE_LIMIT = 20000
API = ("https://product-api.rozetka.com.ua/v4/goods/get-main?front-type=xl&country=UA"
       "&lang={lang}&goodsId={gid}")

_lock = threading.Lock()
_cache = None


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (R-TubeUA)",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def slug(href):
    """…/ua/headphones/c80027/ → «headphones» (числові й транслітні — як є)."""
    parts = [p for p in href.split("/") if p and p not in ("ua", "https:", "http:")
             and "rozetka" not in p and not re.fullmatch(r"c\d+", p)]
    return parts[-1].replace("-", " ").replace("_", " ") if parts else ""


def parse(main_ua, main_ru=None):
    """Відповіді get-main → {crumbs_ua, crumbs_ru, slugs, mpath, title} або None."""
    data = (main_ua or {}).get("data") or {}
    crumbs = data.get("breadcrumbs") or []
    if not crumbs:
        return None
    ru = ((main_ru or {}).get("data") or {}).get("breadcrumbs") or []
    mpath = [p for p in str(data.get("mpath") or "").split(".") if p.isdigit()]
    if not mpath:       # без mpath — ID категорій з адрес хлібних крихт
        mpath = [m.group(1) for b in crumbs for m in [re.search(r"/c(\d+)/", b.get("href") or "")]
                 if m]
    return {"title": data.get("title") or "",
            "crumbs_ua": [b.get("title") or "" for b in crumbs],
            "crumbs_ru": [b.get("title") or "" for b in ru],
            "slugs": [slug(b.get("href") or "") for b in crumbs],
            "mpath": mpath}


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
            if not isinstance(_cache, dict):
                _cache = {}
        except Exception:
            _cache = {}
    return _cache


def save_cache():
    """Атомарно; найстаріші записи понад CACHE_LIMIT викидаються."""
    with _lock:
        cache = _load()
        if len(cache) > CACHE_LIMIT:
            keep = sorted(cache.items(), key=lambda kv: kv[1].get("t", 0))[-CACHE_LIMIT:]
            cache.clear()
            cache.update(keep)
        data = dict(cache)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except Exception as exc:
        applog.warning(f"Кеш категорій товарів не записався: {exc}")


def product_info(product_id, fetch=_get_json, save=True):
    """Категорія товару або None (немає на сайті, немає мережі). Кешується."""
    gid = str(product_id).strip()
    if not gid.isdigit():
        return None
    now = time.time()
    with _lock:
        hit = _load().get(gid)
    if hit:
        ttl = CACHE_TTL if hit.get("info") else MISSING_TTL
        if now - hit.get("t", 0) < ttl:
            return hit.get("info")
    try:
        try:
            main_ua = fetch(API.format(lang="ua", gid=gid))
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            exc.close()
            main_ua = None          # товару немає на сайті ({"success": false})
        info = parse(main_ua)
        if info:
            try:
                info = parse(main_ua, fetch(API.format(lang="ru", gid=gid)))
            except Exception as exc:
                applog.warning(f"Товар {gid}: російські назви категорій не отримано — {exc}")
    except Exception as exc:
        applog.warning(f"Товар {gid}: категорію на rozetka.com.ua не отримано — {exc}")
        return hit.get("info") if hit else None     # краще застаріле, ніж нічого
    with _lock:
        _load()[gid] = {"t": now, "info": info}
    if save:
        save_cache()
    return info
