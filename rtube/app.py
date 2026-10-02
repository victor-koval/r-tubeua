"""Головне вікно R-TubeUA: посилання → вибір якості, доріжки, субтитрів → черга."""

import io
import os
import queue
import threading
import time
import urllib.request
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import applog, downloader, ffinstall, formats, settings, tools, uikit, ytupdate
from .uikit import FONT_SMALL, FONT_UI, FONT_UI_BOLD, GREEN, GREEN_HOVER

APP_TITLE = "R-TubeUA"
APP_VERSION = "1.0.1"

DEFAULT_SIZE = (1000, 800)
MIN_SIZE = (880, 660)
THUMB_SIZE = (224, 126)
CLOSE_TIMEOUT = 5          # скільки чекати зупинки завантаження при закритті, с

THEME_LIGHT, THEME_DARK, THEME_SYSTEM = "Світла", "Темна", "Системна"
THEMES = {THEME_LIGHT: "Light", THEME_DARK: "Dark", THEME_SYSTEM: "System"}

SUBS_EMBED, SUBS_FILE = "Вшити у відео", "Окремий файл .srt"
VIDEO_CONTAINERS = ("mp4", "mkv")
AUDIO_CONTAINERS = ("m4a", "mp3")


def _unique(labels):
    """Підписи в CTkOptionMenu мусять бути різними — інакше вибір не
    зіставити назад із варіантом (напр. «iw» і «he» — обидва «Іврит»)."""
    seen, out = {}, []
    for label in labels:
        n = seen.get(label, 0)
        seen[label] = n + 1
        out.append(label if n == 0 else f"{label} ({n + 1})")
    return out


class JobRow(ctk.CTkFrame):
    """Рядок у списку завантажень: назва, параметри, прогрес, дії."""

    def __init__(self, master, app, job):
        super().__init__(master, fg_color=uikit.SURFACE_RAISED, corner_radius=uikit.RADIUS)
        self.app = app
        self.job = job
        self.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self, text=job.title, font=FONT_UI_BOLD, anchor="w",
                                      justify="left")
        self.lbl_title.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=(8, 0))
        self.lbl_summary = ctk.CTkLabel(self, text=job.summary(), font=FONT_SMALL, anchor="w",
                                        text_color=uikit.TEXT_MUTED)
        self.lbl_summary.grid(row=1, column=0, sticky="ew", padx=(12, 8))
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
                                        command=lambda: app.manager.cancel(job))
        self.btn_cancel.pack(side="left")
        self.btn_open = uikit.SecondaryButton(self.actions, text="▶ Відкрити", width=96,
                                              height=30, command=self._open)
        self.btn_folder = uikit.SecondaryButton(self.actions, text="📁 У теці", width=84,
                                                height=30, command=self._folder)
        self.btn_retry = uikit.SecondaryButton(self.actions, text="↻ Повторити", width=104,
                                               height=30, command=lambda: app.retry(job))
        self.lbl_title.bind("<Configure>", lambda e: self.lbl_title.configure(
            wraplength=max(200, e.width - 4)))

    def set_progress(self, fraction, text):
        if fraction is None:
            if self.bar.cget("mode") != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start()
        else:
            if self.bar.cget("mode") != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.bar.set(max(0.0, min(1.0, fraction)))
        self.lbl_status.configure(text=text, text_color=uikit.TEXT_MUTED)

    def set_state(self, state, text):
        colors = {"done": uikit.STATE_OK, "error": uikit.STATE_ERROR,
                  "cancelled": uikit.STATE_WARN, "running": uikit.STATE_INFO}
        self.lbl_status.configure(text=text, text_color=colors.get(state, uikit.TEXT_MUTED))
        if state in ("done", "error", "cancelled"):
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.bar.set(1 if state == "done" else 0)
            self.btn_cancel.pack_forget()
        if state == "done":
            if "без субтитрів" in text:
                self.lbl_status.configure(text_color=uikit.STATE_WARN)
            if self.job.filepath:
                self.lbl_status.configure(text=f"{text}  ·  {os.path.basename(self.job.filepath)}")
            self.btn_open.pack(side="left", padx=(0, 6))
            self.btn_folder.pack(side="left")
        elif state in ("error", "cancelled"):
            self.btn_retry.pack(side="left")
        elif state == "running":
            self.bar.configure(mode="determinate")
            self.bar.set(0)

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


class RTubeApp(ctk.CTk):
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

        self.manager = downloader.DownloadManager()
        self.ui_events = queue.Queue()     # результати аналізу з фонових потоків
        self.rows = {}                      # job.id → JobRow
        self.info = None
        self.choices = None
        self._analyze_token = 0
        self._thumb_image = None

        self.grid_columnconfigure(0, weight=1)
        # Черзі — мінімум місця на два рядки, хай навіть вікно низьке.
        self.grid_rowconfigure(3, weight=1, minsize=170)
        self._build_header()
        self._build_url_card()
        self._build_video_card()
        self._build_jobs_card()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.after(100, self._poll)
        self.after(50, lambda: self.ent_url.focus_set())
        threading.Thread(target=self._check_environment, daemon=True).start()
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

        self.theme_var = ctk.StringVar(value=settings.get("theme"))
        ctk.CTkOptionMenu(header, values=list(THEMES), variable=self.theme_var, width=110,
                          fg_color=uikit.HEADER_HOVER, button_color=uikit.HEADER_HOVER,
                          button_hover_color="#403b3b", text_color=uikit.HEADER_TEXT,
                          command=self._set_theme).grid(row=0, column=3, padx=(0, 8))
        ctk.CTkButton(header, text="Лог", width=56, fg_color=uikit.HEADER_HOVER,
                      hover_color="#403b3b", text_color=uikit.HEADER_TEXT,
                      command=applog.open_log_folder).grid(row=0, column=4, padx=(0, 22))

    def _build_url_card(self):
        card = uikit.Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=22, pady=(18, 10))
        card.grid_columnconfigure(0, weight=1)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        row.grid_columnconfigure(0, weight=1)

        self.ent_url = ctk.CTkEntry(row, height=38, font=FONT_UI,
                                    placeholder_text="Вставте посилання на відео YouTube "
                                                     "(можна з &list=, &t= — зайве приберемо)")
        self.ent_url.grid(row=0, column=0, sticky="ew")
        self.ent_url.bind("<Return>", lambda e: self.analyze())
        uikit.bind_text_hotkeys(self.ent_url, on_paste=self._after_paste)

        uikit.SecondaryButton(row, text="Вставити", width=96, height=38,
                              command=self.paste_and_analyze).grid(row=0, column=1, padx=(8, 0))
        self.btn_analyze = ctk.CTkButton(row, text="Аналізувати", width=130, height=38,
                                         font=FONT_UI_BOLD, command=self.analyze)
        self.btn_analyze.grid(row=0, column=2, padx=(8, 0))

        self.lbl_url_hint = ctk.CTkLabel(card, text="Після вставки посилання аналіз "
                                                    "запускається сам. Enter — теж.",
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
        self.lbl_title.bind("<Configure>", lambda e: self.lbl_title.configure(
            wraplength=max(200, e.width - 4)))
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
        self.subs_mode.set(SUBS_FILE if settings.get("subs_mode") == "file" else SUBS_EMBED)
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

        label("Зберегти в", 4)
        folder = ctk.CTkFrame(opts, fg_color="transparent")
        folder.grid(row=4, column=1, columnspan=2, sticky="ew", pady=5)
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
        ctk.CTkLabel(head, text="Завантаження", font=uikit.FONT_TITLE, anchor="w").grid(
            row=0, column=0, sticky="w")
        uikit.SecondaryButton(head, text="Прибрати завершені", width=150, height=28,
                              command=self._clear_finished).grid(row=0, column=1)
        self.jobs_list = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self.jobs_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self.jobs_list.grid_columnconfigure(0, weight=1)
        self.lbl_empty = ctk.CTkLabel(self.jobs_list, text="Поки що нічого. Можна додати "
                                      "кілька відео підряд — вони качатимуться по черзі.",
                                      font=FONT_SMALL, text_color=uikit.TEXT_MUTED)
        self.lbl_empty.grid(row=0, column=0, pady=18)

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
        self._ffmpeg_was_running = False

    # ── оточення ──────────────────────────────────────────────────────────
    def _check_environment(self):
        self._send_environment()
        # Свіжий yt-dlp — після перевірки оточення, щоб не гальмувати старт.
        try:
            installed = ytupdate.check_and_install()
            if installed:
                self.ui_events.put(("ytdlp_ready", installed))
        except Exception as exc:
            applog.error("Оновлення yt-dlp не вдалося — працюю на поточному", exc)

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
        self._ffmpeg_was_running = True

    def _track_ffmpeg_install(self):
        """Прогрес встановлення ffmpeg — у рядку статусу; по завершенні —
        повторна перевірка оточення, щоб «ffmpeg ✗» змінився на «✓»."""
        st = ffinstall.status()
        if st["running"]:
            self._ffmpeg_was_running = True
            pct = f" ({st['fraction'] * 100:.0f}%)" if st["fraction"] else ""
            self.lbl_status.configure(text=st["text"] + pct, text_color=uikit.STATE_INFO)
        elif self._ffmpeg_was_running:
            self._ffmpeg_was_running = False
            if st["error"]:
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
        url = tools.clean_url(self.ent_url.get())
        if url.startswith("http"):
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

    def analyze(self):
        url = tools.clean_url(self.ent_url.get())
        if not url:
            self._hint("Спершу вставте посилання", uikit.STATE_WARN)
            return
        if url != self.ent_url.get().strip():
            self.ent_url.delete(0, "end")
            self.ent_url.insert(0, url)
        self._analyze_token += 1
        token = self._analyze_token
        self.btn_analyze.configure(state="disabled", text="Аналіз…")
        self._hint("Отримую список якостей і доріжок… (5–15 секунд)", uikit.STATE_INFO)
        max_height = int(settings.get("max_height") or 1080)
        preferred = settings.get("preferred_audio") or "uk"

        def work():
            try:
                info = downloader.analyze(url)
                choices = formats.build_choices(info, max_height, preferred)
                thumb = _load_thumbnail(info)
                self.ui_events.put(("analyzed", token, url, info, choices, thumb))
            except Exception as exc:
                applog.error(f"Аналіз {url} не вдався", exc)
                self.ui_events.put(("analyze_error", token, downloader.humanize_error(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _show_info(self, url, info, choices, thumb):
        self.btn_analyze.configure(state="normal", text="Аналізувати")
        if not choices.videos:
            self._hint("У ролику не знайдено жодного відео- чи аудіоформату", uikit.STATE_ERROR)
            return
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

        has_uk = any(formats.base_lang(a.lang) == "uk" for a in choices.audios)
        if has_uk:
            self.lbl_audio_hint.configure(text="✓ Є українська доріжка — обрано її",
                                          text_color=uikit.STATE_OK)
        elif choices.default_sub:
            self.lbl_audio_hint.configure(text="Української доріжки немає — увімкнено "
                                               "українські субтитри",
                                          text_color=uikit.STATE_WARN)
        else:
            self.lbl_audio_hint.configure(text="Української доріжки й субтитрів немає",
                                          text_color=uikit.STATE_WARN)

        self._on_video_change(initial=True)
        self.video_card.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 10))

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

    # ── завантаження ──────────────────────────────────────────────────────
    def download(self):
        if not self.info or not self.choices:
            self._hint("Спершу проаналізуйте посилання", uikit.STATE_WARN)
            return
        video, audio, sub = self._video(), self._audio(), self._sub()
        if video is None:
            return
        out_dir = self.dir_var.get()
        audio_only = video.key[0] == formats.AUDIO_ONLY
        keep_original = bool(self.keep_original_var.get()) and \
            self.chk_original.cget("state") == "normal"
        job = downloader.Job(
            url=self.url, title=self.info.get("title") or self.url, info=self.info,
            video_key=video.key, audio_lang=audio.lang if audio else "",
            audio_label=audio.label if audio else "", out_dir=out_dir,
            container=self.container.get(), keep_original=keep_original,
            sub_key=sub.key if sub and not audio_only else (),
            subs_mode="file" if self.subs_mode.get() == SUBS_FILE else "embed")

        if downloader.needs_ffmpeg(job, progressive=video.has_audio) and \
                not tools.find_ffmpeg() and not ffinstall.in_progress():
            if not messagebox.askyesno(
                    APP_TITLE,
                    "Щоб склеїти відео зі звуком, потрібен ffmpeg, а на цьому комп'ютері "
                    f"його немає.\n\nЗавантажити його зараз (~{ffinstall.APPROX_SIZE_MB} МБ, "
                    "один раз)? Відео почне качатися одразу після цього.", parent=self):
                return
            self._install_ffmpeg()

        remember = {"download_dir": out_dir, "keep_original": bool(self.keep_original_var.get()),
                    "subs_mode": job.subs_mode}
        if audio_only:
            remember["audio_container"] = job.container
        else:
            remember["container"] = job.container
            remember["max_height"] = video.height
        settings.set_many(**remember)

        self._add_row(job)
        self.manager.submit(job)
        self._hint(f"«{job.title}» додано в чергу. Можна вставляти наступне посилання.",
                   uikit.STATE_OK)
        # Картку ховаємо: відео вже в черзі, а місце потрібне списку
        # завантажень. Наступне посилання покаже її знову.
        self.video_card.grid_remove()
        self.info = self.choices = None
        self.ent_url.delete(0, "end")
        self.ent_url.focus_set()

    def retry(self, job):
        clone = downloader.Job(
            url=job.url, title=job.title, info=job.info, video_key=job.video_key,
            audio_lang=job.audio_lang, audio_label=job.audio_label, out_dir=job.out_dir,
            container=job.container, keep_original=job.keep_original, sub_key=job.sub_key,
            subs_mode=job.subs_mode)
        old = self.rows.pop(job.id, None)
        if old:
            old.destroy()
        self._add_row(clone)
        self.manager.submit(clone)

    def _add_row(self, job):
        self.lbl_empty.grid_remove()
        row = JobRow(self.jobs_list, self, job)
        self.rows[job.id] = row
        self._regrid_rows()

    def _regrid_rows(self):
        # Нові зверху: щойно додане завантаження має бути видно одразу.
        for i, row in enumerate(sorted(self.rows.values(), key=lambda r: -r.job.id)):
            row.grid(row=i + 1, column=0, sticky="ew", padx=4, pady=4)
        if not self.rows:
            self.lbl_empty.grid()

    def _clear_finished(self):
        for job_id, row in list(self.rows.items()):
            if row.job.state in ("done", "error", "cancelled"):
                row.destroy()
                del self.rows[job_id]
        self._regrid_rows()

    def _choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get(), parent=self,
                                       title="Куди зберігати відео")
        if path:
            path = os.path.normpath(path)
            self.dir_var.set(path)
            settings.set_many(download_dir=path)

    # ── події з фонових потоків ───────────────────────────────────────────
    def _poll(self):
        # Перепланування — у finally: якщо котрась подія впаде, решта вікна
        # однаково має жити, а не застигнути без оновлень прогресу.
        try:
            self._drain_events()
        finally:
            self.after(100, self._poll)

    def _drain_events(self):
        self._track_ffmpeg_install()
        try:
            while True:
                event = self.ui_events.get_nowait()
                kind = event[0]
                if kind == "analyzed" and event[1] == self._analyze_token:
                    self._show_info(*event[2:])
                elif kind == "analyze_error" and event[1] == self._analyze_token:
                    self.btn_analyze.configure(state="normal", text="Аналізувати")
                    self._hint(event[2], uikit.STATE_ERROR)
                elif kind == "env":
                    self._show_environment(*event[1:])
                elif kind == "ytdlp_ready":
                    self.lbl_update.configure(text=f"yt-dlp {event[1]} завантажено — "
                                                   "застосується після перезапуску")
                    self.btn_restart.grid(row=0, column=2, padx=(8, 0))
        except queue.Empty:
            pass
        try:
            for _ in range(200):
                kind, job_id, payload = self.manager.events.get_nowait()
                row = self.rows.get(job_id)
                if row is None:
                    continue
                if kind == "progress":
                    row.set_progress(*payload)
                elif kind == "state":
                    row.set_state(*payload)
        except queue.Empty:
            pass

    # ── решта ─────────────────────────────────────────────────────────────
    def _set_theme(self, name):
        ctk.set_appearance_mode(THEMES.get(name, "Dark"))
        settings.set_many(theme=name)

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
        return [r.job for r in self.rows.values() if r.job.state in ("queued", "running")]

    def _restart(self):
        if self._active_jobs():
            self.lbl_update.configure(text="Дочекайтесь завершення завантажень, тоді перезапустіть",
                                      text_color=uikit.STATE_WARN)
            return
        try:
            ytupdate.relaunch()
        except Exception as exc:
            applog.error("Перезапуск не вдався", exc)
            self.lbl_update.configure(text="Не вдалося перезапустити — закрийте й відкрийте вручну",
                                      text_color=uikit.STATE_ERROR)
            return
        self._close_now()

    def on_closing(self):
        if getattr(self, "_closing", False):
            return      # уже зупиняємось — повторне ✕ не перепитує
        active = self._active_jobs()
        if active and not messagebox.askyesno(
                APP_TITLE, f"Ще не завершено завантажень: {len(active)}.\n"
                           "Закрити програму й перервати їх?", parent=self):
            return
        for job in active:
            self.manager.cancel(job)
        if self.manager.is_busy():
            # Скасування спрацює на наступному кроці yt-dlp, після чого
            # потік прибере .part і проміжні файли. Закрийся вікно одразу —
            # потік загинув би разом із процесом і сміття лишилося б у теці.
            self._closing = True
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
        if self.state() == "normal":
            settings.set_many(geometry=self._logical_size())
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
