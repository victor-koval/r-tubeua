"""Заливання на FTP у фоні: план (куди класти кожен файл) і саме заливання.

Як і завантажувач, працює в окремому потоці й лише кладе події в чергу
(events): вікно розбирає їх у своєму потоці. Розділи — по черзі (video →
video2 → …): коли поточний забитий, файл перезіставляється в наступному.
"""

import collections
import itertools
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field

from . import applog, ftpcat, ftpclient, ftpstate, rozetka

_task_ids = itertools.count(1)

# Стани завдання заливання.
PLANNING, PLANNED, NEED_CHOICE, ALREADY = "planning", "planned", "need_choice", "already"
QUEUED, UPLOADING, UPLOADED, ERROR, CANCELLED = \
    "queued", "uploading", "uploaded", "error", "cancelled"
READY = (PLANNED,)                       # можна заливати без питань
# Обірвалось з'єднання чи сервер не відповів — ще спроби з докачуванням, а не
# одразу «помилка» (у колеги лишився недолитий «.rtube-part»).
RETRY_WAITS = (5, 15, 45)
FINISHED = (UPLOADED, ALREADY, ERROR, CANCELLED)
# На FTP — лише відео товарів; звук (m4a, mp3…) туди не потрапляє ніколи.
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
NOT_VIDEO = "Не відео — на FTP заливаються лише відео"
# Ім'я на FTP зайняте іншим відео (у товару кілька роликів) — 590312170_2.mp4, _3…
MAX_NAME_SUFFIX = 99
NAME_SUFFIX = re.compile(r"_\d+$")
MAX_RECHECKS = 3                         # ім'я зайняли між планом і заливанням


def is_video(path):
    return os.path.splitext(path or "")[1].lower() in VIDEO_EXTS


def product_names(name):
    """«590312170.mp4» (чи «590312170_2.mp4») → [590312170.mp4, 590312170_2.mp4, …
    590312170_99.mp4] — імена, під якими на FTP лежать відео цього товару."""
    stem, ext = os.path.splitext(name)
    base = NAME_SUFFIX.sub("", stem) or stem
    return [base + ext] + [f"{base}_{n}{ext}" for n in range(2, MAX_NAME_SUFFIX + 1)]


@dataclass
class UploadTask:
    local: str                          # готовий файл на диску
    product_id: str
    job_id: int = 0
    name: str = ""                      # ім'я на FTP — як локальне (590312170.mp4)
    mpath: list = None                  # категорії сайту (з rozetka)
    product: dict = None                # rozetka.product_info — для зіставлення назв
    site: str = ""                      # «Одяг → Одяг для чоловіків → …» — для плану
    section: str = ""
    path: tuple = ()                    # тека всередині розділу; () — не визначено
    source: str = None                  # ftpcat.RULE / HISTORY / NAMES
    confidence: float = 0.0
    state: str = PLANNING
    note: str = ""
    fraction: float = 0.0
    sent: int = 0                       # залито байт (для загальної смужки)
    total: int = 0                      # розмір файлу, байт
    speed: float = 0.0                  # байт/с, згладжено
    ftp_path: str = ""
    id: int = field(default_factory=lambda: next(_task_ids))
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self):
        self.name = self.name or os.path.basename(self.local)

    @property
    def folder(self):
        """«video/odyag_vzuttya_ta_aksesuari/odyag» або ""."""
        return "/".join((self.section,) + tuple(self.path)) if self.path else ""

    @property
    def renamed(self):
        """На FTP піде під іншим ім'ям: своє там зайняте іншим відео товару."""
        return self.name != os.path.basename(self.local)


class UploadManager:
    """Одне з'єднання, одна дія за раз: план або заливання."""

    def __init__(self, connect, sections, product_info=rozetka.product_info):
        """connect() → підключений ftpclient.FtpClient; sections() → порядок розділів."""
        self.events = queue.Queue()
        self._connect = connect
        self._sections = sections
        self._product_info = product_info
        self._todo = collections.deque()
        self._cond = threading.Condition()
        self._client = None
        self._full = set()              # забиті в цьому сеансі розділи
        self._files = {}                # тека на FTP → {ім'я: розмір} (кеш на сеанс)
        self.running = None
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    # ── команди з вікна ──
    def plan(self, tasks):
        for task in tasks:
            task.state, task.note = PLANNING, "Визначаю теку…"
            self._push("plan", task)

    def upload(self, tasks):
        for task in tasks:
            task.state, task.note, task.fraction = QUEUED, "У черзі на FTP", 0.0
            task.sent, task.speed = 0, 0.0
            try:
                task.total = os.path.getsize(task.local)
            except OSError:
                task.total = 0
            task.cancel_event = threading.Event()
            ftpstate.add_pending(task.local, product_id=task.product_id, name=task.name,
                                 section=task.section, path=list(task.path),
                                 mpath=task.mpath or [])
            self._emit(task)
            self._push("upload", task)

    def choose(self, task, section, path, remember=True):
        """Ручний вибір теки в плані; remember — запам'ятати для категорії."""
        if not ftpcat.folder_exists(ftpstate.tree(section) or {}, path):
            raise ValueError("Такої теки немає в дереві розділу (або це корінь розділу)")
        task.section, task.path, task.source, task.confidence = section, tuple(path), \
            ftpcat.RULE, 1.0
        task.name = os.path.basename(task.local)        # вільне ім'я в новій теці — заново
        if remember:
            ftpstate.remember(section, task.mpath, task.path)
        task.state, task.note = PLANNED, "Обрано вручну"
        self._emit(task)
        self._push("check", task)        # чи немає вже такого файлу в новій теці

    def cancel(self, task, keep_pending=False):
        """keep_pending — програма закривається: недолите доллється після запуску;
        інакше користувач передумав — забуваємо."""
        task.cancel_event.set()
        if not keep_pending:
            ftpstate.remove_pending(task.local)
        if task.state in (QUEUED, PLANNING):
            task.state, task.note = CANCELLED, "Скасовано"
            self._emit(task)

    def is_busy(self):
        return self.running is not None or bool(self._todo)

    def full_sections(self):
        return set(self._full)

    def _push(self, kind, task):
        with self._cond:
            self._todo.append((kind, task))
            self._cond.notify_all()

    def _emit(self, task):
        self.events.put(("task", task.id, None))

    # ── потік ──
    def _worker(self):
        while True:
            with self._cond:
                while not self._todo:
                    self._cond.wait()
                kind, task = self._todo.popleft()
            if task.cancel_event.is_set() and task.state == CANCELLED:
                continue
            self.running = task
            try:
                if not is_video(task.local):
                    # Запобіжник: звідки б не прийшов звук (старе недолите тощо).
                    task.state, task.note = ERROR, NOT_VIDEO
                    ftpstate.remove_pending(task.local)
                elif kind == "plan":
                    self._plan(task)
                elif kind == "check":
                    self._check_existing(task)
                else:
                    self._upload(task)
            except Exception as exc:
                applog.error(f"FTP: {task.name} — {kind} не вдався", exc)
                task.state, task.note = ERROR, human_error(exc)
            finally:
                self.running = None
                self._emit(task)

    def _client_ready(self):
        if self._client is None:
            self._client = self._connect()
        else:
            self._client.keepalive()
        return self._client

    def _tree(self, section):
        tree = ftpstate.tree(section)
        if tree is None:
            applog.info(f"FTP: читаю дерево тек {section}")
            ftpstate.save_tree(self._client_ready().read_tree([section]))
            tree = ftpstate.tree(section)
        return tree

    def _current_section(self, after=None):
        order = list(self._sections())
        if after in order:
            order = order[order.index(after) + 1:]
        return next((s for s in order if s not in self._full), None)

    # ── план ──
    def _plan(self, task):
        uploaded = ftpstate.uploaded(task.local)
        if uploaded:
            task.ftp_path, task.state, task.note = uploaded, ALREADY, "Уже залито раніше"
            return
        if task.mpath is None:
            info = self._product_info(task.product_id) if task.product_id else None
            task.product = info or {}
            task.mpath = task.product.get("mpath") or []
            task.site = " → ".join(task.product.get("crumbs_ua") or [])
        section = self._current_section()
        if section is None:
            task.state, task.note = ERROR, "Усі розділи FTP забиті"
            return
        self._resolve_in(task, section)
        if task.path:
            self._check_existing(task)

    def _resolve_in(self, task, section):
        product = task.product or {"mpath": task.mpath}
        r = ftpcat.resolve(self._tree(section), product, ftpstate.index(section),
                           ftpstate.rules(section))
        task.section, task.path, task.source, task.confidence = section, r.path, r.source, \
            r.confidence
        task.name = os.path.basename(task.local)        # вільне ім'я в новій теці — заново
        if r.path:
            task.state, task.note = PLANNED, ftpcat.SOURCE_LABELS[r.source]
        elif not task.mpath:
            task.state, task.note = NEED_CHOICE, "Товару немає на сайті — оберіть теку"
        else:
            task.state, task.note = NEED_CHOICE, "Теку не визначено — оберіть"

    def _check_existing(self, task):
        """Чи це відео вже в теці: файл того самого розміру під ім'ям товару
        (ID.mp4, ID_2.mp4…) — «уже на FTP». Якщо ж ім'я зайняте іншим відео (у
        товару кілька роликів, колись залитих іншими), — перше вільне: ID_2.mp4,
        ID_3.mp4… Чужих файлів програма не замінює."""
        if not task.path:
            return
        folder = [task.section] + list(task.path)
        key = "/".join(folder)
        if key not in self._files:
            self._files[key] = self._client_ready().list_files(folder)
        files = self._files[key]
        size = os.path.getsize(task.local)
        names = product_names(os.path.basename(task.local))
        same = next((n for n in [task.name] + names if files.get(n) == size), None)
        if same:
            task.name, task.ftp_path = same, f"{key}/{same}"
            task.state, task.note = ALREADY, "Уже є на FTP (той самий розмір)"
            ftpstate.mark_uploaded(task.local, task.ftp_path)
            ftpstate.remove_pending(task.local)
            return
        if task.name in files:
            # Лише далі за списком: другий ролик (ID_2) не займе вільне ID.mp4.
            later = names[names.index(task.name) + 1:] if task.name in names else names
            free = next((n for n in later if n not in files), None)
            if free is None:
                task.state = ERROR
                task.note = f"У {task.folder} уже {MAX_NAME_SUFFIX} відео цього товару"
                return
            task.name = free
        if task.state != PLANNED:
            return
        notes = []
        if task.renamed:
            notes.append(f"{os.path.basename(task.local)} на FTP — інше відео, заллю як {task.name}")
        part = files.get(task.name + ftpclient.PART_SUFFIX)
        if part:
            notes.append(f"недолите з минулого разу ({part * 100 // max(1, size)}%) — доллється")
        if notes:
            note = "; ".join(notes)
            task.note = note[0].upper() + note[1:]

    # ── заливання ──
    def _upload(self, task):
        if task.cancel_event.is_set():
            task.state, task.note = CANCELLED, "Скасовано"
            return
        reread = False                  # тека зникла — перечитуємо дерево розділу раз
        attempt = 0                     # повтори після обриву (RETRY_WAITS)
        taken = 0                       # ім'я виявилось зайнятим — перевибір (MAX_RECHECKS)
        while True:
            if not task.path:
                task.state = NEED_CHOICE
                task.note = task.note or "Теку не визначено — оберіть"
                return
            folder = [task.section] + list(task.path)
            task.state = UPLOADING
            task.note = f"Заливаю в {task.folder}" + (f" як {task.name}…" if task.renamed else "…")
            self._emit(task)

            clock = {"t": time.monotonic(), "b": None}

            def progress(done, total):
                now = time.monotonic()
                if clock["b"] is None:
                    clock["b"] = done           # докачування: рахуємо від того, що вже було
                elif now - clock["t"] >= 0.5:
                    rate = (done - clock["b"]) / (now - clock["t"])
                    task.speed = rate if not task.speed else 0.7 * task.speed + 0.3 * rate
                    clock["t"], clock["b"] = now, done
                task.sent, task.total = done, total
                task.fraction = done / total if total else 0.0
                self.events.put(("progress", task.id, task.fraction))

            try:
                task.ftp_path = self._client_ready().upload(
                    task.local, folder, task.name, progress=progress,
                    cancel=task.cancel_event.is_set)
            except ftpclient.Cancelled:
                self._client = None
                task.state, task.note = CANCELLED, "Скасовано"
                return
            except ftpclient.Exists:
                # Ім'я зайняли вже після плану (інше завдання того ж товару, колега)
                # або недолите з минулого запуску встигло дозалитись перед закриттям:
                # перечитуємо теку — «уже на FTP» чи наступне вільне ім'я.
                if taken >= MAX_RECHECKS:
                    raise
                taken += 1
                self._files.pop("/".join(folder), None)
                self._check_existing(task)
                if task.state in (ALREADY, ERROR):
                    return
                continue
            except ftpclient.MissingFolder:
                if reread:
                    raise
                reread = True
                applog.warning(f"FTP: теки {task.folder} немає — перечитую теки {task.section}")
                ftpstate.save_tree(self._client_ready().read_tree([task.section]))
                self._files.clear()
                self._resolve_in(task, task.section)
                continue
            except ftpclient.NoSpace:
                self._full.add(task.section)
                nxt = self._current_section(after=task.section)
                applog.warning(f"FTP: {task.section} забитий — далі {nxt or 'нікуди'}")
                if nxt is None:
                    task.state, task.note = ERROR, "Усі розділи FTP забиті"
                    return
                old = task.section
                self._resolve_in(task, nxt)
                task.note = f"{old} забитий → {task.note}"
                if task.path:
                    self._check_existing(task)
                    if task.state in (ALREADY, ERROR):
                        return
                continue
            except (ftpclient.NoSpace, ftpclient.RootForbidden):
                raise
            except Exception as exc:
                # Обрив, тайм-аут, недолитий файл: з'єднання заново, докачування з місця обриву.
                self._client = None
                if attempt >= len(RETRY_WAITS):
                    applog.error(f"FTP: {task.name} не залито після {attempt + 1} спроб", exc)
                    raise
                wait = RETRY_WAITS[attempt]
                attempt += 1
                applog.warning(f"FTP: {task.name} — {exc}; повтор через {wait} с "
                               f"(спроба {attempt + 1} з {len(RETRY_WAITS) + 1})")
                task.state = UPLOADING
                task.note = (f"Зв'язок обірвався — повтор через {wait} с "
                             f"(спроба {attempt + 1} з {len(RETRY_WAITS) + 1})")
                self._emit(task)
                if task.cancel_event.wait(wait):
                    task.state, task.note = CANCELLED, "Скасовано"
                    return
                continue
            self._files.setdefault("/".join(folder), {})[task.name] = os.path.getsize(task.local)
            ftpstate.mark_uploaded(task.local, task.ftp_path)
            ftpstate.remove_pending(task.local)
            ftpstate.learn(task.section, task.mpath, task.path)
            task.state, task.fraction = UPLOADED, 1.0
            task.note = f"Залито як {task.name}" if task.renamed else "Залито"
            task.sent = task.total = os.path.getsize(task.local)
            applog.info(f"FTP: {task.local} → {task.ftp_path}")
            return


# Корінь FTP — це https://video.rozetka.com.ua/: залите в video/…/ID.mp4 відкривається
# за video.rozetka.com.ua/video/…/ID.mp4.
PUBLIC_URL = "https://video.rozetka.com.ua/"


def public_url(ftp_path):
    """«video/sport_i_zahoplennya/…/361484283.mp4» → посилання на відео на сайті."""
    return PUBLIC_URL + ftp_path.lstrip("/") if ftp_path else ""


def batch_progress(tasks):
    """Загальний стан заливання цих завдань: (готово файлів, усього файлів,
    байт залито, байт усього, швидкість байт/с, частка або None)."""
    tasks = [t for t in tasks if t.state in (QUEUED, UPLOADING, UPLOADED)]
    if not tasks:
        return None
    done = sum(1 for t in tasks if t.state == UPLOADED)
    total = sum(t.total for t in tasks)
    sent = sum(t.total if t.state == UPLOADED else t.sent for t in tasks)
    speed = sum(t.speed for t in tasks if t.state == UPLOADING)
    return done, len(tasks), sent, total, speed, (sent / total if total else None)


def describe_batch(progress):
    """«Заливається 3 з 10 · 45 з 120 МБ · 5,2 МБ/с · ще ~1 хв»."""
    done, count, sent, total, speed, _fraction = progress
    mb = 1024 * 1024
    parts = [f"Залито {done} з {count}" if done == count else f"Заливається {done + 1} з {count}"]
    if total:
        parts.append(f"{sent / mb:.0f} з {total / mb:.0f} МБ")
    if speed and done < count:
        parts.append(f"{speed / mb:.1f} МБ/с".replace(".", ","))
        left = (total - sent) / speed
        parts.append("ще ~" + (f"{int(left)} с" if left < 60 else f"{round(left / 60)} хв"))
    return "  ·  ".join(parts)


def human_error(exc):
    text = str(exc)
    low = text.lower()
    # 530 — лише як код відповіді сервера (на початку чи після «…: »): у тексті
    # бувають ID товару й розміри, і «590530123.mp4» не означає хибний пароль.
    if re.search(r"(?:^|: )530\b", text) or "login" in low and "fail" in low:
        return "FTP не пускає: невірний логін чи пароль"
    if "getaddrinfo" in low or "timed out" in low or "refused" in low:
        return "Немає з'єднання з FTP-сервером"
    return text.splitlines()[0][:200] if text else type(exc).__name__
