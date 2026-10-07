"""Вікно налаштувань (⚙ у шапці).

Кожна зміна зберігається одразу — без кнопки «Зберегти», яку легко забути.
Значення тут — це те, з чим відкривається картка відео й картка пакета;
вибір у самій картці стосується лише того завантаження.
"""

import os
import threading
import time
import tkinter
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import applog, appupdate, ftpstate, settings, uikit, uploader, ytupdate
from .uikit import FONT_SMALL, FONT_UI, FONT_UI_BOLD, GREEN, GREEN_HOVER

QUALITY_OPTIONS = [("Найкраща доступна", 0), ("2160p (4K)", 2160), ("1440p", 1440),
                   ("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]
AUDIO_OPTIONS = [("Українська, якщо є, інакше оригінал", "uk"), ("Оригінальна", "orig")]
SUBS_OPTIONS = [("Вшивати у відео", "embed"), ("Окремим файлом .srt", "file")]
TAB_DOWNLOADS, TAB_BACKGROUND, TAB_FTP, TAB_UPDATES = "Завантаження", "Фон і вигляд", "FTP", \
    "Оновлення"
TABS = (TAB_DOWNLOADS, TAB_BACKGROUND, TAB_FTP, TAB_UPDATES)
FOCUS_TABS = {"ftp": TAB_FTP, "updates": TAB_UPDATES}
TITLE_BAR = 48          # заголовок вікна Windows і запас до краю екрана, справжні пікселі


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

    def __init__(self, master, on_change, focus=None):
        super().__init__(master)
        # Ховаємо до кінця побудови: CTkToplevel показує себе за 5 мс, і
        # було видно недобудоване вікно 686×220 не на своєму місці, яке
        # потім розросталось до повного й переїжджало. Покаже _show().
        self.withdraw()
        self.on_change = on_change
        self._focus = focus             # вкладка, з якої відкрити ("ftp", "updates")
        self.title("Налаштування — R-TubeUA")
        self.configure(fg_color=uikit.SURFACE_SUNKEN)
        # Не self.resizable(): у CTkToplevel він ще раз ховає й показує вікно,
        # щоб перефарбувати заголовок, — це було друге мигання.
        tkinter.Toplevel.resizable(self, False, False)
        self.transient(master)
        uikit.apply_window_icon(self)
        self.grid_columnconfigure(0, weight=1)

        # Вкладки: усе одним стовпчиком було ~1300 px заввишки й не влазило навіть
        # на 1440 px, а на ноутбуці — тим паче. Кожна вкладка — прокручувана
        # сторінка однакової висоти (див. _fit_height), тож вікно не стрибає.
        self.seg_tabs = ctk.CTkSegmentedButton(self, values=list(TABS), font=FONT_UI_BOLD,
                                               selected_color=GREEN,
                                               selected_hover_color=GREEN_HOVER,
                                               command=self._select_tab)
        self.seg_tabs.grid(row=0, column=0, sticky="w", padx=22, pady=(16, 4))
        self._pages = {}
        for name in TABS:
            page = ctk.CTkScrollableFrame(self, fg_color="transparent", width=660)
            page.grid_columnconfigure(0, weight=1)
            self._pages[name] = page

        self._vars = {}
        self._section(TAB_DOWNLOADS, 0, "Завантаження", self._build_downloads)
        self._section(TAB_BACKGROUND, 0, "Робота у фоні", self._build_background)
        self._section(TAB_BACKGROUND, 1, "Вигляд", self._build_look)
        self._section(TAB_FTP, 0, "Вхід на FTP", self._build_ftp_login)
        self._section(TAB_FTP, 1, "База розкладання", self._build_ftp_base)
        self._section(TAB_UPDATES, 0, "Оновлення", self._build_updates)
        start = FOCUS_TABS.get(focus) or (TAB_UPDATES if self.updates_pending() else TAB_DOWNLOADS)
        self._select_tab(start)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=22, pady=(4, 18))
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

    def _scale(self):
        """Скільки справжніх пікселів в одиниці CTk: DPI Windows × масштаб програми."""
        try:
            return ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            return 1.0

    def _fit_height(self):
        """Сторінки — заввишки з найвищу вкладку, але так, щоб усе вікно разом із
        заголовком і «Готово» влізло в робочу область екрана. Що не влізло —
        прокручується всередині вкладки."""
        self.update_idletasks()
        scale = self._scale()
        content = max(page.winfo_reqheight() for page in self._pages.values()) / scale + 8
        for page in self._pages.values():
            page.configure(height=content)
        self.update_idletasks()
        _l, _t, _w, work_h = uikit.work_rect()
        if work_h:
            over = self.winfo_reqheight() - (work_h - TITLE_BAR)
            if over > 0:
                fitted = max(160, content - over / scale)
                for page in self._pages.values():
                    page.configure(height=fitted)
                self.update_idletasks()

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
            # Не нижче краю робочої області: інакше «Готово» опинялось за екраном.
            left, top, width, height = uikit.work_rect()
            if height:
                y = max(top, min(y, top + height - self.winfo_reqheight() - TITLE_BAR))
                x = max(left, min(x, left + width - self.winfo_reqwidth()))
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

    def _select_tab(self, name):
        for tab, page in self._pages.items():
            if tab == name:
                page.grid(row=1, column=0, sticky="nsew")
            else:
                page.grid_remove()
        self.seg_tabs.set(name)
        self.tab = name

    # ── побудова ──
    def _section(self, tab, row, title, builder):
        card = uikit.Card(self._pages[tab], title=title)
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
        row = self._check(body, row, "Продовжувати незавершені завантаження після перезапуску",
                          "resume_queue",
                          "Закриття посеред завантаження не перепитує: недокачане лишається "
                          "й докачується при наступному запуску.")
        self._check(body, row, "Зберігати звіт, коли пакет завантажено", "auto_report",
                    "xlsx у теці з відео — як кнопка «📊 Звіт». Лише для пакетів "
                    "(від двох товарів), не для одного відео.")

    def _build_ftp_login(self, body):
        login = self.master.ftp_login
        self.ftp_host = ctk.StringVar(value=login.get("host", ""))
        self.ftp_user = ctk.StringVar(value=login.get("user", ""))
        self.ftp_pass = ctk.StringVar(value=login.get("password", ""))
        self.ftp_remember = ctk.BooleanVar(value=bool(settings.get("ftp_remember")))
        for row, (text, var, extra) in enumerate((
                ("Сервер", self.ftp_host, {"placeholder_text": "адреса або адреса:порт"}),
                ("Логін", self.ftp_user, {}),
                ("Пароль", self.ftp_pass, {"show": "•"}))):
            self._label(body, row, text)
            entry = ctk.CTkEntry(body, textvariable=var, width=300, font=FONT_UI, **extra)
            entry.grid(row=row, column=1, sticky="w", pady=4)
            setattr(self, ("ftp_host_entry", "ftp_user_entry", "ftp_pass_entry")[row], entry)
            var.trace_add("write", lambda *_: self._apply_ftp(persist=False))
        ctk.CTkCheckBox(body, text="Запам'ятати вхід", variable=self.ftp_remember, font=FONT_UI,
                        command=lambda: self._apply_ftp(persist=True)).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._hint(body, 4, "Без цього сервер, логін і пароль живуть лише до закриття програми. "
                            "Пароль зберігається в Диспетчері облікових даних Windows, "
                            "не у файлах програми.", indent=True)
        self._label(body, 5, "Розділи по черзі")
        self.ftp_sections = ctk.StringVar(value=settings.get("ftp_sections"))
        ctk.CTkEntry(body, textvariable=self.ftp_sections, width=300, font=FONT_UI).grid(
            row=5, column=1, sticky="w", pady=4)
        self.ftp_sections.trace_add(
            "write", lambda *_: settings.set_many(ftp_sections=self.ftp_sections.get()))
        self._hint(body, 6, "Коли розділ забитий («недостатньо місця»), файл іде в наступний. "
                            "У корінь розділу програма не кладе нічого.")
        line = ctk.CTkFrame(body, fg_color="transparent")
        line.grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.btn_ftp_check = uikit.SecondaryButton(line, text="Перевірити вхід", width=160,
                                                   command=self._check_ftp)
        self.btn_ftp_check.pack(side="left")
        self.lbl_ftp = ctk.CTkLabel(line, text="чи пускає сервер з цим логіном і паролем",
                                    font=FONT_SMALL, text_color=uikit.TEXT_MUTED)
        self.lbl_ftp.pack(side="left", padx=(12, 0))

    def _build_ftp_base(self, body):
        """База розкладання — вшита в програму; оновлювати рідко й лише вручну."""
        self.lbl_base = ctk.CTkLabel(body, text="", font=FONT_UI, anchor="w", justify="left",
                                     wraplength=560)
        self.lbl_base.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._hint(body, 1, "Це теки на FTP і те, куди ви вже клали товари: за цим програма "
                            "обирає теку. База вшита в програму й оновлюється з кожним "
                            "релізом. Оновлювати вручну не обов'язково — лише якщо на FTP "
                            "з'явились нові теки або програма часто помиляється з текою. "
                            "Це довго (до години на розділ), іде у фоні й продовжиться з "
                            "того самого місця.")
        line = ctk.CTkFrame(body, fg_color="transparent")
        line.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.btn_history = uikit.SecondaryButton(line, text="Оновити базу", width=160,
                                                 command=self._toggle_history)
        self.btn_history.pack(side="left")
        self.lbl_history = ctk.CTkLabel(line, text="", font=FONT_SMALL, text_color=uikit.TEXT_MUTED)
        self.lbl_history.pack(side="left", padx=(12, 0))
        self._poll_history()

    def _hint(self, body, row, text, indent=False):
        ctk.CTkLabel(body, text=text, font=FONT_SMALL, text_color=uikit.TEXT_MUTED, anchor="w",
                     justify="left", wraplength=540).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=(30 if indent else 0, 0),
            pady=(0, 4))

    def _base_text(self):
        sizes = [f"{s} — {ftpstate.index_size(s)}" for s in self.master.ftp_sections()
                 if ftpstate.index_size(s)]
        age = ftpstate.tree_age()
        when = f" (станом на {time.strftime('%d.%m.%Y', time.localtime(age))})" if age else ""
        if not sizes:
            return "Бази розкладання ще немає — програма обиратиме теку лише за назвою."
        return f"Програма знає, куди ви клали товари: {', '.join(sizes)}{when}."

    def _toggle_history(self):
        app = self.master
        if app.history_running():
            app.stop_history()
        else:
            self._apply_ftp(persist=True)
            if not app.ftp_ready():
                self.lbl_history.configure(text="спершу вкажіть вхід на FTP вище",
                                           text_color=uikit.STATE_WARN)
                return
            app.start_history()
        self._poll_history()

    def _poll_history(self):
        if not self.winfo_exists():
            return
        app = self.master
        running = app.history_running()
        status = app.history.status if app.history else None
        self.lbl_base.configure(text=self._base_text())
        if status:
            color = uikit.STATE_ERROR if status.get("error") else \
                uikit.STATE_INFO if running else uikit.TEXT_MUTED
            self.lbl_history.configure(text=status["text"][:90], text_color=color)
        else:
            self.lbl_history.configure(text="")
        self.btn_history.configure(text="Зупинити оновлення" if running else "Оновити базу")
        if running:
            self.after(1000, self._poll_history)

    def _apply_ftp(self, persist):
        self.master.set_ftp_login(self.ftp_host.get(), self.ftp_user.get(), self.ftp_pass.get(),
                                  bool(self.ftp_remember.get()), persist=persist)

    def _check_ftp(self):
        """Лише вхід — чи пускає сервер; нічого на ньому не читає й не змінює."""
        self._apply_ftp(persist=True)
        if not self.master.ftp_ready():
            self.lbl_ftp.configure(text="вкажіть сервер, логін і пароль", text_color=uikit.STATE_WARN)
            return
        self.btn_ftp_check.configure(state="disabled", text="Перевіряю…")
        result = {}

        def work():
            try:
                client = self.master._ftp_connect()
                result["kind"] = client.kind
                client.close()
            except Exception as exc:
                applog.error("Перевірка входу FTP не вдалася", exc)
                result["error"] = uploader.human_error(exc)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self._wait_ftp(thread, result)

    def _wait_ftp(self, thread, result):
        if thread.is_alive():
            self.after(300, lambda: self._wait_ftp(thread, result))
            return
        if not self.winfo_exists():
            return
        self.btn_ftp_check.configure(state="normal", text="Перевірити вхід")
        if result.get("error"):
            self.lbl_ftp.configure(text=result["error"][:80], text_color=uikit.STATE_ERROR)
        else:
            self.lbl_ftp.configure(text=f"✓ сервер пускає ({result['kind']})",
                                   text_color=uikit.STATE_OK)

    def destroy(self):
        if hasattr(self, "ftp_host"):
            try:
                self._apply_ftp(persist=True)       # пароль зберігаємо раз, при закритті
            except Exception as exc:
                applog.error("Вхід FTP не збережено", exc)
        super().destroy()

    def _build_updates(self, body):
        row = self._check(body, 0, "Перевіряти оновлення при запуску програми",
                          "check_updates_on_start",
                          "Нова версія R-TubeUA (з GitHub) і свіжий yt-dlp (він лагодить "
                          "завантаження, коли YouTube щось змінює) завантажуються й "
                          "перевіряються у фоні; тоді тут і внизу вікна з'являється "
                          "«Оновити й перезапустити». Вручну — «Перевірити зараз».")
        line = ctk.CTkFrame(body, fg_color="transparent")
        line.grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.btn_check = uikit.SecondaryButton(line, text="Перевірити зараз", width=150,
                                               command=self._check_now)
        self.btn_check.pack(side="left")
        self.lbl_check = ctk.CTkLabel(line, text=self._ytdlp_text(), font=FONT_SMALL,
                                      text_color=uikit.TEXT_MUTED)
        self.lbl_check.pack(side="left", padx=(12, 0))
        # Готове оновлення — помітною кнопкою тут: унизу головного вікна колеги
        # її не бачили й думали, що програма не оновлюється.
        self.update_line = ctk.CTkFrame(body, fg_color="transparent")
        self.update_line.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.btn_restart = ctk.CTkButton(self.update_line, text="", width=240, height=34,
                                         font=FONT_UI_BOLD, fg_color=GREEN,
                                         hover_color=GREEN_HOVER, command=self._restart_now)
        self.btn_restart.grid(row=0, column=0, sticky="w")
        self.lbl_restart = ctk.CTkLabel(self.update_line, text="", font=FONT_SMALL,
                                        text_color=uikit.TEXT_MUTED)
        self.lbl_restart.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.update_line.grid_remove()
        self.refresh_updates()

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
            self.refresh_updates()
        else:
            self.lbl_check.configure(text=self._ytdlp_text() + " — найсвіжіший",
                                     text_color=uikit.STATE_OK)

    def refresh_updates(self):
        """Кнопка «Оновити й перезапустити» — лише коли є що застосувати."""
        app_version = appupdate.state["version"]
        ytdlp = ytupdate.state.get("pending")
        if app_version:
            text = f"Оновити до {app_version} і перезапустити"
        elif ytdlp:
            text = f"Перезапустити — застосувати yt-dlp {ytdlp}"
        else:
            self.update_line.grid_remove()
            return
        self.lbl_check.configure(text=self._ytdlp_text())
        self.btn_restart.configure(text=text)
        if settings.get("resume_queue"):
            self.lbl_restart.configure(text="Незавершені завантаження докачаються після перезапуску")
            self.lbl_restart.grid()
        else:
            self.lbl_restart.grid_remove()
        self.update_line.grid()

    def updates_pending(self):
        return bool(appupdate.state["version"] or ytupdate.state.get("pending"))

    def _restart_now(self):
        blocker = self.master.restart_blocker()
        if blocker:
            self.lbl_check.configure(text=blocker, text_color=uikit.STATE_WARN)
            return
        app = self.master
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        app.after_idle(app.restart)

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
        self.ftp_sections.set(settings.get("ftp_sections"))
        for key, (widget, options) in self._vars.items():
            value = settings.get(key)
            if options is not None:
                widget.set(_label_for(options, value))
            elif isinstance(widget, ctk.CTkSegmentedButton):
                widget.set(value)
            else:
                widget.set(bool(value))
