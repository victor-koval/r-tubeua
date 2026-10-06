"""Вікно налаштувань (⚙ у шапці).

Кожна зміна зберігається одразу — без кнопки «Зберегти», яку легко забути.
Значення тут — це те, з чим відкривається картка відео й картка пакета;
вибір у самій картці стосується лише того завантаження.
"""

import os
import threading
import tkinter
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import applog, appupdate, settings, uikit, ytupdate
from .uikit import FONT_SMALL, FONT_UI, FONT_UI_BOLD, GREEN, GREEN_HOVER

QUALITY_OPTIONS = [("Найкраща доступна", 0), ("2160p (4K)", 2160), ("1440p", 1440),
                   ("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]
AUDIO_OPTIONS = [("Українська, якщо є, інакше оригінал", "uk"), ("Оригінальна", "orig")]
SUBS_OPTIONS = [("Вшивати у відео", "embed"), ("Окремим файлом .srt", "file")]


def _label_for(options, value, default=0):
    for label, val in options:
        if val == value:
            return label
    return options[default][0]


def _value_for(options, label):
    for lab, val in options:
        if lab == label:
            return val
    return options[0][1]


class SettingsDialog(ctk.CTkToplevel):
    """on_change(key, value) — щоб вікно одразу застосовувало тему, панель задач тощо."""

    def __init__(self, master, on_change):
        super().__init__(master)
        # Ховаємо до кінця побудови: CTkToplevel показує себе за 5 мс, і
        # було видно недобудоване вікно 686×220 не на своєму місці, яке
        # потім розросталось до повного й переїжджало. Покаже _show().
        self.withdraw()
        self.on_change = on_change
        self.title("Налаштування — R-TubeUA")
        self.configure(fg_color=uikit.SURFACE_SUNKEN)
        # Не self.resizable(): у CTkToplevel він ще раз ховає й показує вікно,
        # щоб перефарбувати заголовок, — це було друге мигання.
        tkinter.Toplevel.resizable(self, False, False)
        self.transient(master)
        uikit.apply_window_icon(self)
        self.grid_columnconfigure(0, weight=1)

        # Розділи — у прокручуваній області: усе разом ~880 px заввишки й на
        # ноутбуці 1366×768 (чи Full HD зі 125%) не влізло б, сховавши «Готово».
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent", width=660)
        self.body.grid(row=0, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)

        self._vars = {}
        row = 0
        row = self._section(row, "Завантаження", self._build_downloads)
        row = self._section(row, "Робота у фоні", self._build_background)
        row = self._section(row, "Оновлення", self._build_updates)
        row = self._section(row, "Вигляд", self._build_look)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=22, pady=(4, 18))
        uikit.SecondaryButton(footer, text="📁 Тека налаштувань і логів",
                              command=self._open_config_dir).pack(side="left")
        uikit.SecondaryButton(footer, text="Скинути до стандартних",
                              command=self._reset).pack(side="left", padx=(8, 0))
        ctk.CTkButton(footer, text="Готово", width=100, command=self.destroy).pack(side="right")

        # Після того як CTkToplevel відпрацює своє перефарбування заголовка (5–10 мс).
        self.after(30, self._show)

    def iconbitmap(self, bitmap=None, default=None):
        # CTkToplevel через 200 мс ставить свою іконку поверх нашої — пропускаємо її,
        # інакше іконка вікна помітно блимала.
        if bitmap and "CustomTkinter_icon" in str(bitmap):
            return None
        return super().iconbitmap(bitmap, default)

    def _fit_height(self):
        """Висота прокручуваної області — уся, якщо влазить, інакше до краю екрана."""
        self.update_idletasks()
        try:
            scale = self._get_window_scaling()
        except Exception:
            scale = 1.0
        inner = sum(w.winfo_reqheight() for w in self.body.winfo_children()) / scale + 60
        _w, screen_h = uikit.work_area()
        available = (screen_h / scale if screen_h else 760) - 210    # заголовок вікна, кнопки внизу, запас
        self.body.configure(height=max(300, min(inner, available)))

    def _show(self):
        """Розмір і місце — поки вікно сховане, і лише тоді показ: один раз,
        одразу готове."""
        try:
            self._fit_height()
            self.update_idletasks()
            master = self.master
            # Поки вікно сховане, winfo_width() дає 1 — беремо запитаний розмір.
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
            y = master.winfo_rooty() + 40
            # Сирий tk-geometry: позиція в справжніх пікселях, як і winfo_root*.
            tkinter.Toplevel.geometry(self, f"+{max(0, x)}+{max(0, y)}")
        except Exception as exc:
            applog.warning(f"Вікно налаштувань: не вдалося розмістити — {exc}")
        try:
            # Темний заголовок CTkToplevel ставив у тому самому повторному
            # показі, який ми прибрали, — фарбуємо самі, поки вікно сховане
            # (воно так і лишиться схованим: стан до фарбування — withdrawn).
            self._windows_set_titlebar_color(self._get_appearance_mode())
        except Exception:
            pass
        self.after(20, self._reveal)

    def _reveal(self):
        self.deiconify()
        try:
            self.grab_set()
            self.focus_set()
        except Exception:
            pass

    # ── побудова ──
    def _section(self, row, title, builder):
        card = uikit.Card(self.body, title=title)
        card.grid(row=row, column=0, sticky="ew", padx=22, pady=(16 if row == 0 else 0, 10))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=card.body_row, column=0, sticky="ew", padx=16, pady=(0, 12))
        body.grid_columnconfigure(1, weight=1)
        builder(body)
        return row + 1

    def _label(self, body, row, text):
        ctk.CTkLabel(body, text=text, font=FONT_UI_BOLD, anchor="w", width=210).grid(
            row=row, column=0, sticky="w", pady=4)

    def _menu(self, body, row, text, key, options):
        self._label(body, row, text)
        var = ctk.StringVar(value=_label_for(options, settings.get(key)))
        menu = ctk.CTkOptionMenu(body, values=[o[0] for o in options], variable=var, width=300,
                                 dynamic_resizing=False,
                                 command=lambda label: self._set(key, _value_for(options, label)))
        menu.grid(row=row, column=1, sticky="w", pady=4)
        self._vars[key] = (var, options)

    def _segment(self, body, row, text, key, values):
        self._label(body, row, text)
        seg = ctk.CTkSegmentedButton(body, values=list(values), selected_color=GREEN,
                                     selected_hover_color=GREEN_HOVER,
                                     command=lambda v: self._set(key, v))
        seg.set(settings.get(key) if settings.get(key) in values else values[0])
        seg.grid(row=row, column=1, sticky="w", pady=4)
        self._vars[key] = (seg, None)

    def _check(self, body, row, text, key, hint=None):
        var = ctk.BooleanVar(value=bool(settings.get(key)))
        ctk.CTkCheckBox(body, text=text, variable=var, font=FONT_UI,
                        command=lambda: self._set(key, bool(var.get()))).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(6, 0 if hint else 4))
        if hint:
            ctk.CTkLabel(body, text=hint, font=FONT_SMALL, text_color=uikit.TEXT_MUTED,
                         anchor="w", justify="left", wraplength=520).grid(
                row=row + 1, column=0, columnspan=2, sticky="w", padx=(30, 0), pady=(0, 4))
        self._vars[key] = (var, None)
        return row + (2 if hint else 1)

    def _build_downloads(self, body):
        self._label(body, 0, "Тека за замовчуванням")
        folder = ctk.CTkFrame(body, fg_color="transparent")
        folder.grid(row=0, column=1, sticky="ew", pady=4)
        folder.grid_columnconfigure(0, weight=1)
        self.dir_var = ctk.StringVar(value=settings.get("download_dir"))
        ctk.CTkEntry(folder, textvariable=self.dir_var, state="readonly", width=300).grid(
            row=0, column=0, sticky="ew")
        uikit.SecondaryButton(folder, text="Змінити…", width=96,
                              command=self._choose_dir).grid(row=0, column=1, padx=(8, 0))

        self._menu(body, 1, "Якість за замовчуванням", "max_height", QUALITY_OPTIONS)
        self._menu(body, 2, "Звукова доріжка", "preferred_audio", AUDIO_OPTIONS)
        self._segment(body, 3, "Формат відео", "container", ("mp4", "mkv"))
        self._segment(body, 4, "Формат звуку", "audio_container", ("m4a", "mp3"))
        self._menu(body, 5, "Субтитри", "subs_mode", SUBS_OPTIONS)
        self._check(body, 6, "Додавати оригінальну доріжку другою", "keep_original")
        self._check(body, 7, "Підхоплювати посилання з буфера обміну", "watch_clipboard",
                    "Скопіювали посилання на YouTube у браузері — воно саме з'являється в полі "
                    "й аналізується. Те, що було в буфері раніше, не чіпається.")

    def _build_background(self, body):
        row = self._check(body, 0, "Сповіщення Windows, коли все завантажено", "notify_done",
                          "Лише якщо вікно програми не на передньому плані.")
        row = self._check(body, row, "Прогрес на іконці в панелі задач", "taskbar_progress")
        self._check(body, row, "Продовжувати незавершені завантаження після перезапуску",
                    "resume_queue",
                    "Закриття посеред завантаження не перепитує: недокачане лишається "
                    "й докачується при наступному запуску.")

    def _build_updates(self, body):
        row = self._check(body, 0, "Перевіряти оновлення при запуску програми",
                          "check_updates_on_start",
                          "Нова версія R-TubeUA (з GitHub) і свіжий yt-dlp (він лагодить "
                          "завантаження, коли YouTube щось змінює) завантажуються й "
                          "перевіряються у фоні; унизу вікна з'являється «Оновити й "
                          "перезапустити». Вручну — «Перевірити зараз».")
        line = ctk.CTkFrame(body, fg_color="transparent")
        line.grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.btn_check = uikit.SecondaryButton(line, text="Перевірити зараз", width=150,
                                               command=self._check_now)
        self.btn_check.pack(side="left")
        self.lbl_check = ctk.CTkLabel(line, text=self._ytdlp_text(), font=FONT_SMALL,
                                      text_color=uikit.TEXT_MUTED)
        self.lbl_check.pack(side="left", padx=(12, 0))

    def _build_look(self, body):
        from .app import THEMES
        self._segment(body, 0, "Тема", "theme", tuple(THEMES))

    # ── дії ──
    def _set(self, key, value):
        settings.set_many(**{key: value})
        try:
            self.on_change(key, value)
        except Exception as exc:
            applog.error(f"Застосування налаштування {key} не вдалося", exc)

    def _choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get(), parent=self,
                                       title="Куди зберігати відео")
        if path:
            path = os.path.normpath(path)
            self.dir_var.set(path)
            self._set("download_dir", path)

    def _ytdlp_text(self):
        from .app import APP_VERSION
        st = ytupdate.state
        text = f"R-TubeUA {APP_VERSION} · yt-dlp {st.get('active') or '?'}"
        if st.get("source") == "lib":
            text += " (оновлений)"
        if st.get("pending"):
            text += f" · yt-dlp {st['pending']} після перезапуску"
        if appupdate.state["version"]:
            text += f" · R-TubeUA {appupdate.state['version']} готова"
        return text

    def _check_now(self):
        self.btn_check.configure(state="disabled", text="Перевіряю…")
        result = {}

        def work():
            from .app import APP_VERSION
            try:
                result["installed"] = ytupdate.check_and_install()
            except Exception as exc:
                applog.error("Ручна перевірка yt-dlp не вдалася", exc)
                result["error"] = str(exc)
            if appupdate.enabled():
                try:
                    result["app"] = appupdate.check_and_download(APP_VERSION)
                except Exception as exc:
                    applog.error("Ручна перевірка оновлення R-TubeUA не вдалася", exc)
                    result["error"] = str(exc)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self._wait_check(thread, result)

    def _wait_check(self, thread, result):
        if thread.is_alive():
            self.after(300, lambda: self._wait_check(thread, result))
            return
        if not self.winfo_exists():
            return
        self.btn_check.configure(state="normal", text="Перевірити зараз")
        if result.get("error"):
            self.lbl_check.configure(text=f"не вдалося: {result['error']}"[:90],
                                     text_color=uikit.STATE_ERROR)
        elif result.get("installed") or result.get("app"):
            self.lbl_check.configure(text=self._ytdlp_text(), text_color=uikit.STATE_OK)
            if result.get("installed"):
                self.on_change("ytdlp_ready", result["installed"])
            if result.get("app"):
                self.on_change("app_ready", result["app"])
        else:
            self.lbl_check.configure(text=self._ytdlp_text() + " — найсвіжіший",
                                     text_color=uikit.STATE_OK)

    def _open_config_dir(self):
        os.makedirs(settings.CONFIG_DIR, exist_ok=True)
        uikit.open_path(settings.CONFIG_DIR)

    def _reset(self):
        if not messagebox.askyesno("R-TubeUA", "Повернути всі налаштування до стандартних?",
                                   parent=self):
            return
        settings.reset_user()
        for key in settings.USER_KEYS:
            self.on_change(key, settings.get(key))
        self.dir_var.set(settings.get("download_dir"))
        for key, (widget, options) in self._vars.items():
            value = settings.get(key)
            if options is not None:
                widget.set(_label_for(options, value))
            elif isinstance(widget, ctk.CTkSegmentedButton):
                widget.set(value)
            else:
                widget.set(bool(value))
