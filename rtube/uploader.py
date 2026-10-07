"""Заливання на FTP у фоні: план (куди класти кожен файл) і саме заливання.

Як і завантажувач, працює в окремому потоці й лише кладе події в чергу
(events): вікно розбирає їх у своєму потоці. Розділи — по черзі (video →
video2 → …): коли поточний забитий, файл перезіставляється в наступному.
"""

import collections
import itertools
import os
import queue
import threading
from dataclasses import dataclass, field

from . import applog, ftpcat, ftpclient, ftpstate, rozetka

_task_ids = itertools.count(1)

# Стани завдання заливання.
PLANNING, PLANNED, NEED_CHOICE, CONFIRM, ALREADY = \
    "planning", "planned", "need_choice", "confirm", "already"
QUEUED, UPLOADING, UPLOADED, ERROR, CANCELLED = \
    "queued", "uploading", "uploaded", "error", "cancelled"
READY = (PLANNED,)                       # можна заливати без питань
FINISHED = (UPLOADED, ALREADY, ERROR, CANCELLED)


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
    overwrite: bool = False             # на FTP інший файл з тим самим ім'ям — замінити
    state: str = PLANNING
    note: str = ""
    fraction: float = 0.0
    ftp_path: str = ""
    id: int = field(default_factory=lambda: next(_task_ids))
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self):
        self.name = self.name or os.path.basename(self.local)

    @property
    def folder(self):
        """«video/odyag_vzuttya_ta_aksesuari/odyag» або ""."""
        return "/".join((self.section,) + tuple(self.path)) if self.path else ""


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
            task.cancel_event = threading.Event()
            self._emit(task)
            self._push("upload", task)

    def choose(self, task, section, path, remember=True):
        """Ручний вибір теки в плані; remember — запам'ятати для категорії."""
        if not ftpcat.folder_exists(ftpstate.tree(section) or {}, path):
            raise ValueError("Такої теки немає в дереві розділу (або це корінь розділу)")
        task.section, task.path, task.source, task.confidence = section, tuple(path), \
            ftpcat.RULE, 1.0
        if remember:
            ftpstate.remember(section, task.mpath, task.path)
        task.state, task.note = PLANNED, "Обрано вручну"
        self._emit(task)
        self._push("check", task)        # чи немає вже такого файлу в новій теці

    def cancel(self, task):
        task.cancel_event.set()
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
                if kind == "plan":
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
        if r.path:
            task.state, task.note = PLANNED, ftpcat.SOURCE_LABELS[r.source]
        elif not task.mpath:
            task.state, task.note = NEED_CHOICE, "Товару немає на сайті — оберіть теку"
        else:
            task.state, task.note = NEED_CHOICE, "Теку не визначено — оберіть"

    def _check_existing(self, task):
        """Файл із таким ім'ям уже в теці: той самий розмір — «уже на FTP», інший —
        лише з підтвердженням (overwrite)."""
        if not task.path:
            return
        folder = [task.section] + list(task.path)
        key = "/".join(folder)
        if key not in self._files:
            self._files[key] = self._client_ready().list_files(folder)
        size = self._files[key].get(task.name)
        if size is None:
            return
        if size == os.path.getsize(task.local):
            task.ftp_path = f"{key}/{task.name}"
            task.state, task.note = ALREADY, "Уже є на FTP (той самий розмір)"
            ftpstate.mark_uploaded(task.local, task.ftp_path)
        elif not task.overwrite:
            task.state, task.note = CONFIRM, "На FTP інший файл з таким ім'ям — замінити?"

    # ── заливання ──
    def _upload(self, task):
        if task.cancel_event.is_set():
            task.state, task.note = CANCELLED, "Скасовано"
            return
        reread = False                  # тека зникла — перечитуємо дерево розділу раз
        while True:
            if not task.path:
                task.state = NEED_CHOICE
                task.note = task.note or "Теку не визначено — оберіть"
                return
            folder = [task.section] + list(task.path)
            task.state, task.note = UPLOADING, f"Заливаю в {task.folder}…"
            self._emit(task)

            def progress(done, total):
                task.fraction = done / total if total else 0.0
                self.events.put(("progress", task.id, task.fraction))

            try:
                task.ftp_path = self._client_ready().upload(
                    task.local, folder, task.name, progress=progress,
                    cancel=task.cancel_event.is_set, overwrite=task.overwrite)
            except ftpclient.Cancelled:
                self._client = None
                task.state, task.note = CANCELLED, "Скасовано"
                return
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
                    if task.state in (ALREADY, CONFIRM):
                        return
                continue
            self._files.setdefault("/".join(folder), {})[task.name] = os.path.getsize(task.local)
            ftpstate.mark_uploaded(task.local, task.ftp_path)
            ftpstate.learn(task.section, task.mpath, task.path)
            task.state, task.note, task.fraction = UPLOADED, "Залито", 1.0
            applog.info(f"FTP: {task.local} → {task.ftp_path}")
            return


def human_error(exc):
    text = str(exc)
    low = text.lower()
    if "530" in low or "login" in low and "fail" in low:
        return "FTP не пускає: невірний логін чи пароль"
    if "getaddrinfo" in low or "timed out" in low or "refused" in low:
        return "Немає з'єднання з FTP-сервером"
    return text.splitlines()[0][:200] if text else type(exc).__name__
