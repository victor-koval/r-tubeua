"""Аналіз посилання й черга завантажень поверх Python-API yt-dlp.

Усе тут працює у фонових потоках. В інтерфейс нічого не пишемо напряму —
лише кладемо події в чергу, яку вікно розбирає у своєму потоці: tkinter
не терпить викликів з інших потоків і рано чи пізно падає без пояснень.
"""

import collections
import copy
import glob
import itertools
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, ISO639Utils

from . import applog, ffinstall, formats, tools

_job_ids = itertools.count(1)


class Cancelled(DownloadCancelled):
    msg = "Скасовано користувачем"


# ── дочірні процеси yt-dlp ───────────────────────────────────────────────
# ffmpeg (склеювання, субтитри, mp3) і node (JS-челендж YouTube) yt-dlp
# запускає через свій yt_dlp.utils.Popen і чекає на них блокуючим викликом.
# Хуки прогресу в цей час мовчать, тож без цього реєстру «Скасувати» діяло б
# лише після того, як ffmpeg доробить своє — на 4K-відео це хвилини.
_children = {}                  # id потоку → {Popen, …}
_children_lock = threading.Lock()


def _track_child_processes():
    popen = yt_dlp.utils.Popen
    if getattr(popen, "_rtube_tracked", False):
        return
    original_init = popen.__init__

    def tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        with _children_lock:
            procs = _children.setdefault(threading.get_ident(), set())
            procs.difference_update([p for p in procs if p.poll() is not None])
            procs.add(self)

    popen.__init__ = tracked_init
    popen._rtube_tracked = True


def kill_children(thread_ident):
    """Зупиняє ffmpeg/node, запущені yt-dlp з цього потоку. Повертає, скільки вбито."""
    with _children_lock:
        procs = list(_children.get(thread_ident, ()))
    killed = 0
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.kill()
                killed += 1
            except OSError:
                pass
    return killed


_track_child_processes()


# Завдання, яке зараз виконує цей потік (див. _Runner.run).
_current = threading.local()


def _cancelling():
    """Чи скасовано завдання цього потоку: тоді «ERROR» від yt-dlp — лише
    наслідок вбитого ffmpeg, а не справжня помилка."""
    job = getattr(_current, "job", None)
    return job is not None and job.cancel_event.is_set()


def base_opts():
    return {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "noplaylist": True,          # &list= у посиланні не тягне весь плейлист
        "logger": applog.YtdlpLogger(quiet_errors=_cancelling),
        "js_runtimes": tools.find_js_runtimes(),
        "windowsfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
    }


def analyze(url):
    """info ролика без завантаження. Кидає DownloadError із текстом yt-dlp."""
    opts = base_opts()
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise DownloadError("За посиланням немає жодного відео")
            info = entries[0]
        return ydl.sanitize_info(info)


def expand_collection(url):
    """Плейлист або канал → (назва, [(посилання, назва ролика), …]).

    extract_flat не відкриває кожен ролик, тож навіть канал на 357 відео
    розгортається за кілька секунд, і назви є одразу.
    """
    opts = base_opts()
    opts.update({"extract_flat": "in_playlist", "noplaylist": False, "skip_download": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries, seen = [], set()
    for entry in info.get("entries") or []:
        if not entry:
            continue
        link = tools.clean_url(entry.get("url") or entry.get("webpage_url") or entry.get("id") or "")
        if "watch?v=" not in link or link in seen:
            continue        # вкладки каналу, «живі» трансляції без посилання тощо
        seen.add(link)
        entries.append((link, entry.get("title") or link))
    if not entries:
        raise DownloadError("За посиланням не знайдено жодного відео")
    return info.get("title") or url, entries


# Склеювання тимчасово тримає на диску і частини, і готовий файл.
DISK_FACTOR = 2.1


def needed_bytes(part_sizes):
    """Скільки місця треба під завантаження з частинами такого розміру."""
    return int(sum(part_sizes) * DISK_FACTOR)


def track_titles(info, fmt):
    """[(код мови, назва), …] для аудіодоріжок рядка формату, у їхньому порядку."""
    by_id = {f.get("format_id"): f for f in info.get("formats") or []}
    choices = {a.lang: a for a in formats.build_choices(info).audios}
    tracks = []
    for fid in fmt.split("+"):
        f = by_id.get(fid) or {}
        if f.get("vcodec") not in (None, "none") and not formats.is_audio_only(f):
            continue
        lang = f.get("language") or ""
        choice = choices.get(lang)
        tracks.append((lang, choice.label if choice else formats.lang_name(lang)))
    return tracks


def track_metadata_args(info, fmt, source_url=None):
    """Аргументи ffmpeg, що підписують аудіодоріжки мовою й назвою.

    Без них склеєні доріжки мають мову «und», і плеєр показує «Доріжка 1 /
    Доріжка 2». Передаються склеювачу yt-dlp (postprocessor_args), тож файл
    не переписується вдруге. Склеювач кладе доріжки в порядку формату:
    обрана — a:0, оригінал — a:1.

    source_url пишеться в коментар файлу: за ним pick_id_target упізнає, з
    якого ролика вже скачано «590312170.mp4».
    """
    args = ["-metadata", f"comment={source_url}"] if source_url else []
    for i, (lang, title) in enumerate(track_titles(info, fmt)):
        base = formats.base_lang(lang)
        # short2long дивиться лише на дві перші літери: «fil» (філіппінська)
        # перетворилася б на «fin» (фінська). Трилітерний код уже готовий.
        code = base if len(base) == 3 else ISO639Utils.short2long(base) if base else None
        if code:
            args += [f"-metadata:s:a:{i}", f"language={code}"]
        # mkv читає назву з title, mp4 — з handler_name.
        args += [f"-metadata:s:a:{i}", f"title={title}",
                 f"-metadata:s:a:{i}", f"handler_name={title}",
                 f"-disposition:a:{i}", "default" if i == 0 else "0"]
    return args


ALREADY_NOTE = "Уже є в теці — не качав вдруге"


class AlreadyHave(Exception):
    """Файл тієї ж якості вже лежить у теці — качати нічого не треба."""

    def __init__(self, path):
        super().__init__(path)
        self.path = path


def quality_tag(video_key):
    """(720, 30, …) → «720p», (1080, 60, …) → «1080p60» — як у списку якостей."""
    height, fps = video_key[0], video_key[1] if len(video_key) > 1 else 30
    return f"{height}p" + ("60" if fps == 60 else "")


def final_ext(info, fmt, job):
    """Розширення готового файлу: після склеювання — контейнер, після
    конвертації — mp3, інакше — розширення самого формату."""
    if job.audio_only and job.container == "mp3":
        return "mp3"
    if "+" in fmt:
        return job.container
    f = next((x for x in info.get("formats") or [] if x.get("format_id") == fmt), {})
    return f.get("ext") or job.container


def probe_quality(path):
    """Якість відео у файлі («1080» для 1920×1080 і для вертикального
    1080×1920) — та сама міра, що й video_key[0]. None — не вдалося дізнатися."""
    ffprobe = tools.find_ffprobe()
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        if not out:
            return None
        width, height = (int(x) for x in out.splitlines()[0].split(",")[:2])
        return formats.quality_of(width, height)
    except Exception:
        return None


def probe_comment(path):
    """Коментар файлу (туди пишемо посилання на ролик) або None."""
    ffprobe = tools.find_ffprobe()
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format_tags=comment",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return out or None
    except Exception:
        return None


MAX_ID_SUFFIX = 99


def pick_id_target(out_dir, product_id, ext, url, height,
                   comment_of=probe_comment, height_of=probe_quality):
    """Куди качати ролик для товару: (шлях, дія).

    дія «new» — файлу немає; «skip» — цей самий ролик тієї ж якості вже є;
    «overwrite» — цей самий ролик, але іншої якості (на FTP потрібен один
    файл, тож старий замінюється). Інший ролик того ж товару — наступне ім'я:
    590312170.mp4 → 590312170_2.mp4 → _3… Файл без коментаря (скачаний до
    цієї версії або іншою програмою) вважається іншим роликом.
    """
    for n in range(1, MAX_ID_SUFFIX + 1):
        name = product_id if n == 1 else f"{product_id}_{n}"
        path = os.path.join(out_dir, f"{name}.{ext}")
        if not os.path.isfile(path):
            return path, "new"
        if comment_of(path) == url:
            if height is None or height_of(path) == height:
                return path, "skip"
            return path, "overwrite"
    raise DownloadError(f"Для товару {product_id} уже {MAX_ID_SUFFIX} різних відео в теці")


# Скільки тримати info ролика для повторного використання: посилання на
# формати YouTube живуть кілька годин, година — із запасом.
INFO_TTL = 3600


def same_video_key(job):
    """Однакові ключі — однаковий файл: той самий ролик, якість, доріжки,
    контейнер і субтитри. Тоді другий товар отримує копію, а не нове завантаження."""
    return (job.url, tuple(job.video_key or ()), job.audio_lang, job.container,
            bool(job.keep_original), tuple(job.sub_key or ()), job.subs_mode)


def needs_ffmpeg(job, progressive=False):
    """Чи знадобиться ffmpeg: склеювання, mp3 або субтитри.

    Без нього обходяться лише «лише звук» у m4a без субтитрів і формати,
    де відео й звук в одному файлі (progressive).
    """
    if job.audio_only:
        return job.container == "mp3"
    return not progressive or bool(job.sub_key)


@dataclass
class Job:
    """Одне завантаження.

    Відео, додане через картку, приходить із готовим info і точним вибором
    (video_key, audio_lang, sub_key). Відео з пакета чи відновленої черги —
    без info: тоді None у цих полях означає «вирішити після аналізу за prefs».
    """
    url: str
    title: str
    out_dir: str
    info: dict = None
    video_key: tuple = None
    audio_lang: str = None
    audio_label: str = ""
    container: str = "mp4"          # mp4 / mkv; для «лише звук» — m4a / mp3
    keep_original: bool = False
    sub_key: tuple = ()             # () — без субтитрів, None — вирішити за prefs
    subs_mode: str = "embed"        # embed / file
    product_id: str = ""            # ID товару — тоді файл зветься «590312170.mp4»
    # max_height: 0 — найкраща, AUDIO_ONLY — лише звук; audio: "uk" / "orig";
    # subs: "none" / "author_uk"
    prefs: dict = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_job_ids))
    state: str = "queued"           # queued / running / done / error / cancelled
    status: str = "У черзі"         # останній текст стану — для звіту й пізно створеного рядка
    filepath: str = ""
    keep_partial: bool = False      # при скасуванні лишити .part, щоб докачати потім
    pause_requested: bool = False   # «скасування» від паузи: повернути в чергу, а не скасовувати
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def audio_only(self):
        if self.video_key:
            return self.video_key[0] == formats.AUDIO_ONLY
        return self.prefs.get("max_height") == formats.AUDIO_ONLY

    def summary(self):
        parts = [f"ID {self.product_id}"] if self.product_id else []
        if self.audio_only:
            parts.append(f"лише звук, {self.container}")
        elif self.video_key:
            parts.append(f"{self.video_key[0]}p, {self.container}")
        else:
            limit = self.prefs.get("max_height")
            parts.append((f"до {limit}p" if limit else "найкраща якість") + f", {self.container}")
        if self.audio_label:
            parts.append(self.audio_label)
        elif self.audio_lang is None:
            parts.append("українська, якщо є" if self.prefs.get("audio", "uk") == "uk"
                         else "оригінальна доріжка")
        if self.sub_key:
            parts.append(f"субтитри {self.sub_key[0].removesuffix('-orig')}")
        return "  ·  ".join(parts)

    def clone(self):
        """Нове завдання з тими самими параметрами — для «Повторити»."""
        return Job(url=self.url, title=self.title, out_dir=self.out_dir, info=self.info,
                   video_key=self.video_key, audio_lang=self.audio_lang,
                   audio_label=self.audio_label, container=self.container,
                   keep_original=self.keep_original, sub_key=self.sub_key,
                   subs_mode=self.subs_mode, product_id=self.product_id,
                   prefs=dict(self.prefs))


def apply_prefs(job, info):
    """Заповнює невирішені поля завдання за prefs — тими ж правилами, що й
    картка відео: якість не вища за ліміт, українська доріжка, якщо є,
    субтитри від автора лише тоді, коли української доріжки немає."""
    prefs = job.prefs
    limit = prefs.get("max_height")
    preferred = "orig" if prefs.get("audio") == "orig" else "uk"
    choices = formats.build_choices(info, limit if isinstance(limit, int) else 0, preferred)
    if job.video_key is None:
        if limit == formats.AUDIO_ONLY:
            job.video_key = (formats.AUDIO_ONLY,)
        elif choices.videos:
            job.video_key = choices.videos[choices.default_video].key
        else:
            raise DownloadError("У ролику не знайдено жодного відеоформату")
    if job.audio_lang is None:
        audio = choices.audios[choices.default_audio] if choices.audios else None
        job.audio_lang = audio.lang if audio else ""
        job.audio_label = audio.label if audio else ""
    if job.sub_key is None:
        job.sub_key = ()
        audio_is_uk = formats.base_lang(job.audio_lang) == "uk"
        if prefs.get("subs") == "author_uk" and not audio_is_uk and not job.audio_only:
            job.sub_key = next((s.key for s in choices.subs
                                if s.key and not s.key[1] and formats.base_lang(s.key[0]) == "uk"),
                               ())
    if job.keep_original:
        orig = formats.original_lang(info)
        job.keep_original = orig is not None and orig != job.audio_lang and not job.audio_only


# YouTube обмежив запити (429, «підтвердіть, що ви не бот») — на великих
# пакетах таке буває. Замість одразу «Помилка» чекаємо й пробуємо знову.
RATE_LIMIT_WAITS = (60, 180, 600)
# Між роликами пакета — коротка пауза перед наступним аналізом: запити
# підряд без перерви YouTube і обмежує.
BATCH_GAP = 3


def is_rate_limited(exc):
    """Чи це обмеження запитів від YouTube, яке минає саме за кілька хвилин."""
    low = str(exc).lower()
    return "429" in low or "too many requests" in low or \
        ("sign in to confirm you" in low and "bot" in low)


class DownloadManager:
    """Одне завантаження за раз, решта чекає в черзі.

    Паралельно YouTube качати не варто: швидкість однаково ділиться, а
    ризик отримати 429 (забагато запитів) росте.
    """

    def __init__(self, done_files=None, on_done=None):
        """done_files — що вже скачано (з минулих запусків, queuestore.load_done);
        on_done(dict) — зберегти його, коли додався новий файл."""
        self.events = queue.Queue()
        self._pending = collections.deque()
        self._cond = threading.Condition()
        self._paused = False
        self._running = None
        self._worker_ident = None
        self._last_network = 0.0    # коли закінчилось попереднє завантаження (monotonic)
        # Що вже скачано в цьому сеансі: same_video_key → шлях. У шаблоні
        # Rozetka одне відео буває в 9 товарів — качаємо раз, решті копіюємо.
        self._done_files = dict(done_files or {})
        self._on_done = on_done
        self._info_cache = {}       # посилання → (час, info); див. INFO_TTL
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, job):
        with self._cond:
            self._pending.append(job)
            self._cond.notify_all()
        self._emit("state", job, "queued", "У черзі")

    def is_busy(self):
        """Чи качається щось просто зараз (для коректного закриття вікна)."""
        return self._running is not None

    @property
    def paused(self):
        return self._paused

    def pause(self):
        """Наступні не починаються; поточне зупиняється, лишаючи .part, і
        повертається на початок черги — після «Продовжити» докачається."""
        with self._cond:
            self._paused = True
        job = self._running
        if job is not None:
            self.cancel(job, keep_partial=True, pause=True)

    def resume(self):
        with self._cond:
            self._paused = False
            self._cond.notify_all()

    def cancel(self, job, keep_partial=False, pause=False):
        """Скасовує завдання. keep_partial — лишити недокачане, щоб продовжити
        після перезапуску (див. queuestore). pause — це не скасування, а пауза:
        завдання повернеться в чергу."""
        job.keep_partial = keep_partial
        job.pause_requested = pause
        job.cancel_event.set()
        if job.state == "queued":
            job.state = "cancelled"
            self._emit("state", job, "cancelled", "Скасовано")
        elif job is self._running and self._worker_ident is not None:
            # Якщо зараз працює ffmpeg чи node — зупиняємо їх одразу, а не
            # чекаємо, поки вони доробять (yt-dlp тоді кине помилку, яку
            # _Runner.run перетворить на скасування).
            if kill_children(self._worker_ident):
                applog.info(f"Скасування «{job.title}»: зупинено ffmpeg/node")

    def _emit(self, kind, job, *payload):
        self.events.put((kind, job.id, payload))

    def _next(self):
        """Наступне завдання; на паузі й з порожньою чергою — чекає."""
        with self._cond:
            while self._paused or not self._pending:
                self._cond.wait()
            return self._pending.popleft()

    def _worker(self):
        self._worker_ident = threading.get_ident()
        while True:
            job = self._next()
            if job.cancel_event.is_set():
                continue
            job.state = "running"
            self._running = job
            self._emit("state", job, "running", "Підготовка…")
            try:
                self._process(job)
            finally:
                self._running = None
                self._last_network = time.monotonic()

    def _process(self, job):
        for attempt in itertools.count():
            try:
                note = _Runner(job, self._emit, self._done_files, self._info_cache,
                               throttle=self._throttle).run()
            except Cancelled:
                self._stopped(job)
                return
            except Exception as exc:
                if is_rate_limited(exc) and attempt < len(RATE_LIMIT_WAITS):
                    wait = RATE_LIMIT_WAITS[attempt]
                    applog.warning(f"«{job.title}»: YouTube обмежив запити, повтор через "
                                   f"{wait} с (спроба {attempt + 2}): {exc}")
                    if self._sleep(job, wait, "YouTube обмежив запити — повтор через {}"):
                        continue
                    self._stopped(job)
                    return
                job.state = "error"
                applog.error(f"Завантаження «{job.title}» ({job.url}) не вдалося", exc)
                self._emit("state", job, "error", humanize_error(exc))
                return
            job.state = "done"
            if job.filepath and os.path.isfile(job.filepath):
                self._done_files[same_video_key(job)] = job.filepath
                if self._on_done:
                    self._on_done(dict(self._done_files))
            self._emit("state", job, "done", note or "Готово")
            return

    def _stopped(self, job):
        """Завдання перервали: або пауза (назад на початок черги), або скасування."""
        if job.pause_requested:
            job.pause_requested = False
            job.keep_partial = False
            job.cancel_event = threading.Event()
            job.state = "queued"
            with self._cond:
                self._pending.appendleft(job)
            self._emit("state", job, "queued", "Пауза — докачається з того ж місця")
            return
        job.state = "cancelled"
        self._emit("state", job, "cancelled", "Скасовано")

    def _sleep(self, job, seconds, text=None):
        """Чекає, показуючи відлік у рядку. False — завдання скасували чи поставили на паузу."""
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            if text:
                self._emit("progress", job, None, text.format(_eta(left)))
            if job.cancel_event.wait(min(1.0, left)):
                return False
        return not job.cancel_event.is_set()

    def _throttle(self, job):
        """Перед аналізом ролика з пакета: не частіше, ніж раз на BATCH_GAP секунд."""
        if not job.prefs:
            return
        left = self._last_network + BATCH_GAP - time.monotonic()
        if left > 0 and not self._sleep(job, left):
            raise Cancelled()


class _Runner:
    """Одне завантаження: параметри, прогрес, повтор у разі збою."""

    def __init__(self, job, emit, done_files=None, info_cache=None, throttle=None):
        self.job = job
        self.emit = emit
        self.throttle = throttle    # пауза перед запитом до YouTube (див. DownloadManager._throttle)
        self.done_files = done_files if done_files is not None else {}
        self.info_cache = info_cache if info_cache is not None else {}
        self.part_sizes = {}      # format_id → розмір у байтах
        self.done_bytes = {}      # format_id → скільки вже скачано
        self.temp_files = set()
        self.created_files = set()  # готові файли, створені саме цим запуском

    # ── параметри yt-dlp ──
    def _default_name(self, info, quality_in_name=False):
        """Ім'я без ID товару — латиницею, як для FTP: «kylymok_dlia_myshy_morskyi»,
        з «_uk», якщо доріжка не оригінальна, і з «_720p», якщо в теці вже
        лежить файл іншої якості."""
        job = self.job
        name = tools.translit_name(info.get("title") or "") or tools.translit_name(info.get("id") or "") \
            or "video"
        orig = formats.original_lang(info)
        if job.audio_lang and orig is not None and job.audio_lang != orig:
            name += f"_{formats.base_lang(job.audio_lang)}"
        if quality_in_name and not job.audio_only:
            name += f"_{quality_tag(job.video_key)}"
        return name

    def _opts(self, info, with_subs=True, quality_in_name=False, name=None):
        job = self.job
        fmt = formats.resolve_format(info, job.video_key, job.audio_lang, job.container,
                                     job.keep_original, audio_ext=job.container)
        if not fmt:
            raise DownloadError("Обраної якості вже немає серед форматів ролика — "
                                "проаналізуйте посилання ще раз")
        ffmpeg = tools.find_ffmpeg()
        needs_ffmpeg = "+" in fmt or (job.audio_only and job.container == "mp3") or \
            (with_subs and job.sub_key)
        if needs_ffmpeg and not ffmpeg and ffinstall.in_progress():
            # Користувач погодився поставити ffmpeg, коли додавав відео, —
            # чекаємо, поки він доставиться, замість одразу падати.
            self.emit("progress", job, None, "Чекаю, поки встановиться ffmpeg…")
            while not ffinstall.wait(0.5):
                if job.cancel_event.is_set():
                    raise Cancelled()
            ffmpeg = tools.find_ffmpeg()
        if needs_ffmpeg and not ffmpeg:
            raise DownloadError("Немає ffmpeg — без нього не склеїти відео зі звуком. "
                                "Натисніть «Встановити ffmpeg» унизу вікна, тоді «Повторити»")

        self.part_sizes = {}
        for fid in fmt.split("+"):
            f = next((x for x in info.get("formats") or [] if x.get("format_id") == fid), {})
            self.part_sizes[fid] = f.get("filesize") or f.get("filesize_approx") or 0

        # Ім'я — лише [a-z0-9_] (ID товару або транслітерація), тож у шаблон
        # yt-dlp його можна ставити без екранування «%».
        name = name or self._default_name(info, quality_in_name)
        opts = base_opts()
        opts.update({
            "format": fmt,
            "outtmpl": {"default": os.path.join(job.out_dir, name + ".%(ext)s")},
            "progress_hooks": [self._progress],
            "postprocessor_hooks": [self._postprocess],
            "postprocessors": [],
        })
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
        if "+" in fmt:
            opts["merge_output_format"] = job.container
        if fmt.count("+") >= 2:
            opts["allow_multiple_audio_streams"] = True
        if "+" in fmt and ffmpeg:
            meta = track_metadata_args(info, fmt, source_url=job.url)
            if meta:
                opts["postprocessor_args"] = {"merger+ffmpeg_o": meta}

        if job.audio_only and job.container == "mp3":
            opts["postprocessors"].append({"key": "FFmpegExtractAudio",
                                           "preferredcodec": "mp3", "preferredquality": "192"})
        if with_subs and job.sub_key and not job.audio_only:
            lang, auto = job.sub_key
            opts["writesubtitles"] = not auto
            opts["writeautomaticsub"] = auto
            opts["subtitleslangs"] = [lang]
            opts["subtitlesformat"] = "srt/vtt/best"
            # Перетворення в srt потрібне й для «окремого файлу» (vtt
            # відкривають не всі плеєри), і для mkv; у mp4 субтитри однаково
            # перекодуються в mov_text при вбудовуванні.
            opts["postprocessors"].append({"key": "FFmpegSubtitlesConvertor",
                                           "format": "srt", "when": "before_dl"})
            if job.subs_mode == "embed":
                opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle",
                                               "already_have_subtitle": False})
        return opts

    # ── хуки ──
    def _progress(self, d):
        if self.job.cancel_event.is_set():
            raise Cancelled()
        # Лише те, що справді пишемо зараз: «finished» без «downloading»
        # приходить і для файлу, скачаного колись раніше, — його при
        # скасуванні чіпати не можна.
        if d.get("status") == "downloading":
            for key in ("tmpfilename", "filename"):
                if d.get(key):
                    self.temp_files.add(d[key])
        info = d.get("info_dict") or {}
        fid = info.get("format_id")
        if d.get("status") == "finished":
            if fid in self.part_sizes:
                total = d.get("total_bytes") or d.get("downloaded_bytes") or 0
                self.part_sizes[fid] = total or self.part_sizes[fid]
                self.done_bytes[fid] = self.part_sizes[fid]
            return
        if d.get("status") != "downloading":
            return
        if fid not in self.part_sizes:
            # Субтитри теж проходять через цей хук, але вони крихітні.
            self.emit("progress", self.job, None, "Субтитри…")
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total:
            self.part_sizes[fid] = total
        self.done_bytes[fid] = d.get("downloaded_bytes") or 0

        all_total = sum(self.part_sizes.values())
        fraction = sum(self.done_bytes.values()) / all_total if all_total else None
        ids = list(self.part_sizes)
        if len(ids) == 1:
            what = "Звук" if self.job.audio_only else "Файл"
        elif ids.index(fid) == 0:
            what = "Відео"
        else:
            what = "Звук" if ids.index(fid) == 1 else "Оригінальна доріжка"
        done = sum(self.done_bytes.values())
        bits = [what]
        if d.get("speed"):
            bits.append(f"{d['speed'] / 1024 / 1024:.1f} МБ/с")
        if all_total:
            bits.append(f"{done / 1024 / 1024:.1f} з {all_total / 1024 / 1024:.1f} МБ")
        # ETA від yt-dlp — лише для поточної частини; поки качається відео,
        # він не враховує звук, що йде слідом. Рахуємо на весь обсяг.
        if d.get("speed") and all_total:
            bits.append(f"ще ~{_eta((all_total - done) / d['speed'])}")
        self.emit("progress", self.job, fraction, "  ·  ".join(bits))

    def _postprocess(self, d):
        path = (d.get("info_dict") or {}).get("filepath")
        if d.get("status") == "finished":
            if path:
                self.job.filepath = path
                if d.get("postprocessor") in ("Merger", "ExtractAudio"):
                    self.created_files.add(path)
            return
        if d.get("status") != "started":
            return
        # Між етапами ffmpeg хук прогресу мовчить, тож скасування
        # перевіряємо й тут — інакше кнопка не діяла б, поки йде склеювання.
        if self.job.cancel_event.is_set():
            raise Cancelled()
        # Назви — pp_key(): ім'я класу без «FFmpeg» і «PP».
        names = {
            "Merger": "Склеювання відео й звуку…",
            "EmbedSubtitle": "Вбудовування субтитрів…",
            "ExtractAudio": "Конвертація в MP3…",
        }
        text = names.get(d.get("postprocessor"))
        if text:
            self.emit("progress", self.job, 1.0, text)

    # ── запуск ──
    def run(self):
        _current.job = self.job
        try:
            return self._run()
        except AlreadyHave as have:
            self.job.filepath = have.path
            return ALREADY_NOTE
        except Exception as exc:
            # Під час скасування падає будь-що: вбитий ffmpeg, перерваний
            # node, наш Cancelled із хука. Усе це — скасування, не помилка.
            if self.job.cancel_event.is_set():
                if not self.job.keep_partial:
                    self._cleanup()
                if isinstance(exc, Cancelled):
                    raise
                raise Cancelled() from exc
            raise
        finally:
            _current.job = None

    def _prepare(self):
        """Аналіз і вибір для завдань із пакета чи відновленої черги."""
        job = self.job
        if job.info is None:
            cached = self.info_cache.get(job.url)
            if cached and time.monotonic() - cached[0] < INFO_TTL:
                job.info = cached[1]        # той самий ролик для іншого товару
            else:
                if self.throttle:
                    self.throttle(job)
                self.emit("progress", job, None, "Аналіз…")
                job.info = analyze(job.url)
                self.info_cache[job.url] = (time.monotonic(), job.info)
            job.title = job.info.get("title") or job.title
        if job.video_key is None or job.audio_lang is None or job.sub_key is None:
            apply_prefs(job, job.info)
        self.emit("meta", job, job.title, job.summary())
        if job.cancel_event.is_set():
            raise Cancelled()

    def _copy_from_sibling(self):
        """Той самий ролик із тими самими параметрами вже скачано для іншого
        товару — копіюємо файл під цим ID замість повторного завантаження.
        None — копіювати нема з чого."""
        job = self.job
        source = self.done_files.get(same_video_key(job))
        if not source or not os.path.isfile(source):
            return None
        ext = os.path.splitext(source)[1].lstrip(".")
        height = None if job.audio_only else job.video_key[0]
        target, action = pick_id_target(job.out_dir, job.product_id, ext, job.url, height)
        same_file = os.path.normcase(os.path.abspath(target)) == \
            os.path.normcase(os.path.abspath(source))
        if action == "skip" or same_file:
            raise AlreadyHave(target)
        self.emit("progress", job, None, f"Копіюю {os.path.basename(source)}…")
        shutil.copy2(source, target)
        # Субтитри окремим файлом — теж під новим ім'ям.
        src_stem, dst_stem = os.path.splitext(source)[0], os.path.splitext(target)[0]
        for srt in glob.glob(glob.escape(src_stem) + ".*.srt"):
            shutil.copy2(srt, dst_stem + srt[len(src_stem):])
        job.filepath = target
        applog.info(f"{target}: копія {source} (той самий ролик)")
        return f"Готово — копія {os.path.basename(source)} (те саме відео)"

    def _run(self):
        job = self.job
        os.makedirs(job.out_dir, exist_ok=True)
        note = ""
        self._prepare()
        if job.product_id:
            copied = self._copy_from_sibling()
            if copied:
                return copied
        try:
            info = self._download(job.info, with_subs=True)
        except DownloadError as exc:
            if job.cancel_event.is_set():
                raise
            text = str(exc).lower()
            if job.sub_key and "subtitle" in text:
                # Автопереклад YouTube часто віддає 429 — краще відео без
                # субтитрів, ніж нічого.
                applog.warning(f"Субтитри не завантажились, повтор без них: {exc}")
                note = "Готово, але без субтитрів (YouTube їх не віддав)"
                info = self._download(job.info, with_subs=False)
            elif "403" in text or "expired" in text or "forbidden" in text:
                # Посилання на формати живуть кілька годин — якщо ролик довго
                # стояв у черзі, беремо info заново.
                applog.warning(f"Посилання застаріли, отримуємо info заново: {exc}")
                self.emit("progress", job, None, "Оновлення посилань…")
                fresh = analyze(job.url)
                info = self._download(fresh, with_subs=True)
            else:
                raise
        downloads = (info or {}).get("requested_downloads") or []
        if downloads and downloads[-1].get("filepath"):
            job.filepath = downloads[-1]["filepath"]
        elif info and info.get("filepath"):
            job.filepath = info["filepath"]
        return note

    def _download(self, info, with_subs):
        if self.job.product_id:
            opts = self._id_opts(info, with_subs)
            existing = None
        else:
            opts = self._opts(info, with_subs)
            existing = self._existing_target(info, opts)
        if existing:
            # yt-dlp, побачивши файл із таким іменем, мовчки пропустив би
            # завантаження — і на запит 720p лишився б старий 1080p під
            # «Готово». Тож вирішуємо самі: та сама якість — нічого не качаємо,
            # інша — новий файл із якістю в імені.
            if self._same_quality(existing):
                applog.info(f"Уже є: {existing} — не качаю вдруге")
                raise AlreadyHave(existing)
            opts = self._opts(info, with_subs, quality_in_name=True)
            existing = self._existing_target(info, opts)
            if existing:
                applog.info(f"Уже є: {existing} — не качаю вдруге")
                raise AlreadyHave(existing)
        self._check_disk_space()
        applog.info(f"Завантаження {self.job.url}: format={opts['format']}, "
                    f"контейнер={self.job.container}, субтитри={self.job.sub_key if with_subs else '—'}")
        with yt_dlp.YoutubeDL(opts) as ydl:
            # Як --load-info-json: info з аналізу вже містить розшифровані
            # посилання, тож YouTube вдруге не питаємо, а ID на кшталт
            # 140-19 гарантовано відповідають тим самим доріжкам.
            return ydl.process_ie_result(ydl.sanitize_info(copy.deepcopy(info), True),
                                         download=True)

    def _id_opts(self, info, with_subs):
        """Ім'я з ID товару: 590312170, 590312170_2… — див. pick_id_target."""
        job = self.job
        probe = self._opts(info, with_subs, name=job.product_id)
        ext = final_ext(info, probe["format"], job)
        height = None if job.audio_only else job.video_key[0]
        path, action = pick_id_target(job.out_dir, job.product_id, ext, job.url, height)
        if action == "skip":
            applog.info(f"Уже є: {path} (той самий ролик) — не качаю вдруге")
            raise AlreadyHave(path)
        name = os.path.splitext(os.path.basename(path))[0]
        opts = probe if name == job.product_id else self._opts(info, with_subs, name=name)
        if action == "overwrite":
            # Той самий ролик іншої якості: на FTP потрібен один файл на ролик.
            applog.info(f"{path}: той самий ролик іншої якості — перезаписую")
            opts["overwrites"] = True
        return opts

    def _existing_target(self, info, opts):
        """Шлях готового файлу, якщо він уже лежить у теці, інакше None.

        Ім'я будує сам yt-dlp (prepare_filename) з тими ж outtmpl і
        windowsfilenames — інакше не збіглися б заміни на кшталт " → ＂.
        """
        ext = final_ext(info, opts["format"], self.job)
        params = {"outtmpl": opts["outtmpl"], "windowsfilenames": True, "quiet": True,
                  "logger": applog.YtdlpLogger()}
        with yt_dlp.YoutubeDL(params) as ydl:
            path = ydl.prepare_filename({**info, "ext": ext})
        return path if os.path.isfile(path) else None

    def _same_quality(self, path):
        """Чи наявний файл тієї якості, яку просять зараз."""
        if self.job.audio_only:
            return True     # ім'я й розширення ті самі — це той самий звук
        return probe_quality(path) == self.job.video_key[0]

    def _check_disk_space(self):
        need = needed_bytes(self.part_sizes.values())
        if not need:
            return      # розміри невідомі (буває в HLS) — не вгадуємо
        try:
            free = shutil.disk_usage(self.job.out_dir).free
        except OSError:
            return
        if free < need:
            raise DownloadError(f"Не вистачає місця на диску: потрібно ~{formats.format_size(need)}, "
                                f"вільно {formats.format_size(free)}")

    def _cleanup(self):
        """Прибирає все, що лишив скасований запуск: .part, проміжні .fNNN,
        тимчасовий файл склеювання і вже склеєний, але не доведений до кінця файл."""
        candidates = set(self.created_files)
        for path in self.temp_files:
            stem = os.path.splitext(path)[0]
            # «Назва [uk].f137.mp4» → «Назва [uk]»: від неї названо .temp-файл склеювання.
            title = os.path.splitext(stem)[0] if re.search(r"\.f[\w-]+$", stem) else stem
            candidates |= {path, path + ".part", path + ".ytdl",
                           *glob.glob(glob.escape(stem) + "*.part"),
                           *glob.glob(glob.escape(stem) + "*.part-Frag*"),
                           *glob.glob(glob.escape(stem) + "*.ytdl"),
                           *glob.glob(glob.escape(title) + ".temp.*")}
        for candidate in candidates:
            try:
                if os.path.isfile(candidate):
                    os.remove(candidate)
            except OSError:
                pass


def _eta(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} хв {secs:02d} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} год {minutes:02d} хв"


def humanize_error(exc):
    """Короткий текст замість простирадла від yt-dlp (повне — у лозі)."""
    text = str(exc).replace("ERROR: ", "").strip()
    low = text.lower()
    if "sign in to confirm your age" in low or "age-restricted" in low:
        return "Відео з віковим обмеженням — YouTube не віддає його без входу в акаунт"
    if "private video" in low:
        return "Приватне відео"
    if "video unavailable" in low or "this video is not available" in low:
        return "Відео недоступне (видалене або заблоковане в регіоні)"
    if "sign in to confirm you" in low and "bot" in low:
        return "YouTube вимагає підтвердити, що ви не бот — спробуйте пізніше або з іншої мережі"
    if "429" in low or "too many requests" in low:
        return "YouTube тимчасово обмежив запити (429) — спробуйте за кілька хвилин"
    if "unsupported url" in low:
        return "Посилання не підтримується"
    if "getaddrinfo failed" in low or "failed to resolve" in low or "unable to connect" in low:
        return "Немає з'єднання з інтернетом"
    if "no space left" in low or "errno 28" in low:
        return "Не вистачає місця на диску"
    if "permission denied" in low or "errno 13" in low:
        return "Немає доступу до теки або файл відкритий в іншій програмі"
    first = text.splitlines()[0] if text else type(exc).__name__
    return first[:220]
