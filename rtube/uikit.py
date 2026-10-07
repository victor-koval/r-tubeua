"""Спільний стиль, палітра, масштабування та доступ до ресурсів.

Палітра та сама, що в Інспекторі Гаджеті й Size Charts: значення з
CSS-змінних rozetka.com.ua (--global-black, --global-green тощо), щоб
програми виглядали як одна родина.
"""

import os
import sys

import customtkinter as ctk

# ── палітра rozetka.com.ua ───────────────────────────────────────────────
BLACK = "#221f1f"
BLACK_60 = "#797878"
BLACK_40 = "#a6a5a5"
BLACK_20 = "#d2d2d2"
BLACK_10 = "#e9e9e9"
BLACK_5 = "#f5f5f5"

GREEN = "#00a046"
GREEN_80 = "#44b26f"
GREEN_60 = "#73c592"

BLUE = "#3e77aa"
RED = "#f84147"
YELLOW = "#ffa900"

# ── семантичні пари (світла, темна) ──────────────────────────────────────
SURFACE = ("#ffffff", "#2a2727")
SURFACE_SUNKEN = (BLACK_10, "#1c1a1a")
SURFACE_RAISED = (BLACK_5, "#333030")
BORDER = (BLACK_20, "#3d3939")
TEXT = (BLACK, "#ececec")
TEXT_MUTED = (BLACK_60, BLACK_40)

GREEN_HOVER = (GREEN_80, GREEN_80)
DANGER = (RED, "#8b2c2c")
DANGER_HOVER = ("#e0353b", "#6f2222")
NEUTRAL = (BLACK_10, "#3d3939")
NEUTRAL_HOVER = (BLACK_20, "#4a4545")

HEADER_BG = BLACK          # шапка темна в обох темах — як на rozetka.com.ua
HEADER_TEXT = "#d8d5d5"
HEADER_HOVER = "#332f2f"

STATE_OK = ("#0b8a3e", "#74c69d")
STATE_WARN = ("#b06a00", "#ffd166")
STATE_ERROR = ("#c62c31", "#ff6b6b")
STATE_INFO = (BLUE, "#89b4fa")

# Тло рядків однієї групи «те саме відео в кількох товарів» у таблиці пакета:
# там рядки групи розкидані по списку, і колір їх пов'язує. 8 кольорів
# (світла тема, темна); сусідні групи завжди різні — див. batch.describe.
GROUP_COLORS = (("#c6ead2", "#2f5b3e"), ("#cfe0f7", "#2e4a6e"),
                ("#fae0ad", "#6a5224"), ("#efcdea", "#5c3559"),
                ("#c4ebe8", "#225a57"), ("#f7d0d0", "#6b2f35"),
                ("#e2ecbe", "#4d5a24"), ("#d9d4f5", "#3e3a6e"))

# ── типографіка ──────────────────────────────────────────────────────────
FONT_FAMILY = "Segoe UI"
FONT_UI = (FONT_FAMILY, 12)
FONT_UI_BOLD = (FONT_FAMILY, 12, "bold")
FONT_SMALL = (FONT_FAMILY, 11)
FONT_TITLE = (FONT_FAMILY, 14, "bold")
FONT_VIDEO_TITLE = (FONT_FAMILY, 15, "bold")
FONT_BRAND = (FONT_FAMILY, 19, "bold")
FONT_BIG_BUTTON = (FONT_FAMILY, 14, "bold")

RADIUS = 8


class SecondaryButton(ctk.CTkButton):
    """Допоміжна дія: нейтральна, без зеленої заливки.

    Зеленою лишається тільки головна дія («Аналізувати», «Завантажити»),
    інакше її не знайти очима серед решти кнопок.
    """

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", NEUTRAL)
        kwargs.setdefault("hover_color", NEUTRAL_HOVER)
        kwargs.setdefault("text_color", TEXT)
        kwargs.setdefault("height", 32)
        super().__init__(master, **kwargs)


class Card(ctk.CTkFrame):
    """Біла картка із заголовком — основний будівельний блок вікна."""

    def __init__(self, master, title=None, **kwargs):
        kwargs.setdefault("fg_color", SURFACE)
        kwargs.setdefault("corner_radius", RADIUS + 2)
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.body_row = 0
        if title:
            ctk.CTkLabel(self, text=title, font=FONT_TITLE, anchor="w").grid(
                row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
            self.body_row = 1


def resource_path(relative):
    """Шлях до ресурсу і в звичайному запуску, і всередині onefile-збірки."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def apply_theme():
    try:
        ctk.set_default_color_theme(resource_path(os.path.join("assets", "rozetka_theme.json")))
        return True
    except Exception:
        ctk.set_default_color_theme("green")
        return False


def apply_window_icon(window):
    try:
        window.iconbitmap(resource_path(os.path.join("assets", "logo.ico")))
    except Exception:
        pass


def is_dark(appearance=None):
    mode = appearance or ctk.get_appearance_mode()
    return str(mode).lower().startswith("dark")


def open_path(path):
    """Відкриває теку (або теку з файлом) у Провіднику."""
    try:
        target = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        os.startfile(target)
        return True
    except Exception:
        return False


def select_in_explorer(path):
    """Відкриває Провідник із виділеним файлом — одразу видно, що з'явилося."""
    import subprocess
    try:
        if path and os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return True
    except Exception:
        pass
    return open_path(path)


def format_duration(seconds):
    """«1:02:03» або «4:05» — як під відео на YouTube."""
    seconds = int(max(0, seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def whole_seconds(seconds):
    """Тривалість у цілих секундах — так, як її видно в «3,27». Суми складаємо
    з цих цілих, а не з точних значень: інакше «Разом» (1,39) не збігалося б
    із сумою того, що видно в рядках (1,40)."""
    return int(round(max(0, seconds or 0)))


def format_min_sec(seconds):
    """207 → «3,27»: хвилини, кома, секунди двома цифрами. Годин не виділяємо
    (75 хв — «75,03»): такий формат просили для звітів."""
    seconds = whole_seconds(seconds)
    minutes, secs = divmod(seconds, 60)
    return f"{minutes},{secs:02d}"


class CopyLabel(ctk.CTkLabel):
    """Підпис, що копіює своє значення в буфер обміну по кліку.

    Показує одне («⏱ 3,27»), копіює інше («3,27») і на мить пише
    «Скопійовано ✓», щоб було видно, що клік спрацював.
    """

    COPIED = "Скопійовано ✓"

    def __init__(self, master, **kwargs):
        kwargs.setdefault("cursor", "hand2")
        kwargs.setdefault("text", "")
        super().__init__(master, **kwargs)
        self.value = ""
        # Не «_text»: так CTkLabel зве власне поле з показаним текстом.
        self._shown = kwargs["text"]
        self.bind("<Button-1>", self._copy)

    def set_value(self, value, text):
        if value == self.value and text == self._shown:
            return      # викликається з опитування 10 разів на секунду — не перемальовуємо дарма
        self.value = value
        self._shown = text
        if self.cget("text") != self.COPIED:
            self.configure(text=text)

    def _copy(self, _event=None):
        if not self.value:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self.value)
        except Exception:
            return
        self.configure(text=self.COPIED)
        self.after(1200, self._restore)

    def _restore(self):
        try:
            if self.winfo_exists():
                self.configure(text=self._shown)
        except Exception:
            pass


def format_eta(seconds):
    if seconds is None:
        return ""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} хв {secs:02d} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} год {minutes:02d} хв"


# ── гарячі клавіші незалежно від розкладки ──────────────────────────────
# Tk розпізнає Ctrl+V за символом клавіші, а на українській розкладці це
# «м», тож вставка мовчки не працює. Віртуальні коди клавіш Windows від
# розкладки не залежать.
KEY_A, KEY_C, KEY_V, KEY_X = 65, 67, 86, 88


def wrap_to_width(label, margin=4, minimum=200):
    """Переносить текст CTkLabel за шириною, яку йому дала сітка.

    Не через label.bind: CTkLabel передає його й внутрішньому tk.Label, а
    ширина того залежить від уже перенесеного тексту. wraplength тоді щоразу
    зменшувався, довга назва перескакувала з двох рядків на три й назад, і
    вікно зависало в нескінченному перерахунку розмітки (ролик bD_nlDO09f4).
    Зовнішня рамка мітки має ширину клітинки сітки — від тексту вона не залежить.
    """
    import tkinter as tk

    def on_configure(event):
        try:
            width = label._reverse_widget_scaling(event.width)
        except Exception:
            width = event.width
        width = max(minimum, int(width) - margin)
        if label.cget("wraplength") != width:
            label.configure(wraplength=width)

    tk.Misc.bind(label, "<Configure>", on_configure, "+")


def bind_text_hotkeys(widget, on_paste=None):
    """Ctrl+C/V/X/A у CTkEntry незалежно від мовної розкладки.

    on_paste — що зробити після вставки (наприклад, одразу аналізувати).
    """
    import tkinter as tk
    target = getattr(widget, "_entry", widget)

    def selected():
        try:
            return target.selection_present()
        except Exception:
            return False

    def on_key(event):
        if not event.state & 0x4:
            return None
        code, keysym = event.keycode, (event.keysym or "").lower()
        if code == KEY_V or keysym == "v":
            try:
                text = target.clipboard_get()
            except tk.TclError:
                return "break"
            if selected():
                target.delete(tk.SEL_FIRST, tk.SEL_LAST)
            target.insert(tk.INSERT, text.strip())
            if on_paste:
                target.after(10, on_paste)
            return "break"
        if code in (KEY_C, KEY_X) or keysym in ("c", "x"):
            if selected():
                text = target.get()[target.index(tk.SEL_FIRST):target.index(tk.SEL_LAST)]
                target.clipboard_clear()
                target.clipboard_append(text)
                if code == KEY_X or keysym == "x":
                    target.delete(tk.SEL_FIRST, tk.SEL_LAST)
            return "break"
        if code == KEY_A or keysym == "a":
            target.selection_range(0, tk.END)
            target.icursor(tk.END)
            return "break"
        return None

    target.bind("<Control-KeyPress>", on_key, add="+")


# ── масштаб під малі екрани ─────────────────────────────────────────────
COMFORT_SIZE = (1000, 800)
MIN_SCALE = 0.8


def work_rect():
    """Робоча область основного екрана в справжніх пікселях, без панелі задач:
    (ліво, верх, ширина, висота); нулі — не вдалося дізнатися."""
    try:
        import ctypes
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    return 0, 0, 0, 0


def work_area():
    """Розмір робочої області екрана в справжніх пікселях, без панелі задач."""
    return work_rect()[2:]


def fit_scaling(dpi_scale):
    """customtkinter множить свій масштаб на DPI Windows: на ноутбуці
    1366x768 зі 125% вікно просто не влізло б. Зменшуємо рівно настільки,
    щоб влізло, і не дрібніше за MIN_SCALE."""
    width, height = work_area()
    if not width or not height or dpi_scale <= 0:
        return 1.0
    fit = min(width / (COMFORT_SIZE[0] * dpi_scale), height / (COMFORT_SIZE[1] * dpi_scale), 1.0)
    return 1.0 if fit >= 1.0 else max(MIN_SCALE, round(fit, 2))
