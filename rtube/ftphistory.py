"""Історія розкладання: куди люди вже клали товари на FTP — щоб класти так само.

Для відео «ID.mp4», що вже лежать у теках розділу, беремо категорію товару
на сайті (rozetka.mpath) і записуємо в ftpstate: «категорія → тека». Потім
ftpcat.history_lookup підставляє теку, куди найчастіше клали таку категорію,
— так програма сама переймає домовленості (біжутерія → prikrasi тощо).

Повний обхід — це запит до сайту на кожне відео, а в розділах десятки тисяч
файлів. Для голосування досить вибірки: до SAMPLE_PER_FOLDER файлів з теки.
Оброблені ID пам'ятаються (ftpstate.seen, разом із вшитою базою), тож
«Оновити базу» питає сайт лише про нові файли, і її можна зупинити й продовжити.
"""

import random
import re
import threading
import time

from . import applog, ftpcat, ftpclient, ftpstate, rozetka

SAMPLE_PER_FOLDER = 40
ID_FILE = re.compile(r"(\d{6,12})(?:_\d+)?\.(?:mp4|mkv|mov|webm|avi|m4v)", re.I)
PAUSE = 0.15            # між запитами до сайту — не смикати його без перерви
MAX_FAILURES = 5        # стільки поспіль «сайт не відповів» — зупиняємось


def sample_files(client, section, tree, per_folder=SAMPLE_PER_FOLDER, cancel=None):
    """[(ID товару, тека всередині розділу)] — до per_folder файлів з кожної теки."""
    result = []
    rng = random.Random(section)            # та сама вибірка при продовженні
    for path in sorted(p for p in tree if p):
        if cancel and cancel():
            break
        if not all(ftpcat.usable(name) for name in path):
            continue
        try:
            names = list(client.list_files([section] + list(path)))
        except Exception as exc:
            applog.warning(f"Історія: {section}/{'/'.join(path)} не читається — {exc}")
            continue
        ids = []
        for name in names:
            m = ID_FILE.fullmatch(name)
            if m:
                ids.append(m.group(1))
        rng.shuffle(ids)
        result += [(pid, path) for pid in ids[:per_folder]]
    return result


class HistoryBuilder:
    """Фоновий збір; status — для показу у вікні (читається з іншого потоку)."""

    def __init__(self, connect, sections, product_mpath=None, refresh_tree=True):
        """refresh_tree — спершу перечитати теки розділу (нові теки на FTP)."""
        self._connect = connect
        self._sections = list(sections)
        self._refresh_tree = refresh_tree
        self._mpath = product_mpath or _mpath_from_site
        self._cancel = threading.Event()
        self.status = {"text": "Готуюсь…", "running": True, "error": None}
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._cancel.set()

    def _run(self):
        client = None
        try:
            client = self._connect()
            for section in self._sections:
                if self._cancel.is_set():
                    break
                tree = ftpstate.tree(section)
                if tree is None or self._refresh_tree:
                    self.status["text"] = f"{section}: читаю теки…"
                    ftpstate.save_tree(client.read_tree([section], cancel=self._cancel.is_set))
                    tree = ftpstate.tree(section)
                self.status["text"] = f"{section}: дивлюсь, що вже лежить у теках…"
                files = sample_files(client, section, tree, cancel=self._cancel.is_set)
                done = ftpstate.seen(section)
                todo = [(pid, path) for pid, path in files if pid not in done]
                fresh = []
                failures = 0
                for i, (pid, path) in enumerate(todo, 1):
                    if self._cancel.is_set():
                        break
                    try:
                        mpath = self._mpath(pid)
                    except rozetka.Unavailable as exc:
                        # Не позначаємо обробленим — спробуємо наступного разу.
                        failures += 1
                        if failures >= MAX_FAILURES:
                            raise RuntimeError(f"rozetka.com.ua не відповідає ({exc})") from exc
                        continue
                    failures = 0
                    if mpath:
                        ftpstate.learn(section, mpath, path, save=False)
                    done.add(pid)
                    fresh.append(pid)
                    self.status["text"] = (f"{section}: {i} з {len(todo)} · у базі "
                                           f"{ftpstate.index_size(section)} відео")
                    if i % 50 == 0:
                        ftpstate.mark_seen(section, fresh)
                        fresh = []
                        ftpstate.save_index()
                        rozetka.save_cache()
                ftpstate.mark_seen(section, fresh)
                ftpstate.save_index()
            stopped = self._cancel.is_set()
            sizes = ", ".join(f"{s}: {ftpstate.index_size(s)}" for s in self._sections)
            self.status["text"] = ("Зупинено — продовжиться з того місця. " if stopped else
                                   "Готово. ") + f"У базі відео — {sizes}"
        except ftpclient.Cancelled:
            # «Зупинити» посеред читання тек — це не помилка.
            applog.info("Оновлення бази FTP зупинено")
            self.status["text"] = "Зупинено — продовжиться з того місця."
        except Exception as exc:
            applog.error("Збір історії FTP не вдався", exc)
            self.status["error"] = str(exc)
            self.status["text"] = f"Не вдалося: {exc}"[:120]
        finally:
            ftpstate.save_index()
            rozetka.save_cache()
            if client is not None:
                client.close()
            self.status["running"] = False


def _mpath_from_site(pid):
    """Лише категорії (mpath) — одним запитом, без російських назв."""
    started = time.monotonic()
    info = rozetka.product_info(pid, save=False, languages=("ua",), raise_errors=True)
    if time.monotonic() - started > 0.05:
        time.sleep(PAUSE)               # ходили на сайт (не з кешу) — коротка пауза
    return (info or {}).get("mpath")
