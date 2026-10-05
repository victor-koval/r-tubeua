"""Картка пакета: кілька посилань, плейлист або канал — одні налаштування на всіх.

Ролики тут не аналізуються наперед (на каналі їх сотні): кожен стає в чергу з
prefs, а якість і доріжку завантажувач визначає сам, коли до нього дійде
черга (downloader.apply_prefs) — за тими ж правилами, що й картка одного відео.

Усі ролики пакета видно таблицею: №, ID товару, ролик і примітка (копія,
вже в черзі, поза «Перші N»); ✕ прибирає рядок. Таблиця — ttk.Treeview: вона
малює й тисячі рядків миттєво, а рядок із CTk-віджетів коштує ~25 мс.
"""

from tkinter import ttk

import customtkinter as ctk

from . import downloader, formats, settings, tools, uikit
from .settings_dialog import AUDIO_OPTIONS, QUALITY_OPTIONS, _label_for, _value_for
from .uikit import FONT_SMALL, FONT_UI_BOLD, GREEN, GREEN_HOVER

AUDIO_ONLY_LABEL = "🎵  Лише звук (без відео)"
SUBS_OPTIONS = [("Без субтитрів", "none"), ("Українські від автора, якщо є", "author_uk")]
LIMITS = (10, 25, 50, 100)
TABLE_ROWS = 8          # найбільше; на низькому вікні менше (fit)
REMOVE = "✕"
COLUMNS = ("n", "pid", "video", "note", "x")


def describe(entries, busy=(), limit=None):
    """Рядки таблиці: [{n, pid, video, note, kind}, …].

    entries — [(посилання, назва, ID товару), …]; busy — {(посилання, ID)}, що вже
    чекають чи качаються; limit — скільки беремо («Перші N»), None — усі.
    kind: "" — звичайний; "copy" — той самий ролик для іншого товару (буде копія);
    "skip" — пропуститься (повтор рядка або вже в черзі); "over" — поза «Перші N».
    """
    rows, first_of_url, seen = [], {}, {}
    for i, (url, title, pid) in enumerate(entries):
        n = i + 1
        key = (url, pid or "")
        video = title if title and title != url else tools.short_url(url)
        note, kind = "", ""
        if limit is not None and i >= limit:
            note, kind = f"поза «Перші {limit}» — не качатиметься", "over"
        elif key in busy:
            note, kind = "уже в черзі — пропуститься", "skip"
        elif key in seen:
            note, kind = f"повтор рядка {seen[key]} — пропуститься", "skip"
        elif url in first_of_url:
            note, kind = f"той самий ролик, що в рядку {first_of_url[url]} — буде копія", "copy"
        seen.setdefault(key, n)
        first_of_url.setdefault(url, n)
        rows.append({"n": n, "pid": pid or "", "video": video, "note": note, "kind": kind})
    return rows


class BatchCard(uikit.Card):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.entries = []
        self.title_suffix = ""
        self._audio_only = None
        self.max_rows = TABLE_ROWS
        self.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self, text="", font=uikit.FONT_VIDEO_TITLE, anchor="w",
                                      justify="left")
        self.lbl_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        uikit.wrap_to_width(self.lbl_title)
        self.lbl_summary = ctk.CTkLabel(self, text="", font=FONT_SMALL, anchor="w",
                                        text_color=uikit.TEXT_MUTED)
        self.lbl_summary.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        table = ctk.CTkFrame(self, fg_color="transparent")
        table.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        table.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=COLUMNS, show="headings", height=TABLE_ROWS,
                                 selectmode="extended", style="Batch.Treeview")
        for col, text, width, stretch, anchor in (
                ("n", "№", 44, False, "e"), ("pid", "ID товару", 110, False, "w"),
                ("video", "Ролик", 260, True, "w"), ("note", "Примітка", 260, True, "w"),
                ("x", "", 34, False, "center")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, minwidth=width if not stretch else 120,
                             stretch=stretch, anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew")
        # height: інакше CTkScrollbar сам вимагає 200 px і розпирає таблицю.
        self.scroll = ctk.CTkScrollbar(table, command=self.tree.yview, height=40)
        self.scroll.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.tree.configure(yscrollcommand=self.scroll.set)
        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<Delete>", lambda e: self._remove_selected())

        # Параметри — у дві колонки, а тека — під полем посилання: інакше на
        # вікні 880×660 кнопка «Завантажити» опинялась за нижнім краєм.
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=3, column=0, sticky="ew", padx=16)
        opts.grid_columnconfigure(3, weight=1)

        def label(text, row, column):
            ctk.CTkLabel(opts, text=text, font=FONT_UI_BOLD, anchor="w", width=110).grid(
                row=row, column=column, sticky="w", pady=3, padx=(0 if column == 0 else 24, 0))

        label("Якість", 0, 0)
        self.quality_labels = [lab for lab, _ in QUALITY_OPTIONS] + [AUDIO_ONLY_LABEL]
        self.opt_quality = ctk.CTkOptionMenu(opts, values=self.quality_labels, width=260,
                                             dynamic_resizing=False,
                                             command=lambda _: self._sync())
        self.opt_quality.grid(row=0, column=1, sticky="w", pady=3)

        label("Субтитри", 0, 2)
        self.opt_subs = ctk.CTkOptionMenu(opts, values=[o[0] for o in SUBS_OPTIONS], width=260,
                                          dynamic_resizing=False)
        self.opt_subs.grid(row=0, column=3, sticky="w", pady=3)

        label("Доріжка", 1, 0)
        self.opt_audio = ctk.CTkOptionMenu(opts, values=[o[0] for o in AUDIO_OPTIONS], width=260,
                                           dynamic_resizing=False)
        self.opt_audio.grid(row=1, column=1, sticky="w", pady=3)

        label("Формат", 1, 2)
        self.container = ctk.CTkSegmentedButton(opts, values=["mp4", "mkv"], selected_color=GREEN,
                                                selected_hover_color=GREEN_HOVER)
        self.container.grid(row=1, column=3, sticky="w", pady=3)

        label("Скільки взяти", 2, 0)
        self.opt_count = ctk.CTkOptionMenu(opts, values=["—"], width=260, dynamic_resizing=False,
                                           command=lambda _: self._render())
        self.opt_count.grid(row=2, column=1, sticky="w", pady=3)
        self.keep_original_var = ctk.BooleanVar()
        self.chk_original = ctk.CTkCheckBox(opts, text="+ оригінал другою доріжкою",
                                            variable=self.keep_original_var, font=FONT_SMALL)
        self.chk_original.grid(row=2, column=2, columnspan=2, sticky="w", padx=(24, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.btn_download = ctk.CTkButton(buttons, text="", height=44, font=uikit.FONT_BIG_BUTTON,
                                          command=app.download_batch)
        self.btn_download.grid(row=0, column=0, sticky="ew")
        uikit.SecondaryButton(buttons, text="Скасувати", width=110, height=44,
                              command=app.close_batch).grid(row=0, column=1, padx=(8, 0))

    # ── таблиця ──
    def fit(self, rows):
        """Скільки рядків таблиці показувати — щоб картка влізла у вікно."""
        self.max_rows = max(3, min(TABLE_ROWS, rows))
        if self.entries:
            self._fit_table()

    def _fit_table(self):
        count = len(self.tree.get_children())
        self.tree.configure(height=max(1, min(self.max_rows, count)))
        if count > self.max_rows:
            self.scroll.grid()
        else:
            self.scroll.grid_remove()

    def apply_style(self):
        """Кольори таблиці під поточну тему (ttk теми CustomTkinter не бачить)."""
        i = 1 if uikit.is_dark() else 0
        style = ttk.Style(self)
        if style.theme_use() != "clam":
            style.theme_use("clam")     # лише в clam Treeview слухається кольорів
        bg, fg = uikit.SURFACE_RAISED[i], uikit.TEXT[i]
        # У clam рамку малюють три кольори — усі під тло, щоб не було світлої обвідки.
        style.configure("Batch.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0, bordercolor=bg, lightcolor=bg,
                        darkcolor=bg, font=(uikit.FONT_FAMILY, 10))
        style.configure("Batch.Treeview.Heading", background=uikit.SURFACE[i],
                        foreground=uikit.TEXT_MUTED[i], relief="flat", borderwidth=0,
                        bordercolor=uikit.SURFACE[i], lightcolor=uikit.SURFACE[i],
                        darkcolor=uikit.SURFACE[i], font=(uikit.FONT_FAMILY, 10, "bold"))
        style.map("Batch.Treeview.Heading", background=[("active", uikit.SURFACE[i])])
        style.map("Batch.Treeview", background=[("selected", uikit.NEUTRAL_HOVER[i])],
                  foreground=[("selected", fg)])
        self.tree.tag_configure("copy", foreground=uikit.STATE_INFO[i])
        self.tree.tag_configure("skip", foreground=uikit.STATE_WARN[i])
        self.tree.tag_configure("over", foreground=uikit.TEXT_MUTED[i])

    def _render(self):
        """Таблиця, заголовок і кнопка — з поточних entries і «Скільки взяти»."""
        busy = {(j.url, j.product_id) for j in self.app.jobs_panel.active_jobs()}
        rows = describe(self.entries, busy, self._limit())
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", iid=str(row["n"] - 1), tags=(row["kind"],),
                             values=(row["n"], row["pid"], row["video"], row["note"], REMOVE))
        has_ids = any(r["pid"] for r in rows)
        self.tree.configure(displaycolumns=COLUMNS if has_ids else ("n", "video", "note", "x"))
        self._fit_table()

        n = len(self.entries)
        self.lbl_title.configure(text=f"Пакет: {n} відео" +
                                 (f" — {self.title_suffix}" if self.title_suffix else ""))
        copies = sum(1 for r in rows if r["kind"] == "copy")
        skipped = sum(1 for r in rows if r["kind"] == "skip")
        with_ids = sum(1 for r in rows if r["pid"])
        parts = []
        if with_ids and with_ids < n:
            parts.append(f"з ID — {with_ids}, решта назвуться латиницею")
        if copies:
            parts.append(f"копій того самого ролика: {copies} (качається раз)")
        if skipped:
            parts.append(f"пропуститься: {skipped}")
        parts.append("✕ або Delete — прибрати рядок")
        self.lbl_summary.configure(text="  ·  ".join(parts))
        self._sync()

    def _on_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        column = self.tree.identify_column(event.x)          # «#N» серед показаних
        shown = self.tree.cget("displaycolumns")
        if isinstance(shown, str):
            shown = self.tree.tk.splitlist(shown)
        index = int(column[1:]) - 1
        if 0 <= index < len(shown) and shown[index] == "x":
            item = self.tree.identify_row(event.y)
            if item:
                self._remove([item])
                return "break"
        return None

    def _remove_selected(self):
        self._remove(self.tree.selection())

    def _remove(self, items):
        for i in sorted({int(i) for i in items}, reverse=True):
            if 0 <= i < len(self.entries):
                del self.entries[i]
        if not self.entries:
            self.app.close_batch()
            return
        self._refresh_counts()
        self._render()

    # ── показ ──
    def show(self, title, entries):
        """entries — [(посилання, назва), …] або [(посилання, назва, ID товару), …]."""
        self.entries = [(e[0], e[1], e[2] if len(e) > 2 else "") for e in entries]
        if not title and any(e[2] for e in self.entries):
            title = "з ID товарів"
        self.title_suffix = title
        self.opt_count.set("")
        self._refresh_counts()

        # Початкові значення — з налаштувань, щоразу заново.
        self.opt_quality.set(_label_for(QUALITY_OPTIONS, settings.get("max_height"), default=3))
        self.opt_audio.set(_label_for(AUDIO_OPTIONS, settings.get("preferred_audio")))
        self.opt_subs.set(SUBS_OPTIONS[1][0])
        self.keep_original_var.set(bool(settings.get("keep_original")))
        self._audio_only = None
        self.apply_style()
        self._render()

    def _refresh_counts(self):
        """«Скільки взяти» під поточну кількість; обране «Перші N» лишається, якщо ще можливе."""
        n = len(self.entries)
        current = self.opt_count.get()
        counts = [f"Усі ({n})"] + [f"Перші {k}" for k in LIMITS if k < n]
        self.opt_count.configure(values=counts)
        self.opt_count.set(current if current in counts[1:] else counts[0])

    def _is_audio_only(self):
        return self.opt_quality.get() == AUDIO_ONLY_LABEL

    def _limit(self):
        value = self.opt_count.get()
        if value.startswith("Перші "):
            return min(int(value.split()[1]), len(self.entries))
        return None

    def _count(self):
        limit = self._limit()
        return len(self.entries) if limit is None else limit

    def _sync(self):
        audio_only = self._is_audio_only()
        if audio_only != self._audio_only:
            # Набір форматів міняється разом із режимом — і значення беремо з налаштувань.
            self._audio_only = audio_only
            values = ["m4a", "mp3"] if audio_only else ["mp4", "mkv"]
            self.container.configure(values=values)
            saved = settings.get("audio_container" if audio_only else "container")
            self.container.set(saved if saved in values else values[0])
        state = "disabled" if audio_only else "normal"
        self.chk_original.configure(state=state)
        self.opt_subs.configure(state=state)
        self.btn_download.configure(text=f"⬇  Завантажити {self._count()} відео")

    def build_jobs(self, out_dir):
        audio_only = self._is_audio_only()
        limit = formats.AUDIO_ONLY if audio_only else \
            _value_for(QUALITY_OPTIONS, self.opt_quality.get())
        prefs = {
            "max_height": limit,
            "audio": _value_for(AUDIO_OPTIONS, self.opt_audio.get()),
            "subs": "none" if audio_only else _value_for(SUBS_OPTIONS, self.opt_subs.get()),
        }
        return [downloader.Job(url=url, title=title, out_dir=out_dir,
                               container=self.container.get(),
                               keep_original=bool(self.keep_original_var.get()) and not audio_only,
                               sub_key=None, subs_mode=settings.get("subs_mode"),
                               product_id=product_id or "", prefs=dict(prefs))
                for url, title, product_id in self.entries[:self._count()]]
