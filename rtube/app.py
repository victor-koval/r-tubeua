"""Головне вікно R-TubeUA: посилання → вибір якості, доріжки, субтитрів → черга."""

import io
import os
import queue
import re
import threading
import time
import urllib.request
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:
    # Перетягування файлів у вікно. Без бібліотеки програма працює як раніше.
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD
    _DND_BASES = (TkinterDnD.DnDWrapper,)
except Exception:
    TkinterDnD = None
    _DND_BASES = ()

from . import (applog, appupdate, downloader, ffinstall, formats, notify, queuestore, report,
               settings, sheets, taskbar, tools, uikit, watchdog, ytupdate)
from .uikit import FONT_SMALL, FONT_UI, FONT_UI_BOLD, GREEN, GREEN_HOVER

APP_TITLE = "R-TubeUA"
APP_VERSION = "1.3.1"

DEFAULT_SIZE = (1000, 800)
MIN_SIZE = (880, 660)
THUMB_SIZE = (224, 126)
CLOSE_TIMEOUT = 5          # скільки чекати зупинки завантаження при закритті, с
ERROR_FLASH = 3            # скільки тримати червону смужку в панелі задач після помилки, с
CLIPBOARD_EVERY = 7        # буфер обміну перевіряємо кожне 7-ме опитування (~0,7 с)
# Рядок-віджет — ~25 мс на створення й розкладку: канал на 300 відео заморожував
# вікно на 7 секунд. Тож рядки є в завершених, поточного й лише найближчих у
# черзі; решта — одним підписом «… і ще N у черзі», рядки з'являються по ходу.
QUEUED_ROWS = 20

THEME_LIGHT, THEME_DARK, THEME_SYSTEM = "Світла", "Темна", "Системна"
THEMES = {THEME_LIGHT: "Light", THEME_DARK: "Dark", THEME_SYSTEM: "System"}

SUBS_EMBED, SUBS_FILE = "Вшити у відео", "Окремий файл .srt"
VIDEO_CONTAINERS = ("mp4", "mkv")
AUDIO_CONTAINERS = ("m4a", "mp3")
ACTIVE = ("queued", "running")
FINISHED = ("done", "error", "cancelled")


def _unique(labels):
    """Підписи в CTkOptionMenu мусять бути різними — інакше вибір не
    зіставити назад із варіантом (напр. «iw» і «he» — обидва «Іврит»)."""
    seen, out = {}, []
    for label in labels:
        n = seen.get(label, 0)
        seen[label] = n + 1
        out.append(label if n == 0 else f"{label} ({n + 1})")
    return out


def job_duration(job):
    """Тривалість ролика в секундах або None (ще не проаналізовано, пряма трансляція)."""
    duration = (job.info or {}).get("duration")
    return duration if isinstance(duration, (int, float)) and duration > 0 else None


class JobRow(ctk.CTkFrame):
    """Рядок у списку завантажень: назва, параметри, прогрес, дії."""

    def __init__(self, master, app, job):
        super().__init__(master, fg_color=uikit.SURFACE_RAISED, corner_radius=uikit.RADIUS)
        self.app = app
        self.job = job
        self.fraction = 0.0
        self.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self, text=job.title, font=FONT_UI_BOLD, anchor="w",
                                      justify="left")
        self.lbl_title.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=(8, 0))
        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=0, sticky="ew", padx=(12, 8))
        self.lbl_summary = ctk.CTkLabel(meta, text=job.summary(), font=FONT_SMALL, anchor="w",
                                        text_color=uikit.TEXT_MUTED)
        self.lbl_summary.pack(side="left")
        # Тривалість — клік копіює «3,27» (хвилин,секунд).
        self.lbl_duration = uikit.CopyLabel(meta, font=FONT_SMALL, text_color=uikit.STATE_INFO)
        self.lbl_duration.pack(side="left", padx=(10, 0))
        self.refresh_duration()
        self.bar = ctk.CTkProgressBar(self, height=8, progress_color=GREEN)
        self.bar.set(0)
        self.bar.grid(row=2, column=0, sticky="ew", padx=(12, 8), pady=(6, 2))
        self.lbl_status = ctk.CTkLabel(self, text="У черзі", font=FONT_SMALL, anchor="w",
                                       text_color=uikit.TEXT_MUTED)
        self.lbl_status.grid(row=3, column=0, sticky="ew", padx=(12, 8), pady=(0, 8))

        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.grid(row=0, column=1, rowspan=4, sticky="e", padx=(0, 10))
        self.btn_cancel = ctk.CTkButton(self.actions, text="Скасувати", width=96, height=30,
                                        fg_color=uikit.DANGER, hover_color=uikit.DANGER_HOVER,
                                        command=lambda: app.cancel_job(job))
        self.btn_cancel.pack(side="left")
        self.btn_open = uikit.SecondaryButton(self.actions, text="▶ Відкрити", width=96,
                                              height=30, command=self._open)
        self.btn_folder = uikit.SecondaryButton(self.actions, text="📁 У теці", width=84,
                                                height=30, command=self._folder)
        self.btn_retry = uikit.SecondaryButton(self.actions, text="↻ Повторити", width=104,
                                               height=30, command=lambda: app.retry(job))
        uikit.wrap_to_width(self.lbl_title)
        if job.state != "queued":
            self.set_state(job.state, job.status)     # рядок створено, коли завдання вже йшло

    def set_meta(self, title, summary):
        """Після відкладеного аналізу: справжня назва й обрана якість/доріжка."""
        self.lbl_title.configure(text=title)
        self.lbl_summary.configure(text=summary)
        self.refresh_duration()

    def refresh_duration(self):
        """Тривалість відома лише після аналізу (для пакета — коли дійде черга)."""
        seconds = job_duration(self.job)
        if seconds:
            value = uikit.format_min_sec(seconds)
            self.lbl_duration.set_value(value, f"⏱ {value}")
        else:
            self.lbl_duration.set_value("", "")

    def set_progress(self, fraction, text):
        if fraction is None:
            if self.bar.cget("mode") != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start()
        else:
            if self.bar.cget("mode") != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.fraction = max(0.0, min(1.0, fraction))
            self.bar.set(self.fraction)
        self.lbl_status.configure(text=text, text_color=uikit.TEXT_MUTED)

    def set_state(self, state, text):
        colors = {"done": uikit.STATE_OK, "error": uikit.STATE_ERROR,
                  "cancelled": uikit.STATE_WARN, "running": uikit.STATE_INFO}
        self.lbl_status.configure(text=text, text_color=colors.get(state, uikit.TEXT_MUTED))
        if state in FINISHED:
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.fraction = 1.0 if state == "done" else 0.0
            self.bar.set(self.fraction)
            self.btn_cancel.pack_forget()
        if state == "done":
            # «Готово», яке не зовсім «готово», — жовтим: інакше пропущене
            # завантаження виглядало як дуже швидке (так і сталося в колеги).
            if "без субтитрів" in text or text == downloader.ALREADY_NOTE:
                self.lbl_status.configure(text_color=uikit.STATE_WARN)
            if self.job.filepath:
                self.lbl_status.configure(text=f"{text}  ·  {os.path.basename(self.job.filepath)}")
            self.btn_open.pack(side="left", padx=(0, 6))
            self.btn_folder.pack(side="left")
        elif state in ("error", "cancelled"):
            self.btn_retry.pack(side="left")
        elif state == "running":
            self.bar.configure(mode="determinate")
            self.fraction = 0.0
            self.bar.set(0)
        elif state == "queued":
            # Після паузи: смужка не має крутитись, наче щось качається.
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.bar.set(self.fraction)

    def _open(self):
        path = self.job.filepath
        if path and os.path.isfile(path):
            try:
                os.startfile(path)
                return
            except OSError:
                pass
        uikit.open_path(self.job.out_dir)

    def _folder(self):
        if not uikit.select_in_explorer(self.job.filepath):
            uikit.open_path(self.job.out_dir)


class RTubeApp(ctk.CTk, *_DND_BASES):
    def __init__(self):
        super().__init__()
        scale = uikit.fit_scaling(self._window_scale())
        if scale < 1.0:
            ctk.set_widget_scaling(scale)
            ctk.set_window_scaling(scale)

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry(settings.get("geometry") or "{}x{}".format(*DEFAULT_SIZE))
        self.minsize(*MIN_SIZE)
        self.configure(fg_color=uikit.SURFACE_SUNKEN)
        uikit.apply_window_icon(self)

        self.manager = downloader.DownloadManager(done_files=queuestore.load_done(),
                                                  on_done=queuestore.save_done)
        self.ui_events = queue.Queue()     # результати аналізу з фонових потоків
        self.jobs = {}                      # job.id → Job: усе, що в списку
        self.rows = {}                      # job.id → JobRow — не для всіх, див. _materialize
        self.info = None
        self.choices = None
        self._analyze_token = 0
        self._analyzing = False
        self._thumb_image = None
        self._queue_dirty = False
        self._session = set()               # id завдань від останнього «все порожньо»
        self._was_active = False
        self._error_until = 0.0
        self._settings_window = None
        self._pending_id = ""                # ID товару з рядка «ID посилання» для картки
        self.taskbar = None
        self._polls = 0
        self._clip_last = self._read_clipboard()     # що було до старту — не підхоплюємо

        self.grid_columnconfigure(0, weight=1)
        # Черзі — мінімум місця на два рядки, хай навіть вікно низьке.
        self.grid_rowconfigure(3, weight=1, minsize=170)
        self._build_header()
        self._build_url_card()
        self._build_video_card()
        from .batch import BatchCard
        self.batch_card = BatchCard(self, self)
        self._build_jobs_card()
        self._build_statusbar()

        self._init_drop()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.after(100, self._poll)
        self.after(50, lambda: self.ent_url.focus_set())
        self.after(600, self._init_taskbar)
        self.after(300, self._restore_queue)
        threading.Thread(target=self._check_environment, daemon=True).start()
        self.watchdog = watchdog.Watchdog(threading.get_ident())
        self.watchdog.start()
        self._relaunch = False          # після закриття запустити нову копію (оновлення)
        self._ytdlp_ready = None
        applog.info(f"Запуск {APP_TITLE} v{APP_VERSION}")

    def _window_scale(self):
        try:
            return self._get_window_scaling()
        except Exception:
            return 1.0

    # ── побудова ──────────────────────────────────────────────────────────
    def _build_header(self):
        """Чорна шапка з зеленим акцентом — як у rozetka.com.ua і сусідніх програм."""
        header = ctk.CTkFrame(self, fg_color=uikit.HEADER_BG, corner_radius=0, height=60)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(header, text="R-TUBE", font=uikit.FONT_BRAND, text_color=GREEN).grid(
            row=0, column=0, padx=(22, 0), pady=14)
        ctk.CTkLabel(header, text=" UA", font=uikit.FONT_BRAND, text_color="#ffffff").grid(
            row=0, column=1, pady=14)
        ctk.CTkLabel(header, text="завантаження з YouTube з українською доріжкою",
                     font=FONT_SMALL, text_color=uikit.BLACK_40).grid(
            row=0, column=2, sticky="w", padx=14)

        style = {"fg_color": uikit.HEADER_HOVER, "hover_color": "#403b3b",
                 "text_color": uikit.HEADER_TEXT}
        ctk.CTkButton(header, text="⚙  Налаштування", width=130, command=self.open_settings,
                      **style).grid(row=0, column=3, padx=(0, 8))
        ctk.CTkButton(header, text="Лог", width=56, command=applog.open_log_folder,
                      **style).grid(row=0, column=4, padx=(0, 22))

    def _build_url_card(self):
        card = uikit.Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=22, pady=(18, 10))
        card.grid_columnconfigure(0, weight=1)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        row.grid_columnconfigure(0, weight=1)

        self.ent_url = ctk.CTkEntry(row, height=38, font=FONT_UI,
                                    placeholder_text="Посилання на відео, кілька посилань, "
                                                     "плейлист або канал YouTube")
        self.ent_url.grid(row=0, column=0, sticky="ew")
        self.ent_url.bind("<Return>", lambda e: self.analyze())
        uikit.bind_text_hotkeys(self.ent_url, on_paste=self._after_paste)

        uikit.SecondaryButton(row, text="Вставити", width=96, height=38,
                              command=self.paste_and_analyze).grid(row=0, column=1, padx=(8, 0))
        uikit.SecondaryButton(row, text="📄 З файлу…", width=110, height=38,
                              command=self.open_list_file).grid(row=0, column=2, padx=(8, 0))
        self.btn_analyze = ctk.CTkButton(row, text="Аналізувати", width=130, height=38,
                                         font=FONT_UI_BOLD, command=self.analyze)
        self.btn_analyze.grid(row=0, column=3, padx=(8, 0))

        self.lbl_url_hint = ctk.CTkLabel(card, text="Після вставки посилання аналіз "
                                                    "запускається сам. Enter — теж. "
                                                    "Файл зі списком можна перетягнути у вікно.",
                                         font=FONT_SMALL, text_color=uikit.TEXT_MUTED, anchor="w")
        self.lbl_url_hint.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))

    def _build_video_card(self):
        self.video_card = uikit.Card(self)
        card = self.video_card
        # Card розтягує колонку 0 — тут у ній прев'ю, і назва відʼїжджала
        # на середину вікна.
        card.grid_columnconfigure(0, weight=0)
        card.grid_columnconfigure(1, weight=1)

        self.lbl_thumb = ctk.CTkLabel(card, text="", width=THUMB_SIZE[0], height=THUMB_SIZE[1],
                                      fg_color=uikit.SURFACE_RAISED, corner_radius=uikit.RADIUS)
        self.lbl_thumb.grid(row=0, column=0, sticky="nw", padx=(16, 14), pady=(16, 8))

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=1, sticky="new", padx=(0, 16), pady=(16, 0))
        head.grid_columnconfigure(0, weight=1)
        self.lbl_title = ctk.CTkLabel(head, text="", font=uikit.FONT_VIDEO_TITLE, anchor="w",
                                      justify="left")
        self.lbl_title.grid(row=0, column=0, sticky="ew")
        uikit.wrap_to_width(self.lbl_title)
        self.lbl_meta = ctk.CTkLabel(head, text="", font=FONT_SMALL, anchor="w",
                                     text_color=uikit.TEXT_MUTED)
        self.lbl_meta.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.lbl_audio_hint = ctk.CTkLabel(head, text="", font=FONT_UI_BOLD, anchor="w",
                                           justify="left")
        self.lbl_audio_hint.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        # ── параметри ──
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 4))
        opts.grid_columnconfigure(1, weight=1)

        def label(text, row):
            ctk.CTkLabel(opts, text=text, font=FONT_UI_BOLD, anchor="w", width=130).grid(
                row=row, column=0, sticky="w", pady=4)

        label("Якість", 0)
        self.opt_video = ctk.CTkOptionMenu(opts, values=["—"], dynamic_resizing=False,
                                           command=lambda _: self._on_video_change())
        self.opt_video.grid(row=0, column=1, sticky="ew", pady=5)

        label("Звукова доріжка", 1)
        self.opt_audio = ctk.CTkOptionMenu(opts, values=["—"], dynamic_resizing=False,
                                           command=lambda _: self._on_audio_change())
        self.opt_audio.grid(row=1, column=1, sticky="ew", pady=5)
        self.keep_original_var = ctk.BooleanVar(value=bool(settings.get("keep_original")))
        self.chk_original = ctk.CTkCheckBox(opts, text="+ оригінал другою доріжкою",
                                            variable=self.keep_original_var, font=FONT_SMALL)
        self.chk_original.grid(row=1, column=2, sticky="w", padx=(12, 0))

        label("Субтитри", 2)
        self.opt_subs = ctk.CTkOptionMenu(opts, values=["—"], dynamic_resizing=False,
                                          command=lambda _: self._sync_controls())
        self.opt_subs.grid(row=2, column=1, sticky="ew", pady=5)
        self.subs_mode = ctk.CTkSegmentedButton(opts, values=[SUBS_EMBED, SUBS_FILE],
                                                selected_color=GREEN,
                                                selected_hover_color=GREEN_HOVER)
        self.subs_mode.grid(row=2, column=2, sticky="w", padx=(12, 0))

        label("Формат файлу", 3)
        box = ctk.CTkFrame(opts, fg_color="transparent")
        box.grid(row=3, column=1, columnspan=2, sticky="ew", pady=5)
        self.container = ctk.CTkSegmentedButton(box, values=list(VIDEO_CONTAINERS),
                                                selected_color=GREEN,
                                                selected_hover_color=GREEN_HOVER,
                                                command=lambda _: self._sync_controls())
        self.container.pack(side="left")
        self.lbl_container_hint = ctk.CTkLabel(box, text="", font=FONT_SMALL, anchor="w",
                                               text_color=uikit.TEXT_MUTED)
        self.lbl_container_hint.pack(side="left", padx=(12, 0))

        label("ID товару", 4)
        id_box = ctk.CTkFrame(opts, fg_color="transparent")
        id_box.grid(row=4, column=1, columnspan=2, sticky="ew", pady=5)
        self.id_var = ctk.StringVar()
        self.ent_id = ctk.CTkEntry(id_box, textvariable=self.id_var, width=160,
                                   placeholder_text="необов'язково")
        self.ent_id.pack(side="left")
        uikit.bind_text_hotkeys(self.ent_id)
        self.lbl_filename = ctk.CTkLabel(id_box, text="", font=FONT_SMALL, anchor="w",
                                         text_color=uikit.TEXT_MUTED)
        self.lbl_filename.pack(side="left", padx=(12, 0))
        self.id_var.trace_add("write", lambda *_: self._update_filename_hint())

        label("Зберегти в", 5)
        folder = ctk.CTkFrame(opts, fg_color="transparent")
        folder.grid(row=5, column=1, columnspan=2, sticky="ew", pady=5)
        folder.grid_columnconfigure(0, weight=1)
        self.dir_var = ctk.StringVar(value=settings.get("download_dir"))
        ctk.CTkEntry(folder, textvariable=self.dir_var, state="readonly").grid(
            row=0, column=0, sticky="ew")
        uikit.SecondaryButton(folder, text="Змінити…", width=96,
                              command=self._choose_dir).grid(row=0, column=1, padx=(8, 0))
        uikit.SecondaryButton(folder, text="📁", width=40,
                              command=lambda: uikit.open_path(self.dir_var.get())).grid(
            row=0, column=2, padx=(8, 0))

        self.btn_download = ctk.CTkButton(card, text="⬇  Завантажити", height=44,
                                          font=uikit.FONT_BIG_BUTTON, command=self.download)
        self.btn_download.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 14))

    def _build_jobs_card(self):
        card = uikit.Card(self)
        card.grid(row=3, column=0, sticky="nsew", padx=22, pady=(0, 10))
        card.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        head.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        self.lbl_jobs = ctk.CTkLabel(title, text="Завантаження", font=uikit.FONT_TITLE, anchor="w")
        self.lbl_jobs.pack(side="left")
        # Сума тривалостей завершених відео — клік копіює «12,34» (хвилин,секунд).
        self.lbl_total = uikit.CopyLabel(title, font=FONT_UI, text_color=uikit.STATE_INFO)
        self.lbl_total.pack(side="left", padx=(16, 0))
        # Кнопки з'являються, коли мають сенс; порядок сталий — колонки сітки.
        buttons = ctk.CTkFrame(head, fg_color="transparent")
        buttons.grid(row=0, column=1, sticky="e")
        self.btn_pause = uikit.SecondaryButton(buttons, text="⏸ Пауза", width=100, height=28,
                                               command=self.toggle_pause)
        self.btn_cancel_all = ctk.CTkButton(buttons, text="Скасувати все", width=120, height=28,
                                            fg_color=uikit.DANGER, hover_color=uikit.DANGER_HOVER,
                                            command=self.cancel_all)
        self.btn_retry_failed = uikit.SecondaryButton(buttons, text="↻ Невдалі", width=120,
                                                      height=28, command=self.retry_failed)
        self.btn_report = uikit.SecondaryButton(buttons, text="📊 Звіт", width=86, height=28,
                                                command=self.save_report)
        uikit.SecondaryButton(buttons, text="Прибрати завершені", width=150, height=28,
                              command=self._clear_finished).grid(row=0, column=4)
        self.jobs_list = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self.jobs_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self.jobs_list.grid_columnconfigure(0, weight=1)
        self.lbl_empty = ctk.CTkLabel(self.jobs_list, text="Поки що нічого. Можна додати "
                                      "кілька відео підряд — вони качатимуться по черзі.",
                                      font=FONT_SMALL, text_color=uikit.TEXT_MUTED)
        self.lbl_empty.grid(row=0, column=0, pady=18)
        self.lbl_more = ctk.CTkLabel(self.jobs_list, text="", font=FONT_SMALL,
                                     text_color=uikit.TEXT_MUTED)

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)
        self.lbl_status = ctk.CTkLabel(bar, text="Перевірка ffmpeg і JS-рантайму…",
                                       font=FONT_SMALL, text_color=uikit.TEXT_MUTED, anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="ew")
        self.lbl_update = ctk.CTkLabel(bar, text="", font=FONT_SMALL, anchor="e",
                                       text_color=uikit.STATE_OK)
        self.lbl_update.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.btn_restart = ctk.CTkButton(bar, text="Перезапустити", width=110, height=24,
                                         font=FONT_SMALL, command=self._restart)
        self.btn_ffmpeg = ctk.CTkButton(bar, text="Встановити ffmpeg", width=140, height=24,
                                        font=FONT_SMALL, command=self._install_ffmpeg)
        self.btn_ffmpeg_cancel = ctk.CTkButton(bar, text="Скасувати", width=90, height=24,
                                               font=FONT_SMALL, fg_color=uikit.DANGER,
                                               hover_color=uikit.DANGER_HOVER,
                                               command=ffinstall.cancel)
        self._ffmpeg_was_running = False

    # ── оточення ──────────────────────────────────────────────────────────
    def _check_environment(self):
        self._send_environment()
        appupdate.cleanup(APP_VERSION)
        # Свіжий yt-dlp — після перевірки оточення, щоб не гальмувати старт.
        if settings.get("ytdlp_autoupdate"):
            try:
                installed = ytupdate.check_and_install()
                if installed:
                    self.ui_events.put(("ytdlp_ready", installed))
            except Exception as exc:
                applog.error("Оновлення yt-dlp не вдалося — працюю на поточному", exc)
        if settings.get("app_autoupdate"):
            try:
                version = appupdate.check_and_download(APP_VERSION)
                if version:
                    self.ui_events.put(("app_ready", version))
            except Exception as exc:
                applog.error("Перевірка оновлення R-TubeUA не вдалася", exc)

    def _send_environment(self):
        try:
            import yt_dlp.version
            ffmpeg = tools.find_ffmpeg()
            runtimes = tools.probe_js_runtimes()
            self.ui_events.put(("env", yt_dlp.version.__version__, ffmpeg, runtimes))
        except Exception as exc:
            applog.error("Перевірка оточення не вдалася", exc)
            self.ui_events.put(("env", "?", None, []))

    def _install_ffmpeg(self):
        if ffinstall.start():
            applog.info("Встановлення ffmpeg розпочато")
        self.btn_ffmpeg.grid_remove()
        self.btn_ffmpeg_cancel.grid(row=0, column=3, padx=(8, 0))
        self._ffmpeg_was_running = True

    def _track_ffmpeg_install(self):
        """Прогрес встановлення ffmpeg — у рядку статусу; по завершенні —
        повторна перевірка оточення, щоб «ffmpeg ✗» змінився на «✓»."""
        st = ffinstall.status()
        if st["running"]:
            self._ffmpeg_was_running = True
            pct = f" ({st['fraction'] * 100:.0f}%)" if st["fraction"] else ""
            self.lbl_status.configure(text=st["text"] + pct, text_color=uikit.STATE_INFO)
            self.btn_ffmpeg_cancel.grid(row=0, column=3, padx=(8, 0))
        elif self._ffmpeg_was_running:
            self._ffmpeg_was_running = False
            self.btn_ffmpeg_cancel.grid_remove()
            if st["cancelled"]:
                self.lbl_status.configure(text="Встановлення ffmpeg скасовано",
                                          text_color=uikit.STATE_WARN)
                self.btn_ffmpeg.configure(text=f"Встановити ffmpeg (~{ffinstall.APPROX_SIZE_MB} МБ)")
                self.btn_ffmpeg.grid(row=0, column=3, padx=(8, 0))
            elif st["error"]:
                self.lbl_status.configure(text=f"ffmpeg не встановився: {st['error']}"[:220],
                                          text_color=uikit.STATE_ERROR)
                self.btn_ffmpeg.configure(text="Спробувати ще раз")
                self.btn_ffmpeg.grid(row=0, column=3, padx=(8, 0))
            else:
                threading.Thread(target=self._send_environment, daemon=True).start()

    def _show_environment(self, version, ffmpeg, runtimes):
        if ytupdate.state["source"] == "lib":
            version += " (оновлено)"
        usable = [r for r in runtimes if r[3]]
        outdated = [r for r in runtimes if not r[3]]
        parts = [f"yt-dlp {version}", "ffmpeg ✓" if ffmpeg else "ffmpeg ✗"]
        if usable:
            parts.append("JS: " + ", ".join(f"{n} {v}" for n, _p, v, _ok in usable) + " ✓")
        elif outdated:
            parts.append("JS: " + ", ".join(f"{n} {v or '?'}" for n, _p, v, _ok in outdated) + " ✗")
        else:
            parts.append("JS ✗")
        text = "  ·  ".join(parts)
        color = uikit.TEXT_MUTED
        if ffmpeg:
            self.btn_ffmpeg.grid_remove()
        elif ffinstall.in_progress():
            return      # рядок зараз показує прогрес встановлення
        else:
            text += "   —   без ffmpeg не склеїти відео зі звуком"
            color = uikit.STATE_ERROR
            self.btn_ffmpeg.configure(text=f"Встановити ffmpeg (~{ffinstall.APPROX_SIZE_MB} МБ)",
                                      width=180)
            self.btn_ffmpeg.grid(row=0, column=3, padx=(8, 0))
        # Без JS-рантайму yt-dlp поки що обходиться іншим клієнтом YouTube і
        # дубляжі отримує (перевірено 02.10.2026 — ті самі 65 форматів), але
        # сам називає цей шлях застарілим. Тож це порада про запас, а не тривога.
        if ffmpeg and not usable and outdated:
            text += ("   —   Node.js застарий (бажано ≥ 22, на випадок змін YouTube): "
                     "winget upgrade OpenJS.NodeJS.LTS")
        elif ffmpeg and not usable:
            text += ("   —   бажано Node.js ≥ 22, на випадок змін YouTube: "
                     "winget install OpenJS.NodeJS.LTS")
        self.lbl_status.configure(text=text, text_color=color)
        applog.info(f"Оточення: {text}; ffmpeg={ffmpeg}; js={runtimes}")

    # ── аналіз ────────────────────────────────────────────────────────────
    def _after_paste(self):
        text = self.ent_url.get()
        if tools.collection_url(text.strip()) or tools.extract_video_urls(text) or \
                tools.clean_url(text).startswith("http"):
            self.analyze()

    def paste_and_analyze(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            text = ""
        if not text:
            self._hint("Буфер обміну порожній", uikit.STATE_WARN)
            return
        self.ent_url.delete(0, "end")
        self.ent_url.insert(0, text)
        self.analyze()

    def open_list_file(self):
        """Таблиця «ID товару — посилання» (xlsx, csv, txt) → картка пакета."""
        path = filedialog.askopenfilename(
            parent=self, title="Файл зі списком відео",
            filetypes=[("Таблиці й списки", "*.xlsx *.xlsm *.xls *.csv *.txt"),
                       ("Excel", "*.xlsx *.xlsm *.xls"), ("CSV", "*.csv"), ("Усі файли", "*.*")])
        if path:
            self.load_list_file(path)

    def load_list_file(self, path):
        name = os.path.basename(path)

        def work(token):
            try:
                result = sheets.read_pairs(path)
                self.ui_events.put(("file_read", token, name, result))
            except Exception as exc:
                applog.error(f"Не вдалося прочитати {path}", exc)
                text = str(exc) if isinstance(exc, ValueError) else \
                    f"Не вдалося прочитати {name}: {exc}"
                if isinstance(exc, PermissionError):
                    text = f"{name} відкритий в іншій програмі — закрийте його в Excel і спробуйте ще раз"
                self.ui_events.put(("analyze_error", token, text))

        self.ent_url.delete(0, "end")
        self._start_background(work, f"Читаю {name}…")

    # ── перетягування й буфер обміну ──────────────────────────────────────
    def _init_drop(self):
        if TkinterDnD is None:
            return
        try:
            TkinterDnD._require(self)
            self.drop_target_register(DND_FILES, DND_TEXT)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as exc:
            applog.warning(f"Перетягування файлів недоступне: {exc}")

    def _on_drop(self, event):
        """Файл зі списком → пакет; посилання, перетягнуте з браузера, → як вставлене."""
        data = event.data or ""
        try:
            items = list(self.tk.splitlist(data))
        except Exception:
            items = [data]
        files = [p for p in items if os.path.isfile(p)]
        if files:
            supported = [p for p in files if os.path.splitext(p)[1].lower() in sheets.SUPPORTED]
            if not supported:
                self._hint("Це не список: перетягніть .xlsx, .xls, .csv або .txt",
                           uikit.STATE_WARN)
            else:
                self.load_list_file(supported[0])
                if len(files) > 1:
                    applog.info(f"Перетягнуто {len(files)} файлів — беру {supported[0]}")
            return event.action
        self._take_text(data.strip())
        return event.action

    def _take_text(self, text):
        """Текст із посиланнями — у поле й на аналіз, як після «Вставити»."""
        if not text or not _has_links(text):
            return False
        self.ent_url.delete(0, "end")
        self.ent_url.insert(0, text)
        self.analyze()
        return True

    def _read_clipboard(self):
        try:
            return self.clipboard_get()
        except Exception:
            return ""       # порожньо, картинка, буфер зайнятий іншою програмою

    def _watch_clipboard(self):
        """Скопійоване деінде посилання на YouTube саме з'являється в полі."""
        text = self._read_clipboard()
        if text == self._clip_last:
            return
        self._clip_last = text
        text = text.strip()
        # Поки йде аналіз чи відкрита картка пакета — не перебиваємо.
        if self._analyzing or self.batch_card.winfo_manager():
            return
        if text and text != self.ent_url.get().strip() and self._take_text(text):
            applog.info("Посилання з буфера обміну")

    def _show_file_batch(self, name, result):
        entries = [(url, url, pid or "") for pid, url in result.pairs]
        self._show_batch(f"з файлу {name}", entries)
        parts = [f"{name}: {len(entries)} відео"]
        if result.with_ids < len(entries):
            parts.append(f"з ID — {result.with_ids}, решта назвуться латиницею")
        if result.skipped:
            parts.append(f"рядків без посилання пропущено: {result.skipped}")
        if result.sheet and result.sheet != name:
            parts.append(f"аркуш «{result.sheet}»")
        self._hint("  ·  ".join(parts), uikit.STATE_OK)

    def _set_analyzing(self, on):
        """Під час аналізу «Аналізувати» стає «Скасувати»."""
        self._analyzing = on
        if on:
            self.btn_analyze.configure(text="Скасувати", fg_color=uikit.DANGER,
                                       hover_color=uikit.DANGER_HOVER, command=self.cancel_analysis)
        else:
            self.btn_analyze.configure(text="Аналізувати", fg_color=GREEN,
                                       hover_color=GREEN_HOVER, command=self.analyze)

    def cancel_analysis(self):
        """Сам запит до YouTube не перервати, але його результат просто
        відкидається: токен уже інший."""
        self._analyze_token += 1
        self._set_analyzing(False)
        self._hint("Аналіз скасовано", uikit.STATE_WARN)

    def analyze(self):
        text = self.ent_url.get().strip()
        if not text:
            self._hint("Спершу вставте посилання", uikit.STATE_WARN)
            return
        collection = tools.collection_url(text)
        urls = tools.extract_video_urls(text)
        # Список «ID_товару посилання» (як його готують для FTP) — файли одразу
        # отримають імена-ID, перейменовувати потім не треба.
        pairs = tools.extract_id_pairs(text)
        with_ids = [p for p in pairs if p[0]]
        if collection:
            self._expand(collection)
        elif with_ids and len(pairs) > 1:
            self.ent_url.delete(0, "end")
            self.ent_url.insert(0, "  ".join(f"{pid or ''} {url}".strip() for pid, url in pairs))
            self._show_batch("", [(url, url, pid or "") for pid, url in pairs])
        elif with_ids:
            self._analyze_one(with_ids[0][1], product_id=with_ids[0][0])
        elif len(urls) > 1:
            self.ent_url.delete(0, "end")
            self.ent_url.insert(0, "  ".join(urls))
            self._show_batch("", [(u, u) for u in urls])
        else:
            self._analyze_one(urls[0] if urls else tools.clean_url(text))

    def _start_background(self, work, hint):
        self._analyze_token += 1
        token = self._analyze_token
        self._set_analyzing(True)
        self._hint(hint, uikit.STATE_INFO)
        threading.Thread(target=lambda: work(token), daemon=True).start()

    def _analyze_one(self, url, product_id=""):
        self._pending_id = product_id or ""
        shown = f"{product_id} {url}" if product_id else url
        if shown != self.ent_url.get().strip():
            self.ent_url.delete(0, "end")
            self.ent_url.insert(0, shown)
        max_height = int(settings.get("max_height") or 0)
        preferred = settings.get("preferred_audio") or "uk"

        def work(token):
            try:
                info = downloader.analyze(url)
                choices = formats.build_choices(info, max_height, preferred)
                thumb = _load_thumbnail(info)
                self.ui_events.put(("analyzed", token, url, info, choices, thumb))
            except Exception as exc:
                applog.error(f"Аналіз {url} не вдався", exc)
                self.ui_events.put(("analyze_error", token, downloader.humanize_error(exc)))

        self._start_background(work, "Отримую список якостей і доріжок… (5–15 секунд)")

    def _expand(self, url):
        def work(token):
            try:
                title, entries = downloader.expand_collection(url)
                self.ui_events.put(("expanded", token, title, entries))
            except Exception as exc:
                applog.error(f"Плейлист {url} не розгорнувся", exc)
                self.ui_events.put(("analyze_error", token, downloader.humanize_error(exc)))

        self._start_background(work, "Отримую список відео плейлиста чи каналу…")

    def _show_info(self, url, info, choices, thumb):
        self._set_analyzing(False)
        if not choices.videos:
            self._hint("У ролику не знайдено жодного відео- чи аудіоформату", uikit.STATE_ERROR)
            return
        self.batch_card.grid_remove()
        self.info, self.choices, self.url = info, choices, url
        self._hint("Готово — перевірте параметри й натисніть «Завантажити»", uikit.STATE_OK)

        self.lbl_title.configure(text=info.get("title") or url)
        meta = [info.get("uploader") or info.get("channel") or ""]
        if info.get("duration"):
            meta.append(uikit.format_duration(info["duration"]))
        if info.get("view_count"):
            meta.append(f"{info['view_count']:,} переглядів".replace(",", " "))
        self.lbl_meta.configure(text="  ·  ".join(m for m in meta if m))

        if thumb is not None:
            self._thumb_image = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=THUMB_SIZE)
            self.lbl_thumb.configure(image=self._thumb_image, text="")
        else:
            self._thumb_image = None
            self.lbl_thumb.configure(image=None, text="без прев'ю")

        self.video_labels = _unique([v.label for v in choices.videos])
        self.opt_video.configure(values=self.video_labels)
        self.opt_video.set(self.video_labels[choices.default_video])

        self.audio_labels = _unique([a.label for a in choices.audios]) or ["—"]
        self.opt_audio.configure(values=self.audio_labels)
        self.opt_audio.set(self.audio_labels[choices.default_audio] if choices.audios else "—")

        self.sub_labels = _unique([s.label for s in choices.subs])
        self.opt_subs.configure(values=self.sub_labels)
        self.opt_subs.set(self.sub_labels[choices.default_sub])

        # Кожна нова картка починається зі значень із налаштувань, а не з
        # того, що обирали для попереднього відео.
        self.keep_original_var.set(bool(settings.get("keep_original")))
        self.subs_mode.set(SUBS_FILE if settings.get("subs_mode") == "file" else SUBS_EMBED)
        # ID із рядка «590312170 https://…» — інакше поле порожнє для кожного нового відео.
        self.id_var.set(getattr(self, "_pending_id", "") or "")
        self._pending_id = ""
        self.container.configure(values=list(VIDEO_CONTAINERS))
        self.container.set(settings.get("container") if settings.get("container")
                           in VIDEO_CONTAINERS else VIDEO_CONTAINERS[0])

        has_uk = any(formats.base_lang(a.lang) == "uk" for a in choices.audios)
        if has_uk:
            self.lbl_audio_hint.configure(text="✓ Є українська доріжка — обрано її",
                                          text_color=uikit.STATE_OK)
        elif choices.default_sub:
            self.lbl_audio_hint.configure(text="Української доріжки немає — увімкнено "
                                               "українські субтитри від автора",
                                          text_color=uikit.STATE_WARN)
        elif any(s.key and formats.base_lang(s.key[0]) == "uk" for s in choices.subs):
            self.lbl_audio_hint.configure(text="Української доріжки немає — автопереклад "
                                               "субтитрів можна обрати вручну",
                                          text_color=uikit.STATE_WARN)
        else:
            self.lbl_audio_hint.configure(text="Української доріжки й субтитрів немає",
                                          text_color=uikit.STATE_WARN)

        self._on_video_change(initial=True)
        self.video_card.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 10))

    def _show_batch(self, title, entries):
        self._set_analyzing(False)
        self.video_card.grid_remove()
        self.info = self.choices = None
        self.batch_card.show(title, entries)
        self.batch_card.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 10))
        self._hint(f"Знайдено {len(entries)} відео — оберіть параметри для всіх одразу",
                   uikit.STATE_OK)

    def close_batch(self):
        self.batch_card.grid_remove()
        self._hint("Пакет скасовано", uikit.STATE_WARN)
        self.ent_url.delete(0, "end")
        self.ent_url.focus_set()

    def _hint(self, text, color=uikit.TEXT_MUTED):
        self.lbl_url_hint.configure(text=text, text_color=color)

    # ── взаємозалежність контролів ─────────────────────────────────────────
    def _selected(self, labels, items, menu):
        try:
            return items[labels.index(menu.get())]
        except (ValueError, IndexError, AttributeError):
            return None

    def _video(self):
        return self._selected(self.video_labels, self.choices.videos, self.opt_video)

    def _audio(self):
        return self._selected(self.audio_labels, self.choices.audios, self.opt_audio)

    def _sub(self):
        return self._selected(self.sub_labels, self.choices.subs, self.opt_subs)

    def _on_video_change(self, initial=False):
        video = self._video()
        audio_only = bool(video) and video.key[0] == formats.AUDIO_ONLY
        values = AUDIO_CONTAINERS if audio_only else VIDEO_CONTAINERS
        current = self.container.get()
        self.container.configure(values=list(values))
        if current not in values:
            saved = settings.get("audio_container" if audio_only else "container")
            self.container.set(saved if saved in values else values[0])
        self._sync_controls()

    def _on_audio_change(self):
        self._sync_controls()

    def _product_id(self):
        """Лише цифри з поля «ID товару» (пробіли, «;» з таблиці відкидаються)."""
        return re.sub(r"\D", "", self.id_var.get())

    def _update_filename_hint(self):
        """Яким буде ім'я файлу — щоб не дізнаватися про це вже в теці."""
        if not self.choices:
            return
        ext = self.container.get() or "mp4"
        product_id = self._product_id()
        if product_id:
            self.lbl_filename.configure(text=f"файл: {product_id}.{ext}")
            return
        name = tools.translit_name(self.info.get("title") or "") or "video"
        audio = self._audio()
        orig = formats.original_lang(self.info)
        if audio and orig is not None and audio.lang != orig:
            name += f"_{formats.base_lang(audio.lang)}"
        self.lbl_filename.configure(text=f"без ID — латиницею: {name}.{ext}")

    def _sync_controls(self):
        if not self.choices:
            return
        video, audio, sub = self._video(), self._audio(), self._sub()
        audio_only = bool(video) and video.key[0] == formats.AUDIO_ONLY
        progressive = bool(video) and video.has_audio

        has_other_original = bool(audio) and not audio.is_original and \
            any(a.is_original for a in self.choices.audios)
        self.chk_original.configure(
            state="normal" if has_other_original and not audio_only and not progressive
            else "disabled")
        self.opt_audio.configure(state="disabled" if progressive or not self.choices.audios
                                 else "normal")
        self.opt_subs.configure(state="disabled" if audio_only else "normal")
        self.subs_mode.configure(state="normal" if sub and sub.key and not audio_only
                                 else "disabled")

        hints = {
            "mp4": "відкривається скрізь, зокрема на телефоні й телевізорі",
            "mkv": "краща якість звуку (Opus), але не всі телевізори читають",
            "m4a": "без перекодування, як є на YouTube",
            "mp3": "перекодування в 192 кбіт/с — для старих плеєрів",
        }
        self.lbl_container_hint.configure(text=hints.get(self.container.get(), ""))
        self._update_filename_hint()

    # ── завантаження ──────────────────────────────────────────────────────
    def _ensure_ffmpeg(self, needed):
        """Пропонує поставити ffmpeg, якщо він знадобиться. False — користувач відмовився."""
        if not needed or tools.find_ffmpeg() or ffinstall.in_progress():
            return True
        if not messagebox.askyesno(
                APP_TITLE,
                "Щоб склеїти відео зі звуком, потрібен ffmpeg, а на цьому комп'ютері "
                f"його немає.\n\nЗавантажити його зараз (~{ffinstall.APPROX_SIZE_MB} МБ, "
                "один раз)? Відео почне качатися одразу після цього.", parent=self):
            return False
        self._install_ffmpeg()
        return True

    def download(self):
        if not self.info or not self.choices:
            self._hint("Спершу проаналізуйте посилання", uikit.STATE_WARN)
            return
        video, audio, sub = self._video(), self._audio(), self._sub()
        if video is None:
            return
        audio_only = video.key[0] == formats.AUDIO_ONLY
        keep_original = bool(self.keep_original_var.get()) and \
            self.chk_original.cget("state") == "normal"
        job = downloader.Job(
            url=self.url, title=self.info.get("title") or self.url, out_dir=self.dir_var.get(),
            info=self.info, video_key=video.key, audio_lang=audio.lang if audio else "",
            audio_label=audio.label if audio else "",
            container=self.container.get(), keep_original=keep_original,
            sub_key=sub.key if sub and not audio_only else (),
            subs_mode="file" if self.subs_mode.get() == SUBS_FILE else "embed",
            product_id=self._product_id())
        if not self._ensure_ffmpeg(downloader.needs_ffmpeg(job, progressive=video.has_audio)):
            return
        # Вибір у картці стосується лише цього завантаження — значення за
        # замовчуванням живуть у вікні налаштувань.
        if not self._enqueue([job]):
            return
        self._hint(f"«{job.title}» додано в чергу. Можна вставляти наступне посилання.",
                   uikit.STATE_OK)
        # Картку ховаємо: відео вже в черзі, а місце потрібне списку
        # завантажень. Наступне посилання покаже її знову.
        self.video_card.grid_remove()
        self.info = self.choices = None
        self.ent_url.delete(0, "end")
        self.ent_url.focus_set()

    def download_batch(self):
        jobs = self.batch_card.build_jobs(self.dir_var.get())
        if not jobs:
            return
        if not self._ensure_ffmpeg(any(downloader.needs_ffmpeg(j) for j in jobs)):
            return
        added = self._enqueue(jobs)
        skipped = len(jobs) - added
        text = f"Додано в чергу: {added} відео"
        if skipped:
            text += f" (ще {skipped} вже були в черзі)"
        self._hint(text, uikit.STATE_OK)
        self.batch_card.grid_remove()
        self.ent_url.delete(0, "end")
        self.ent_url.focus_set()

    def _enqueue(self, jobs):
        """Ставить у чергу, пропускаючи ролики, що вже чекають чи качаються. Повертає, скільки додано."""
        # Ключ — ролик І товар: той самий ролик для двох товарів — це два файли.
        busy = {(j.url, j.product_id) for j in self.jobs.values() if j.state in ACTIVE}
        added = 0
        for job in jobs:
            key = (job.url, job.product_id)
            if key in busy:
                continue
            busy.add(key)
            self.jobs[job.id] = job
            self._session.add(job.id)
            self.manager.submit(job)
            added += 1
        if added:
            self._materialize()
        if not added and jobs:
            self._hint("Це відео вже в черзі", uikit.STATE_WARN)
        self._queue_dirty = True
        return added

    def cancel_job(self, job):
        self.manager.cancel(job)
        self._queue_dirty = True

    def cancel_all(self):
        active = self._active_jobs()
        if len(active) >= 2 and not messagebox.askyesno(
                APP_TITLE, f"Скасувати всі завантаження ({len(active)})?", parent=self):
            return
        # Спершу ті, що чекають, — інакше черга встигла б узяти наступне,
        # поки зупиняється поточне.
        for job in sorted(active, key=lambda j: j.state == "running"):
            self.manager.cancel(job)
        self._queue_dirty = True

    def retry(self, job):
        self.retry_many([job])

    def retry_many(self, jobs):
        clones = []
        for job in jobs:
            clones.append(job.clone())
            self.jobs.pop(job.id, None)
            old = self.rows.pop(job.id, None)
            if old:
                old.destroy()
        self._enqueue(clones)

    def retry_failed(self):
        failed = sorted((j for j in self.jobs.values() if j.state in ("error", "cancelled")),
                        key=lambda j: j.id)
        if failed:
            self.retry_many(failed)
            self._hint(f"Знову в черзі: {len(failed)} відео", uikit.STATE_OK)

    def toggle_pause(self):
        if self.manager.paused:
            self.manager.resume()
            applog.info("Черга: продовжено")
        else:
            self.manager.pause()
            applog.info("Черга: пауза")
        self._sync_pause_button()

    def _sync_pause_button(self):
        self.btn_pause.configure(text="▶ Продовжити" if self.manager.paused else "⏸ Пауза")

    def save_report(self):
        """Звіт xlsx по всьому, що зараз у списку, — у теку з відео."""
        jobs = sorted(self.jobs.values(), key=lambda j: j.id)
        if not jobs:
            self._hint("Список завантажень порожній — звітувати нема про що", uikit.STATE_WARN)
            return
        dirs = {j.out_dir for j in jobs}
        out_dir = dirs.pop() if len(dirs) == 1 else self.dir_var.get()
        items = [{"product_id": j.product_id, "url": j.url, "title": j.title,
                  "filepath": j.filepath, "state": j.state, "text": j.status,
                  "duration": job_duration(j)} for j in jobs]
        path = os.path.join(out_dir, report.default_name())
        try:
            report.write_report(path, items)
        except PermissionError:
            self._hint(f"{os.path.basename(path)} відкритий в Excel — закрийте й спробуйте ще раз",
                       uikit.STATE_ERROR)
            return
        except Exception as exc:
            applog.error(f"Звіт {path} не записався", exc)
            self._hint(f"Звіт не записався: {exc}"[:220], uikit.STATE_ERROR)
            return
        applog.info(f"Звіт: {path} ({len(items)} рядків)")
        self._hint(f"Звіт збережено: {path}", uikit.STATE_OK)
        if not uikit.select_in_explorer(path):
            uikit.open_path(out_dir)

    def _materialize(self, force=()):
        """Створює рядки для завдань, що от-от почнуться (до QUEUED_ROWS у
        черзі), і для force — тих, що вже почались. Завдання без рядка, яке
        скасували в черзі, рядка не отримує: воно лише в лічильнику й у звіті."""
        queued = sum(1 for r in self.rows.values() if r.job.state == "queued")
        new = {i for i in force if i in self.jobs and i not in self.rows}
        for job in sorted(self.jobs.values(), key=lambda j: j.id):
            if queued >= QUEUED_ROWS:
                break
            if job.id not in self.rows and job.id not in new and job.state == "queued":
                new.add(job.id)
                queued += 1
        for job_id in new:
            self.rows[job_id] = JobRow(self.jobs_list, self, self.jobs[job_id])
        self._regrid_rows()

    def _regrid_rows(self):
        # Нові зверху: щойно додане завантаження має бути видно одразу.
        for i, row in enumerate(sorted(self.rows.values(), key=lambda r: -r.job.id)):
            row.grid(row=i + 1, column=0, sticky="ew", padx=4, pady=4)
        hidden = [j for j in self.jobs.values() if j.id not in self.rows]
        waiting = sum(1 for j in hidden if j.state in ACTIVE)
        parts = []
        if waiting:
            parts.append(f"… і ще {waiting} у черзі — рядки з'являться, коли дійде черга")
        if len(hidden) > waiting:
            parts.append(f"скасовано без рядка: {len(hidden) - waiting} (є у звіті)")
        if parts:
            self.lbl_more.configure(text="  ·  ".join(parts))
            self.lbl_more.grid(row=0, column=0, pady=(6, 2))
        else:
            self.lbl_more.grid_remove()
        if self.jobs:
            self.lbl_empty.grid_remove()
        else:
            self.lbl_empty.grid()

    def _clear_finished(self):
        for job_id, job in list(self.jobs.items()):
            if job.state in FINISHED:
                del self.jobs[job_id]
                row = self.rows.pop(job_id, None)
                if row:
                    row.destroy()
        self._materialize()

    def _choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get(), parent=self,
                                       title="Куди зберігати відео")
        if path:
            path = os.path.normpath(path)
            self.dir_var.set(path)
            settings.set_many(download_dir=path)

    # ── черга між запусками ───────────────────────────────────────────────
    def _restore_queue(self):
        if not settings.get("resume_queue"):
            return
        jobs = queuestore.load()
        if not jobs:
            return
        added = self._enqueue(jobs)
        if added:
            applog.info(f"Відновлено незавершених завантажень: {added}")
            self._hint(f"Продовжую незавершені завантаження: {added}", uikit.STATE_INFO)

    def _save_queue(self):
        self._queue_dirty = False
        if getattr(self, "_closing", False):
            return      # під час закриття черга вже збережена — скасування її не стирає
        if not settings.get("resume_queue"):
            return
        jobs = sorted((j for j in self.jobs.values() if j.state in ACTIVE), key=lambda j: j.id)
        if jobs:
            queuestore.save(jobs)
        else:
            queuestore.clear()

    # ── панель задач і сповіщення ─────────────────────────────────────────
    def _init_taskbar(self):
        try:
            self.taskbar = taskbar.Taskbar(int(self.wm_frame(), 16))
        except Exception as exc:
            applog.warning(f"Панель задач недоступна: {exc}")
            self.taskbar = None

    def _update_taskbar(self, active):
        if not self.taskbar or not settings.get("taskbar_progress"):
            return
        if time.monotonic() < self._error_until:
            self.taskbar.set_state(taskbar.ERROR)
            return
        session = [self.jobs[i] for i in self._session if i in self.jobs]
        done = sum(1 for j in session if j.state in FINISHED)
        total = max(1, len(session))
        running = [self.rows[j.id] for j in session if j.state == "running" and j.id in self.rows]
        if self.manager.paused and active:
            self.taskbar.set_progress(done / total, taskbar.PAUSED)
        elif running and running[0].bar.cget("mode") == "determinate":
            self.taskbar.set_progress((done + running[0].fraction) / total)
        elif active or self._analyzing or ffinstall.in_progress():
            self.taskbar.set_state(taskbar.INDETERMINATE)
        else:
            self.taskbar.clear()

    def _on_queue_idle(self):
        """Черга щойно спорожніла: сповіщення (якщо вікно не перед очима)."""
        session = [self.jobs[i] for i in self._session if i in self.jobs]
        self._session.clear()
        done = sum(1 for j in session if j.state == "done")
        failed = sum(1 for j in session if j.state == "error")
        if not (done or failed) or not settings.get("notify_done"):
            return
        if self.focus_displayof() is not None and self.state() != "iconic":
            return
        text = f"Завантажено {done} відео" if done else "Нічого не завантажилось"
        if failed:
            text += f" · не вдалося {failed}"
        notify.show(APP_TITLE, text)

    # ── налаштування ──────────────────────────────────────────────────────
    def open_settings(self):
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.focus_set()
            return
        from .settings_dialog import SettingsDialog
        self._settings_window = SettingsDialog(self, self._on_setting_changed)

    def _on_setting_changed(self, key, value):
        if key == "theme":
            ctk.set_appearance_mode(THEMES.get(value, "Dark"))
        elif key == "download_dir":
            self.dir_var.set(settings.get("download_dir"))
        elif key == "taskbar_progress" and not value and self.taskbar:
            self.taskbar.clear()
        elif key == "resume_queue":
            if value:
                self._save_queue()
            else:
                queuestore.clear()
        elif key == "watch_clipboard":
            self._clip_last = self._read_clipboard()    # уже скопійоване не підхоплюємо
        elif key == "ytdlp_ready":
            self._show_ytdlp_ready(value)
        elif key == "app_ready":
            self._show_update_ready()

    def _show_ytdlp_ready(self, version):
        self._ytdlp_ready = version
        self._show_update_ready()

    def _show_update_ready(self):
        """Унизу — що чекає на перезапуск: нова версія програми та/або yt-dlp."""
        app_version = appupdate.state["version"]
        if app_version:
            text = f"Є R-TubeUA {app_version} — завантажено й перевірено"
            button = "Оновити й перезапустити"
        else:
            text = f"yt-dlp {self._ytdlp_ready} завантажено — застосується після перезапуску"
            button = "Перезапустити"
        self.lbl_update.configure(text=text, text_color=uikit.STATE_OK)
        self.btn_restart.configure(text=button, width=180 if app_version else 110)
        self.btn_restart.grid(row=0, column=2, padx=(8, 0))

    # ── події з фонових потоків ───────────────────────────────────────────
    def _poll(self):
        # Перепланування — у finally: якщо котрась подія впаде, решта вікна
        # однаково має жити, а не застигнути без оновлень прогресу.
        self.watchdog.beat()
        try:
            self._drain_events()
        finally:
            self.after(100, self._poll)

    def _drain_events(self):
        self._track_ffmpeg_install()
        self._polls += 1
        if self._polls % CLIPBOARD_EVERY == 0 and settings.get("watch_clipboard"):
            self._watch_clipboard()
        try:
            while True:
                event = self.ui_events.get_nowait()
                kind = event[0]
                if kind == "analyzed" and event[1] == self._analyze_token:
                    self._show_info(*event[2:])
                elif kind == "expanded" and event[1] == self._analyze_token:
                    self._show_batch(*event[2:])
                elif kind == "file_read" and event[1] == self._analyze_token:
                    self._show_file_batch(*event[2:])
                elif kind == "analyze_error" and event[1] == self._analyze_token:
                    self._set_analyzing(False)
                    self._hint(event[2], uikit.STATE_ERROR)
                elif kind == "env":
                    self._show_environment(*event[1:])
                elif kind == "ytdlp_ready":
                    self._show_ytdlp_ready(event[1])
                elif kind == "app_ready":
                    self._show_update_ready()
        except queue.Empty:
            pass
        started, finished = [], False
        try:
            for _ in range(200):
                kind, job_id, payload = self.manager.events.get_nowait()
                job = self.jobs.get(job_id)
                if job is None:
                    continue
                if kind == "state":
                    job.status = payload[1]
                    self._queue_dirty = True
                    finished = True
                    if payload[0] == "error":
                        self._error_until = time.monotonic() + ERROR_FLASH
                    if payload[0] == "running" and job_id not in self.rows:
                        started.append(job_id)      # рядок створиться вже з цим станом
                row = self.rows.get(job_id)
                if row is None:
                    continue
                if kind == "progress":
                    row.set_progress(*payload)
                elif kind == "meta":
                    row.set_meta(*payload)
                elif kind == "state":
                    row.set_state(*payload)
                    row.refresh_duration()
        except queue.Empty:
            pass
        if started or finished:
            # Черга посунулась — підтягуємо наступні рядки (і лічильник «ще N»).
            self._materialize(force=started)

        active = self._active_jobs()
        paused = self.manager.paused
        failed = sum(1 for j in self.jobs.values() if j.state in ("error", "cancelled"))
        _show(self.btn_pause, bool(active) or paused, column=0)
        _show(self.btn_cancel_all, bool(active), column=1)
        _show(self.btn_retry_failed, bool(failed), column=2)
        _show(self.btn_report, any(j.state in FINISHED for j in self.jobs.values()), column=3)
        if failed:
            self.btn_retry_failed.configure(text=f"↻ Невдалі ({failed})")
        if active:
            state = "пауза" if paused else "у роботі"
            self.lbl_jobs.configure(text=f"Завантаження · {state} {len(active)}")
        else:
            self.lbl_jobs.configure(text="Завантаження · пауза" if paused else "Завантаження")
        self._update_total()
        if self._was_active and not active:
            self._on_queue_idle()
        self._was_active = bool(active)
        if self._queue_dirty:
            self._save_queue()
        self._update_taskbar(active)

    def _update_total(self):
        """«Завершено: 4 · ⏱ 12,34» — лише ті, що зараз у списку: «Прибрати
        завершені» обнуляє лічильник разом зі списком."""
        done = [j for j in self.jobs.values() if j.state == "done"]
        if not done:
            self.lbl_total.set_value("", "")
            return
        seconds = sum(job_duration(j) or 0 for j in done)
        value = uikit.format_min_sec(seconds)
        self.lbl_total.set_value(value, f"Завершено: {len(done)}  ·  ⏱ {value}")

    # ── решта ─────────────────────────────────────────────────────────────
    def report_callback_exception(self, exc_type, exc, tb):
        """Виняток в обробнику кнопки чи події. У .exe без консолі Tk
        інакше мовчки друкує його в нікуди."""
        applog.get_logger().error("Помилка в інтерфейсі", exc_info=(exc_type, exc, tb))
        try:
            messagebox.showerror(APP_TITLE, f"Щось пішло не так: {exc}\n\n"
                                            "Подробиці — кнопка «Лог» угорі.", parent=self)
        except Exception:
            pass

    def _active_jobs(self):
        return [j for j in self.jobs.values() if j.state in ACTIVE]

    def _restart(self):
        if self._active_jobs() and not settings.get("resume_queue"):
            self.lbl_update.configure(text="Дочекайтесь завершення завантажень, тоді перезапустіть",
                                      text_color=uikit.STATE_WARN)
            return
        new_exe = appupdate.state["path"]
        if new_exe:
            try:
                appupdate.apply(new_exe)
            except Exception as exc:
                applog.error(f"Не вдалося замінити програму на {new_exe}", exc)
                messagebox.showerror(
                    APP_TITLE, "Не вдалося замінити програму новою версією "
                               f"({exc}).\n\nНовий файл лежить тут — його можна поставити "
                               f"замість старого вручну:\n{new_exe}", parent=self)
                uikit.select_in_explorer(new_exe)
                return
            appupdate.state.update(version=None, path=None)
        # Нова копія стартує вже ПІСЛЯ зупинки завантажень (_close_now): інакше
        # вона взялася б докачувати ролик, який ця ще не відпустила.
        self._relaunch = True
        self.on_closing(force=True)

    def on_closing(self, force=False):
        if getattr(self, "_closing", False):
            return      # уже зупиняємось — повторне ✕ не перепитує
        active = self._active_jobs()
        resume = bool(settings.get("resume_queue"))
        if active and not resume and not force and not messagebox.askyesno(
                APP_TITLE, f"Ще не завершено завантажень: {len(active)}.\n"
                           "Закрити програму й перервати їх?", parent=self):
            return
        if resume:
            # Зберігаємо ДО скасування: далі _closing вимикає запис, і черга
            # у файлі лишається такою, якою була до закриття.
            self._save_queue()
        self._closing = True
        ffinstall.cancel()
        for job in sorted(active, key=lambda j: j.state == "running"):
            # З resume — лишаємо .part, щоб докачати після запуску.
            self.manager.cancel(job, keep_partial=resume)
        if self.manager.is_busy():
            # Скасування спрацює на наступному кроці yt-dlp, після чого
            # потік прибере .part і проміжні файли. Закрийся вікно одразу —
            # потік загинув би разом із процесом і сміття лишилося б у теці.
            self.lbl_status.configure(text="Зупиняю завантаження…", text_color=uikit.STATE_WARN)
            self._wait_and_close(deadline=time.monotonic() + CLOSE_TIMEOUT)
            return
        self._close_now()

    def _wait_and_close(self, deadline):
        if self.manager.is_busy() and time.monotonic() < deadline:
            self.after(200, lambda: self._wait_and_close(deadline))
            return
        if self.manager.is_busy():
            applog.warning("Завантаження не зупинилось за відведений час — закриваю як є")
        self._close_now()

    def _close_now(self):
        if self.taskbar:
            self.taskbar.clear()
        if self.state() == "normal":
            settings.set_many(geometry=self._logical_size())
        self.watchdog.stop()
        if self._relaunch:
            try:
                ytupdate.relaunch()
            except Exception as exc:
                applog.error("Перезапуск не вдався", exc)
                messagebox.showerror(APP_TITLE, "Не вдалося запустити програму знову — "
                                                "відкрийте її вручну.", parent=self)
        self.destroy()

    def _logical_size(self):
        """Розмір без DPI-множника: geometry() його знову помножить."""
        try:
            scale = self._get_window_scaling()
        except Exception:
            scale = 1.0
        w = round(self.winfo_width() / scale)
        h = round(self.winfo_height() / scale)
        return f"{max(w, MIN_SIZE[0])}x{max(h, MIN_SIZE[1])}"


def _has_links(text):
    """Чи є в тексті посилання на YouTube. Саме «youtu»: голий ID ролика
    (11 символів) підходить під будь-яке скопійоване слово такої довжини."""
    return "youtu" in text.lower() and \
        bool(tools.collection_url(text) or tools.extract_video_urls(text))


def _show(widget, visible, **grid):
    """grid / grid_remove лише при зміні: викликається з опитування 10 разів на секунду."""
    if visible and not widget.winfo_manager():
        widget.grid(row=0, padx=(0, 8), **grid)
    elif not visible and widget.winfo_manager():
        widget.grid_remove()


def _load_thumbnail(info):
    """Прев'ю ролика як PIL.Image, обрізане до 16:9. None — якщо не вдалось."""
    from PIL import Image, ImageOps
    urls = []
    video_id = info.get("id")
    if video_id and "youtube" in (info.get("extractor") or "youtube").lower():
        # mqdefault — 320x180 JPEG, рівно 16:9 і без чорних смуг.
        urls.append(f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg")
    if info.get("thumbnail"):
        urls.append(info["thumbnail"])
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            return ImageOps.fit(img, (THUMB_SIZE[0] * 2, THUMB_SIZE[1] * 2))
        except Exception:
            continue
    return None
