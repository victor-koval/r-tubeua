"""Головне вікно R-TubeUA: посилання → вибір якості, доріжки, субтитрів → черга.

Тут — каркас вікна, поле посилання, аналіз у фоні, черга між запусками,
панель задач, перезапуск і закриття. Картки й список — окремо:
video_card.py (одне відео), batch.py (пакет), jobs.py (завантаження),
statusbar.py (рядок унизу).
"""

import os
import queue
import threading
import time
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:
    # Перетягування файлів у вікно. Без бібліотеки програма працює як раніше.
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD
    _DND_BASES = (TkinterDnD.DnDWrapper,)
except Exception:
    TkinterDnD = None
    _DND_BASES = ()

from . import (applog, appupdate, credentials, downloader, ffinstall, formats, ftpclient,
               ftphistory, ftpstate, notify, queuestore, settings, uploader,
               sheets, taskbar, tools, uikit, watchdog, ytupdate)
from .batch import BatchCard, plural
from .ftp_card import STATE_TEXT as FTP_STATE_TEXT, FtpCard
from .jobs import FINISHED, JobsPanel
from .statusbar import StatusBar
from .uikit import FONT_SMALL, FONT_UI, FONT_UI_BOLD, GREEN, GREEN_HOVER
from .video_card import VideoCard, load_thumbnail

APP_TITLE = "R-TubeUA"
APP_VERSION = "1.7.0"

DEFAULT_SIZE = (1000, 800)
JOBS_MIN_HEIGHT = 170      # список — щонайменше два рядки, поки картка не відкрита
DIR_SHOWN = 48             # скільки символів шляху до теки показувати під полем
MIN_SIZE = (880, 660)
CLOSE_TIMEOUT = 5          # скільки чекати зупинки завантаження при закритті, с
ERROR_FLASH = 3            # скільки тримати червону смужку в панелі задач після помилки, с
CLIPBOARD_EVERY = 7        # буфер обміну перевіряємо кожне 7-ме опитування (~0,7 с)

THEME_LIGHT, THEME_DARK, THEME_SYSTEM = "Світла", "Темна", "Системна"
THEMES = {THEME_LIGHT: "Light", THEME_DARK: "Dark", THEME_SYSTEM: "System"}


class RTubeApp(ctk.CTk, *_DND_BASES):
    title_text = APP_TITLE

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
        # FTP: вхід — у пам'яті; з «Запам'ятати» — сервер і логін у налаштуваннях,
        # пароль у Диспетчері облікових даних Windows.
        self.ftp_login = self._load_ftp_login()
        self.uploads = uploader.UploadManager(self._ftp_connect, self.ftp_sections)
        self.ftp_tasks = {}                  # task.id → UploadTask
        self.history = None                  # ftphistory.HistoryBuilder, коли збирали
        self.ftp_by_job = {}                 # job.id → UploadTask
        self.ui_events = queue.Queue()     # результати аналізу з фонових потоків
        self.dir_var = ctk.StringVar(value=settings.get("download_dir"))
        self._analyze_token = 0
        self._analyzing = False
        self._queue_dirty = False
        self._was_active = False
        self._error_until = 0.0
        self._settings_window = None
        self._pending_id = ""                # ID товару з рядка «ID посилання» для картки
        self._relaunch = False               # після закриття запустити нову копію (оновлення)
        self._closing = False
        self.taskbar = None
        self._polls = 0
        self._clip_last = self._read_clipboard()     # що було до старту — не підхоплюємо

        self.grid_columnconfigure(0, weight=1)
        # Черзі — мінімум місця на два рядки, хай навіть вікно низьке.
        self.grid_rowconfigure(3, weight=1, minsize=JOBS_MIN_HEIGHT)
        self._build_header()
        self._build_url_card()
        self.video_card = VideoCard(self, self)
        self.batch_card = BatchCard(self, self)
        self.ftp_card = FtpCard(self, self)
        self.jobs_panel = JobsPanel(self, self)
        self.jobs_panel.grid(row=3, column=0, sticky="nsew", padx=22, pady=(0, 10))
        self.statusbar = StatusBar(self, self)
        self.statusbar.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 8))

        self._init_drop()
        self.bind("<Configure>", lambda e: e.widget is self and self.after_idle(self._fit_cards))
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.after(100, self._poll)
        self.after(50, lambda: self.ent_url.focus_set())
        self.after(600, self._init_taskbar)
        self.after(300, self._restore_queue)
        threading.Thread(target=self._check_environment, daemon=True).start()
        self.watchdog = watchdog.Watchdog(threading.get_ident())
        self.watchdog.start()
        applog.info(f"Запуск {APP_TITLE} v{APP_VERSION}")

    def _window_scale(self):
        try:
            return self._get_window_scaling()
        except Exception:
            return 1.0

    # ── побудова ──────────────────────────────────────────────────────────
    def _build_header(self):
        """Чорна шапка з зеленим акцентом — як у rozetka.com.ua і сусідніх програм."""
        header = self._header = ctk.CTkFrame(self, fg_color=uikit.HEADER_BG, corner_radius=0,
                                             height=60)
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
        self.btn_settings = ctk.CTkButton(header, text="⚙  Налаштування", width=130,
                                          command=self.open_settings, **style)
        self.btn_settings.grid(row=0, column=3, padx=(0, 8))
        ctk.CTkButton(header, text="Лог", width=56, command=applog.open_log_folder,
                      **style).grid(row=0, column=4, padx=(0, 22))

    def _build_url_card(self):
        card = self._url_card = uikit.Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=22, pady=(18, 10))
        card.grid_columnconfigure(0, weight=1)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 4))
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

        # Куди зберігати — одне на картку відео й пакета, тож тут, а не в кожній.
        folder = ctk.CTkFrame(card, fg_color="transparent")
        folder.grid(row=1, column=1, sticky="e", padx=(8, 16), pady=(0, 10))
        self.lbl_dir = ctk.CTkLabel(folder, text="", font=FONT_SMALL, cursor="hand2",
                                    text_color=uikit.TEXT_MUTED)
        self.lbl_dir.pack(side="left")
        self.lbl_dir.bind("<Button-1>", lambda e: uikit.open_path(self.dir_var.get()))
        uikit.SecondaryButton(folder, text="Змінити…", width=80, height=24, font=FONT_SMALL,
                              command=self._choose_dir).pack(side="left", padx=(8, 0))
        self.dir_var.trace_add("write", lambda *_: self._show_dir())
        self._show_dir()

    def _show_dir(self):
        path = self.dir_var.get()
        if len(path) > DIR_SHOWN:
            path = path[:12] + "…" + path[-(DIR_SHOWN - 13):]
        self.lbl_dir.configure(text=f"📁 {path}")

    def hint(self, text, color=uikit.TEXT_MUTED):
        self.lbl_url_hint.configure(text=text, text_color=color)

    # ── оточення ──────────────────────────────────────────────────────────
    def _check_environment(self):
        self.send_environment()
        appupdate.cleanup(APP_VERSION)
        # Оновлення — після перевірки оточення, щоб не гальмувати старт; при
        # кожному запуску (без таймера), якщо це не вимкнено в налаштуваннях.
        if not settings.get("check_updates_on_start"):
            return
        try:
            installed = ytupdate.check_and_install()
            if installed:
                self.ui_events.put(("ytdlp_ready", installed))
        except Exception as exc:
            applog.error("Оновлення yt-dlp не вдалося — працюю на поточному", exc)
        try:
            version = appupdate.check_and_download(APP_VERSION)
            if version:
                self.ui_events.put(("app_ready", version))
        except Exception as exc:
            applog.error("Перевірка оновлення R-TubeUA не вдалася", exc)

    def send_environment(self):
        try:
            import yt_dlp.version
            ffmpeg = tools.find_ffmpeg()
            runtimes = tools.probe_js_runtimes()
            self.ui_events.put(("env", yt_dlp.version.__version__, ffmpeg, runtimes))
        except Exception as exc:
            applog.error("Перевірка оточення не вдалася", exc)
            self.ui_events.put(("env", "?", None, []))

    # ── посилання, файл, перетягування, буфер ─────────────────────────────
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
            self.hint("Буфер обміну порожній", uikit.STATE_WARN)
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
                self.hint("Це не список: перетягніть .xlsx, .xls, .csv або .txt", uikit.STATE_WARN)
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

    # ── аналіз ────────────────────────────────────────────────────────────
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
        self.hint("Аналіз скасовано", uikit.STATE_WARN)

    def analyze(self):
        text = self.ent_url.get().strip()
        if not text:
            self.hint("Спершу вставте посилання", uikit.STATE_WARN)
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
            self._show_list(text, [(url, url, pid or "") for pid, url in pairs])
        elif with_ids:
            self._analyze_one(with_ids[0][1], product_id=with_ids[0][0])
        elif len(urls) > 1:
            self._show_list(text, [(u, u) for u in urls])
        else:
            self._analyze_one(urls[0] if urls else tools.clean_url(text))

    def _start_background(self, work, hint):
        self._analyze_token += 1
        token = self._analyze_token
        self._set_analyzing(True)
        self.hint(hint, uikit.STATE_INFO)
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
                thumb = load_thumbnail(info)
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

    # ── картки ────────────────────────────────────────────────────────────
    def _show_info(self, url, info, choices, thumb):
        self._set_analyzing(False)
        if not choices.videos:
            self.hint("У ролику не знайдено жодного відео- чи аудіоформату", uikit.STATE_ERROR)
            return
        self.video_card.show(url, info, choices, thumb, self._pending_id)
        self._pending_id = ""
        self.hint("Готово — перевірте параметри й натисніть «Завантажити»", uikit.STATE_OK)
        self._show_card(self.video_card)

    def _show_batch(self, title, entries):
        self._set_analyzing(False)
        self.video_card.clear()
        self.batch_card.show(title, entries)
        self._show_card(self.batch_card)
        self.hint(f"Знайдено {len(entries)} відео — оберіть параметри для всіх одразу",
                  uikit.STATE_OK)

    def _show_list(self, text, entries):
        """Вставлений список — у таблицю картки пакета; поле звільняється для
        наступного посилання, а не тримає всі рядки одним довгим рядком."""
        self.ent_url.delete(0, "end")
        self._show_batch("", entries)
        with_ids = sum(1 for e in entries if len(e) > 2 and e[2])
        parts = [f"Список з {len(entries)} посилань" + (f" (з ID — {with_ids})" if with_ids else "")
                 + " — у картці нижче"]
        skipped = tools.count_lines_without_links(text)
        if skipped:
            parts.append(f"рядків без посилання пропущено: {skipped}")
        self.hint("  ·  ".join(parts), uikit.STATE_OK)

    def _show_file_batch(self, name, result):
        entries = [(url, url, pid or "") for pid, url in result.pairs]
        self._show_batch(f"з файлу {name}", entries)
        videos = len({e[0] for e in entries})
        if result.with_ids:
            parts = [f"{name}: {len(entries)} {plural(len(entries), 'товар', 'товари', 'товарів')}"
                     + (f", {videos} різних відео" if videos != len(entries) else "")]
        else:
            parts = [f"{name}: {len(entries)} відео"]
        if result.with_ids < len(entries):
            parts.append(f"з ID — {result.with_ids}, решта назвуться латиницею")
        if result.skipped:
            parts.append(f"рядків без посилання пропущено: {result.skipped}")
        if result.sheet and result.sheet != name:
            parts.append(f"аркуш «{result.sheet}»")
        self.hint("  ·  ".join(parts), uikit.STATE_OK)

    def close_batch(self):
        self._show_card(None)
        self.hint("Пакет скасовано", uikit.STATE_WARN)
        self._ready_for_next()

    def close_video(self):
        self.video_card.clear()
        self._show_card(None)
        self.hint("Відео не додано", uikit.STATE_WARN)
        self._ready_for_next()

    def _show_card(self, card):
        """Одна картка (відео чи пакета) або жодної. Поки картка відкрита,
        список завантажень згорнутий до заголовка — інакше на невисокому
        вікні картка не вміщалась і кнопка «Завантажити» зникала за краєм."""
        for other in (self.video_card, self.batch_card, self.ftp_card):
            if other is not card:
                other.grid_remove()
        if card is not None:
            card.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 10))
        self.jobs_panel.set_compact(card is not None)
        self.grid_rowconfigure(3, minsize=0 if card is not None else JOBS_MIN_HEIGHT)
        if card in (self.batch_card, self.ftp_card):
            self.after_idle(self._fit_cards)

    def _fit_cards(self):
        """Таблиця пакета (чи плану FTP) — стільки рядків, скільки влазить у вікно."""
        card = next((c for c in (self.batch_card, self.ftp_card) if c.winfo_manager()), None)
        if card is None:
            return
        self.update_idletasks()
        tree = card.tree
        row_px = 24
        shown = int(tree.cget("height"))
        heading = max(0, tree.winfo_reqheight() - shown * row_px)
        others = sum(w.winfo_reqheight() for w in (self._header, self._url_card, self.jobs_panel,
                                                    self.statusbar))
        paddings = 28 + 10 + 10 + 8 + 12
        card_fixed = card.winfo_reqheight() - tree.winfo_reqheight()
        free = self.winfo_height() - others - paddings - card_fixed - heading
        rows = free // row_px
        if rows != shown or rows < len(tree.get_children()):
            card.fit(rows)

    def _ready_for_next(self):
        self.ent_url.delete(0, "end")
        self.ent_url.focus_set()

    def _choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get(), parent=self,
                                       title="Куди зберігати відео")
        if path:
            path = os.path.normpath(path)
            self.dir_var.set(path)
            settings.set_many(download_dir=path)

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
        self.statusbar.install_ffmpeg()
        return True

    def download(self):
        built = self.video_card.build_job(self.dir_var.get())
        if built is None:
            self.hint("Спершу проаналізуйте посилання", uikit.STATE_WARN)
            return
        job, progressive = built
        if not self._ensure_ffmpeg(downloader.needs_ffmpeg(job, progressive=progressive)):
            return
        # Вибір у картці стосується лише цього завантаження — значення за
        # замовчуванням живуть у вікні налаштувань.
        if not self.enqueue([job]):
            return
        self.hint(f"«{job.title}» додано в чергу. Можна вставляти наступне посилання.",
                  uikit.STATE_OK)
        # Картку ховаємо: відео вже в черзі, а місце потрібне списку
        # завантажень. Наступне посилання покаже її знову.
        self.video_card.clear()
        self._show_card(None)
        self._ready_for_next()

    def download_batch(self):
        jobs = self.batch_card.build_jobs(self.dir_var.get())
        if not jobs:
            return
        if not self._ensure_ffmpeg(any(downloader.needs_ffmpeg(j) for j in jobs)):
            return
        added = self.enqueue(jobs)
        skipped = len(jobs) - added
        products = sum(1 + len(j.also_for) for j in jobs)
        text = f"Додано в чергу: {added} відео"
        if products != len(jobs):
            text += f" для {products} товарів (однакові відео качаються раз)"
        if skipped:
            text += f" · ще {skipped} вже були в черзі"
        self.hint(text, uikit.STATE_OK)
        self._show_card(None)
        self._ready_for_next()

    def enqueue(self, jobs):
        added = self.jobs_panel.enqueue(jobs)
        if not added and jobs:
            self.hint("Це відео вже в черзі", uikit.STATE_WARN)
        self.mark_queue_dirty()
        return added

    def mark_queue_dirty(self):
        self._queue_dirty = True

    # ── черга між запусками ───────────────────────────────────────────────
    def _restore_queue(self):
        if not settings.get("resume_queue"):
            return
        jobs = queuestore.load()
        if not jobs:
            return
        added = self.enqueue(jobs)
        if added:
            applog.info(f"Відновлено незавершених завантажень: {added}")
            self.hint(f"Продовжую незавершені завантаження: {added}", uikit.STATE_INFO)

    def _save_queue(self):
        self._queue_dirty = False
        if self._closing:
            return      # під час закриття черга вже збережена — скасування її не стирає
        if not settings.get("resume_queue"):
            return
        jobs = sorted(self.jobs_panel.active_jobs(), key=lambda j: j.id)
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
        session = self.jobs_panel.session_jobs()
        done = sum(1 for j in session if j.state in FINISHED)
        total = max(1, len(session))
        fraction = self.jobs_panel.running_fraction()
        if self.manager.paused and active:
            self.taskbar.set_progress(done / total, taskbar.PAUSED)
        elif fraction is not None:
            self.taskbar.set_progress((done + fraction) / total)
        elif active or self._analyzing or ffinstall.in_progress():
            self.taskbar.set_state(taskbar.INDETERMINATE)
        else:
            self.taskbar.clear()

    def _on_queue_idle(self):
        """Черга щойно спорожніла: сповіщення (якщо вікно не перед очима)."""
        session = self.jobs_panel.session_jobs()
        self.jobs_panel.session.clear()
        done = sum(1 for j in session if j.state == "done")
        failed = sum(1 for j in session if j.state == "error")
        if not (done or failed):
            return
        report_path = None
        products = sum(1 + len(j.also_for) for j in session)
        if settings.get("auto_report") and products >= 2:
            report_path = self.jobs_panel.save_report(auto=True)
        if not settings.get("notify_done"):
            return
        if self.focus_displayof() is not None and self.state() != "iconic":
            return
        text = f"Завантажено {done} відео" if done else "Нічого не завантажилось"
        if failed:
            text += f" · не вдалося {failed}"
        if report_path:
            text += f" · звіт {os.path.basename(report_path)}"
        notify.show(APP_TITLE, text)

    # ── FTP ───────────────────────────────────────────────────────────────
    def _load_ftp_login(self):
        login = {"host": "", "user": "", "password": ""}
        if not settings.get("ftp_remember"):
            return login
        login.update(host=settings.get("ftp_host") or "", user=settings.get("ftp_user") or "")
        try:
            saved = credentials.load()
            if saved:
                login["user"] = login["user"] or saved[0]
                login["password"] = saved[1]
        except OSError as exc:
            applog.warning(f"Пароль FTP не прочитано з Диспетчера облікових даних: {exc}")
        return login

    def set_ftp_login(self, host, user, password, remember, persist=True):
        """Вхід на FTP з Налаштувань. persist — записати (а не лише в пам'ять)."""
        self.ftp_login = {"host": host.strip(), "user": user.strip(), "password": password}
        if not persist:
            return
        try:
            if remember:
                settings.set_many(ftp_remember=True, ftp_host=host.strip(), ftp_user=user.strip())
                if password:
                    credentials.save(user.strip(), password)
            else:
                settings.set_many(ftp_remember=False, ftp_host="", ftp_user="")
                credentials.delete()
        except OSError as exc:
            applog.error("Вхід FTP не збережено", exc)

    def ftp_ready(self):
        return all(self.ftp_login.get(k) for k in ("host", "user", "password"))

    def ftp_sections(self):
        raw = settings.get("ftp_sections") or ""
        return [s.strip().strip("/") for s in raw.replace(";", ",").split(",") if s.strip()]

    def uploads_section(self):
        """Поточний розділ — перший незабитий."""
        full = self.uploads.full_sections()
        return next((s for s in self.ftp_sections() if s not in full), "")

    def _ftp_connect(self):
        """З потоку заливання: підключений FtpClient."""
        login = dict(self.ftp_login)
        host, _, port = login["host"].partition(":")
        return ftpclient.FtpClient(host, login["user"], login["password"],
                                   port=int(port) if port.isdigit() else 21).connect()

    def open_ftp(self):
        """«↑ На FTP»: план для готових файлів із ID товару."""
        if not self.ftp_ready():
            self.hint("Вкажіть сервер, логін і пароль FTP у Налаштуваннях", uikit.STATE_WARN)
            self.open_settings(section="ftp")
            return
        tasks, new = [], []
        for job in self.jobs_panel.ftp_jobs():
            task = self.ftp_by_job.get(job.id)
            if task is None or task.local != job.filepath:
                task = uploader.UploadTask(job.filepath, job.product_id, job_id=job.id)
                self.ftp_tasks[task.id] = task
                self.ftp_by_job[job.id] = task
                new.append(task)
            elif task.state == uploader.ERROR:
                new.append(task)        # помилка плану (зв'язок, пароль) — ще раз
            tasks.append(task)
        if not tasks:
            self.hint("Немає готових файлів з ID товару, які ще не на FTP", uikit.STATE_WARN)
            return
        self.video_card.clear()
        self.ftp_card.show(tasks)
        self._show_card(self.ftp_card)
        if new:
            self.uploads.plan(new)
        self.hint("Перевірте теки й натисніть «Залити на FTP»", uikit.STATE_OK)

    def start_history(self):
        """Фоновий збір історії розкладання — для розділів по черзі."""
        if not self.history_running():
            self.history = ftphistory.HistoryBuilder(self._ftp_connect, self.ftp_sections()).start()
            applog.info("Збір історії FTP розпочато")

    def stop_history(self):
        if self.history is not None:
            self.history.stop()

    def history_running(self):
        return self.history is not None and self.history.status["running"]

    def close_ftp(self):
        self._show_card(None)
        self._ready_for_next()

    def start_upload(self, tasks):
        self.uploads.upload(tasks)
        for task in tasks:
            self._ftp_to_job(task)

    def refresh_ftp_tree(self):
        """Перечитати теки розділів (у фоні) і заново визначити ще не залиті."""
        if not self.ftp_ready():
            self.open_settings(section="ftp")
            return
        sections = self.ftp_sections()

        def work():
            try:
                client = self._ftp_connect()
                try:
                    tree = client.read_tree(sections)
                finally:
                    client.close()
                ftpstate.save_tree(tree)
                self.ui_events.put(("ftp_tree", sum(len(p) for p in tree.values()), None))
            except Exception as exc:
                applog.error("Дерево тек FTP не прочиталось", exc)
                self.ui_events.put(("ftp_tree", None, uploader.human_error(exc)))

        self.hint("Читаю теки на FTP…", uikit.STATE_INFO)
        threading.Thread(target=work, daemon=True).start()

    def _ftp_tree_read(self, count, error):
        if error:
            self.hint(f"Теки FTP не прочитано: {error}", uikit.STATE_ERROR)
            return
        self.hint(f"Теки FTP оновлено: {count}", uikit.STATE_OK)
        again = [t for t in self.ftp_card.tasks if t.state in
                 (uploader.PLANNED, uploader.NEED_CHOICE, uploader.ERROR)
                 and t.source != "rule"]
        if again:
            self.uploads.plan(again)

    def _ftp_to_job(self, task):
        """Стан заливання — у рядок завдання (лише коли заливання почалось)."""
        job = self.jobs_panel.jobs.get(task.job_id)
        if job is None or task.state in (uploader.PLANNING, uploader.PLANNED,
                                         uploader.NEED_CHOICE, uploader.CONFIRM):
            return
        text = FTP_STATE_TEXT.get(task.state, task.state)
        if task.state == uploader.UPLOADING and task.fraction:
            text = f"заливаю… {task.fraction * 100:.0f}%"
        if task.state in (uploader.UPLOADED, uploader.ALREADY) and task.ftp_path:
            text += f" — {task.ftp_path}"
        elif task.state == uploader.ERROR and task.note:
            text = task.note
        elif task.folder and task.state in (uploader.QUEUED, uploader.UPLOADING):
            text += f" → {task.folder}"
        self.jobs_panel.set_ftp(job, task.state, text, task.ftp_path)

    def _drain_uploads(self):
        changed = {}
        try:
            for _ in range(500):
                _kind, task_id, _payload = self.uploads.events.get_nowait()
                task = self.ftp_tasks.get(task_id)
                if task is not None:
                    changed[task_id] = task
        except queue.Empty:
            pass
        for task in changed.values():
            if self.ftp_card.winfo_manager():
                self.ftp_card.update_task(task)
            self._ftp_to_job(task)

    # ── налаштування ──────────────────────────────────────────────────────
    def open_settings(self, section=None):
        """section — розділ, до якого прокрутити (напр. "ftp")."""
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.focus_set()
            return
        from .settings_dialog import SettingsDialog
        self._settings_window = SettingsDialog(self, self._on_setting_changed, focus=section)

    def _on_setting_changed(self, key, value):
        if key == "theme":
            ctk.set_appearance_mode(THEMES.get(value, "Dark"))
            self.batch_card.apply_style()
            self.ftp_card.apply_style()
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
            self.statusbar.show_ytdlp_ready(value)
            self.show_update_badge()
        elif key == "app_ready":
            self.statusbar.show_update_ready()
            self.show_update_badge()

    def show_update_badge(self):
        """Готове оновлення: «⚙ Є оновлення» зеленим у шапці веде в Налаштування,
        де кнопка «Оновити й перезапустити» (рядок унизу вікна колеги не помічали).
        Відкрите вікно налаштувань одразу показує кнопку."""
        self.btn_settings.configure(text="⚙  Є оновлення", width=150, fg_color=GREEN,
                                    hover_color=GREEN_HOVER, text_color="#ffffff")
        window = self._settings_window
        if window is not None and window.winfo_exists():
            window.refresh_updates()

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
        self.statusbar.track_ffmpeg_install()
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
                    self.hint(event[2], uikit.STATE_ERROR)
                elif kind == "env":
                    self.statusbar.show_environment(*event[1:])
                elif kind == "ytdlp_ready":
                    self.statusbar.show_ytdlp_ready(event[1])
                    self.show_update_badge()
                elif kind == "app_ready":
                    self.statusbar.show_update_ready()
                    self.show_update_badge()
                elif kind == "ftp_tree":
                    self._ftp_tree_read(event[1], event[2])
        except queue.Empty:
            pass

        self._drain_uploads()
        changed, error = self.jobs_panel.handle_events()
        if changed:
            self._queue_dirty = True
        if error:
            self._error_until = time.monotonic() + ERROR_FLASH
        active = self.jobs_panel.refresh()
        if self._was_active and not active:
            self._on_queue_idle()
        self._was_active = bool(active)
        if self._queue_dirty:
            self._save_queue()
        self._update_taskbar(active)

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

    def restart_blocker(self):
        """Чому перезапускати зараз не можна (текст) або None."""
        if self.jobs_panel.active_jobs() and not settings.get("resume_queue"):
            return "Дочекайтесь завершення завантажень, тоді перезапустіть"
        return None

    def restart(self):
        blocker = self.restart_blocker()
        if blocker:
            self.statusbar.set_update_note(blocker, uikit.STATE_WARN)
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
        if self._closing:
            return      # уже зупиняємось — повторне ✕ не перепитує
        active = self.jobs_panel.active_jobs()
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
        for task in self.ftp_tasks.values():
            if task.state not in uploader.FINISHED:
                self.uploads.cancel(task)       # недолитий .rtube-part докачається наступного разу
        self.stop_history()                     # зібране збережеться, продовжиться наступного разу
        if self.manager.is_busy() or self.uploads.is_busy():
            # Скасування спрацює на наступному кроці yt-dlp, після чого
            # потік прибере .part і проміжні файли. Закрийся вікно одразу —
            # потік загинув би разом із процесом і сміття лишилося б у теці.
            self.statusbar.set_status("Зупиняю завантаження…", uikit.STATE_WARN)
            self._wait_and_close(deadline=time.monotonic() + CLOSE_TIMEOUT)
            return
        self._close_now()

    def _wait_and_close(self, deadline):
        busy = self.manager.is_busy() or self.uploads.is_busy()
        if busy and time.monotonic() < deadline:
            self.after(200, lambda: self._wait_and_close(deadline))
            return
        if busy:
            applog.warning("Завантаження чи заливання не зупинилось за відведений час — "
                           "закриваю як є")
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
