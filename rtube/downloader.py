"""Аналіз посилання й черга завантажень поверх Python-API yt-dlp.

Усе тут працює у фонових потоках. В інтерфейс нічого не пишемо напряму —
лише кладемо події в чергу, яку вікно розбирає у своєму потоці: tkinter
не терпить викликів з інших потоків і рано чи пізно падає без пояснень.
"""

import copy
import glob
import itertools
import os
import queue
import threading
from dataclasses import dataclass, field

import yt_dlp
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import DownloadCancelled, DownloadError, prepend_extension

from . import applog, formats, tools

_job_ids = itertools.count(1)


class Cancelled(DownloadCancelled):
    msg = "Скасовано користувачем"


def base_opts():
    return {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "noplaylist": True,          # &list= у посиланні не тягне весь плейлист
        "logger": applog.YtdlpLogger(),
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


# ISO 639-2 — у mp4 мітка мови доріжки мусить бути трилітерною.
ISO639_2 = {
    "uk": "ukr", "en": "eng", "ru": "rus", "pl": "pol", "de": "ger", "fr": "fre",
    "es": "spa", "it": "ita", "pt": "por", "ja": "jpn", "ko": "kor", "zh": "chi",
    "ar": "ara", "hi": "hin", "id": "ind", "iw": "heb", "he": "heb", "nl": "dut",
    "tr": "tur", "cs": "cze", "be": "bel", "kk": "kaz", "ro": "rum", "hu": "hun",
    "bn": "ben", "ta": "tam", "te": "tel", "ml": "mal", "pa": "pan", "vi": "vie",
}


class TagAudioPP(FFmpegPostProcessor):
    """Підписує аудіодоріжки мовою й назвою.

    Склеєні yt-dlp доріжки лишаються з мовою «und», і плеєр показує
    «Доріжка 1 / Доріжка 2» — не вгадаєш, де українська. Тут лише
    переписуються метадані, без перекодування (-c copy), тож це секунди.
    """

    def __init__(self, downloader, tracks):
        super().__init__(downloader)
        self.tracks = tracks        # [(код мови yt-dlp, назва), …] у порядку доріжок

    def run(self, info):
        path = info.get("filepath")
        if not path or not self.tracks or info.get("ext") not in ("mp4", "mkv", "m4a"):
            return [], info
        opts = list(self.stream_copy_opts(ext=info.get("ext")))
        for i, (lang, title) in enumerate(self.tracks):
            code = ISO639_2.get(formats.base_lang(lang))
            if code:
                opts += [f"-metadata:s:a:{i}", f"language={code}"]
            # mkv читає назву з title, mp4 — з handler_name.
            opts += [f"-metadata:s:a:{i}", f"title={title}",
                     f"-metadata:s:a:{i}", f"handler_name={title}"]
            opts += [f"-disposition:a:{i}", "default" if i == 0 else "0"]
        temp = prepend_extension(path, "temp")
        self.to_screen(f'Підпис доріжок у "{path}"')
        self.run_ffmpeg(path, temp, opts)
        os.replace(temp, path)
        return [], info


@dataclass
class Job:
    url: str
    title: str
    info: dict
    video_key: tuple
    audio_lang: str
    audio_label: str
    out_dir: str
    container: str = "mp4"          # mp4 / mkv; для «лише звук» — m4a / mp3
    keep_original: bool = False
    sub_key: tuple = ()
    subs_mode: str = "embed"        # embed / file
    id: int = field(default_factory=lambda: next(_job_ids))
    state: str = "queued"           # queued / running / done / error / cancelled
    filepath: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def audio_only(self):
        return bool(self.video_key) and self.video_key[0] == formats.AUDIO_ONLY

    def summary(self):
        parts = []
        if self.audio_only:
            parts.append(f"лише звук, {self.container}")
        else:
            parts.append(f"{self.video_key[0]}p, {self.container}")
        if self.audio_label:
            parts.append(self.audio_label)
        if self.sub_key:
            parts.append(f"субтитри {self.sub_key[0].removesuffix('-orig')}")
        return "  ·  ".join(parts)


class DownloadManager:
    """Одне завантаження за раз, решта чекає в черзі.

    Паралельно YouTube качати не варто: швидкість однаково ділиться, а
    ризик отримати 429 (забагато запитів) росте.
    """

    def __init__(self):
        self.events = queue.Queue()
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, job):
        self._jobs.put(job)
        self._emit("state", job, "queued", "У черзі")

    def cancel(self, job):
        job.cancel_event.set()
        if job.state == "queued":
            job.state = "cancelled"
            self._emit("state", job, "cancelled", "Скасовано")

    def _emit(self, kind, job, *payload):
        self.events.put((kind, job.id, payload))

    def _worker(self):
        while True:
            job = self._jobs.get()
            if job.cancel_event.is_set():
                continue
            job.state = "running"
            self._emit("state", job, "running", "Підготовка…")
            try:
                note = _Runner(job, self._emit).run()
                job.state = "done"
                self._emit("state", job, "done", note or "Готово")
            except Cancelled:
                job.state = "cancelled"
                self._emit("state", job, "cancelled", "Скасовано")
            except Exception as exc:
                job.state = "error"
                applog.error(f"Завантаження «{job.title}» ({job.url}) не вдалося", exc)
                self._emit("state", job, "error", humanize_error(exc))


class _Runner:
    """Одне завантаження: параметри, прогрес, повтор у разі збою."""

    def __init__(self, job, emit):
        self.job = job
        self.emit = emit
        self.part_sizes = {}      # format_id → розмір у байтах
        self.done_bytes = {}      # format_id → скільки вже скачано
        self.temp_files = set()

    # ── параметри yt-dlp ──
    def _opts(self, info, with_subs=True):
        job = self.job
        fmt = formats.resolve_format(info, job.video_key, job.audio_lang, job.container,
                                     job.keep_original, audio_ext=job.container)
        if not fmt:
            raise DownloadError("Обраної якості вже немає серед форматів ролика — "
                                "проаналізуйте посилання ще раз")
        ffmpeg = tools.find_ffmpeg()
        needs_ffmpeg = "+" in fmt or (job.audio_only and job.container == "mp3") or \
            (with_subs and job.sub_key)
        if needs_ffmpeg and not ffmpeg:
            raise DownloadError("Не знайдено ffmpeg — без нього не склеїти відео зі звуком. "
                                "Встановіть: winget install Gyan.FFmpeg")

        self.part_sizes = {}
        for fid in fmt.split("+"):
            f = next((x for x in info.get("formats") or [] if x.get("format_id") == fid), {})
            self.part_sizes[fid] = f.get("filesize") or f.get("filesize_approx") or 0

        name = "%(title).150B"
        orig = formats.original_lang(info)
        if job.audio_lang and orig is not None and job.audio_lang != orig:
            name += f" [{formats.base_lang(job.audio_lang)}]"
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
        if d.get("status") != "started":
            if d.get("status") == "finished" and (d.get("info_dict") or {}).get("filepath"):
                self.job.filepath = d["info_dict"]["filepath"]
            return
        # Назви — pp_key(): ім'я класу без «FFmpeg» і «PP».
        names = {
            "Merger": "Склеювання відео й звуку…",
            "EmbedSubtitle": "Вбудовування субтитрів…",
            "ExtractAudio": "Конвертація в MP3…",
            "TagAudio": "Підпис доріжок…",
        }
        text = names.get(d.get("postprocessor"))
        if text:
            self.emit("progress", self.job, 1.0, text)

    # ── запуск ──
    def run(self):
        job = self.job
        os.makedirs(job.out_dir, exist_ok=True)
        note = ""
        try:
            info = self._download(job.info, with_subs=True)
        except Cancelled:
            self._cleanup()
            raise
        except DownloadError as exc:
            if job.cancel_event.is_set():
                self._cleanup()
                raise Cancelled() from exc
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
        opts = self._opts(info, with_subs)
        applog.info(f"Завантаження {self.job.url}: format={opts['format']}, "
                    f"контейнер={self.job.container}, субтитри={self.job.sub_key if with_subs else '—'}")
        with yt_dlp.YoutubeDL(opts) as ydl:
            tracks = self._track_titles(info, opts["format"])
            if tracks and opts.get("ffmpeg_location") and not self.job.audio_only:
                ydl.add_post_processor(TagAudioPP(ydl, tracks), when="post_process")
            # Як --load-info-json: info з аналізу вже містить розшифровані
            # посилання, тож YouTube вдруге не питаємо, а ID на кшталт
            # 140-19 гарантовано відповідають тим самим доріжкам.
            return ydl.process_ie_result(ydl.sanitize_info(copy.deepcopy(info), True),
                                         download=True)

    @staticmethod
    def _track_titles(info, fmt):
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

    def _cleanup(self):
        """Прибирає .part і проміжні .fNNN-файли скасованого завантаження."""
        for path in self.temp_files:
            stem = os.path.splitext(path)[0]
            for candidate in {path, path + ".part", path + ".ytdl",
                              *glob.glob(glob.escape(stem) + "*.part"),
                              *glob.glob(glob.escape(stem) + "*.ytdl")}:
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
