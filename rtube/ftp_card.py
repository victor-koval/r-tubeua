"""Картка «Заливання на FTP»: план (куди піде кожен файл), правки й заливання.

Тека визначається сама (uploader → ftpcat): ваш вибір → як раніше → за
назвою. Рядки, де теку не визначено, підсвічено; подвійний клік — обрати
теку. Обрана вручну тека запам'ятовується для категорії товару. Якщо на FTP
під тим самим ім'ям інше відео товару, файл піде як ID_2.mp4, ID_3.mp4…
"""

import tkinter
from tkinter import messagebox, ttk

import customtkinter as ctk

from . import ftpcat, ftpstate, uikit, uploader
from .batch import plural
from .uikit import FONT_SMALL, FONT_UI, FONT_UI_BOLD, GREEN, GREEN_HOVER

COLUMNS = ("n", "pid", "file", "folder", "source", "state")
TABLE_ROWS = 10
STATE_TEXT = {uploader.PLANNING: "визначаю теку…", uploader.PLANNED: "готово до заливання",
              uploader.NEED_CHOICE: "оберіть теку",
              uploader.ALREADY: "уже на FTP", uploader.QUEUED: "у черзі",
              uploader.UPLOADING: "заливаю…", uploader.UPLOADED: "залито",
              uploader.ERROR: "помилка", uploader.CANCELLED: "скасовано"}
TAGS = {uploader.NEED_CHOICE: "warn", uploader.ERROR: "error",
        uploader.UPLOADED: "ok", uploader.ALREADY: "muted", uploader.CANCELLED: "muted",
        uploader.UPLOADING: "info", uploader.QUEUED: "info"}
EDITABLE = (uploader.PLANNED, uploader.NEED_CHOICE, uploader.ERROR, uploader.CANCELLED)


class FtpCard(uikit.Card):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.tasks = []
        self.max_rows = TABLE_ROWS
        self.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self, text="Заливання на FTP", font=uikit.FONT_VIDEO_TITLE,
                                      anchor="w")
        self.lbl_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        self.lbl_summary = ctk.CTkLabel(self, text="", font=FONT_SMALL, anchor="w",
                                        justify="left", text_color=uikit.TEXT_MUTED)
        self.lbl_summary.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        uikit.wrap_to_width(self.lbl_summary)

        table = ctk.CTkFrame(self, fg_color="transparent")
        table.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        table.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=COLUMNS, show="headings", height=TABLE_ROWS,
                                 selectmode="browse", style="Batch.Treeview")
        for col, text, width, stretch, anchor in (
                ("n", "№", 40, False, "e"), ("pid", "ID товару", 100, False, "w"),
                ("file", "Файл", 130, False, "w"), ("folder", "Тека на FTP", 330, True, "w"),
                ("source", "Звідки", 90, False, "w"), ("state", "Стан", 170, False, "w")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, minwidth=width if not stretch else 160,
                             stretch=stretch, anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scroll = ctk.CTkScrollbar(table, command=self.tree.yview, height=40)
        self.scroll.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.tree.configure(yscrollcommand=self.scroll.set)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", self._on_double)

        # Загальний прогрес заливання — під таблицею, поки щось заливається.
        self.progress = ctk.CTkFrame(self, fg_color="transparent")
        self.progress.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.progress.grid_columnconfigure(0, weight=1)
        self.bar = ctk.CTkProgressBar(self.progress, height=10, progress_color=GREEN)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.bar.set(0)
        self.lbl_progress = ctk.CTkLabel(self.progress, text="", font=FONT_SMALL, anchor="w",
                                         text_color=uikit.STATE_INFO)
        self.lbl_progress.grid(row=1, column=0, sticky="ew")
        self.progress.grid_remove()

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.btn_upload = ctk.CTkButton(buttons, text="", height=44, font=uikit.FONT_BIG_BUTTON,
                                        command=self.upload)
        self.btn_upload.grid(row=0, column=0, sticky="ew")
        uikit.SecondaryButton(buttons, text="Закрити", width=100, height=44,
                              command=app.close_ftp).grid(row=0, column=1, padx=(8, 0))

    # ── вигляд ──
    def apply_style(self):
        self.app.batch_card.apply_style()       # спільний стиль Batch.Treeview
        i = 1 if uikit.is_dark() else 0
        self.tree.tag_configure("warn", foreground=uikit.STATE_WARN[i])
        self.tree.tag_configure("error", foreground=uikit.STATE_ERROR[i])
        self.tree.tag_configure("ok", foreground=uikit.STATE_OK[i])
        self.tree.tag_configure("info", foreground=uikit.STATE_INFO[i])
        self.tree.tag_configure("muted", foreground=uikit.TEXT_MUTED[i])

    def fit(self, rows):
        self.max_rows = max(3, min(TABLE_ROWS, rows))
        self._fit_table()

    def _fit_table(self):
        count = len(self.tree.get_children())
        self.tree.configure(height=max(1, min(self.max_rows, count)))
        if count > self.max_rows:
            self.scroll.grid()
        else:
            self.scroll.grid_remove()

    # ── дані ──
    def show(self, tasks):
        self.tasks = list(tasks)
        self.apply_style()
        if self.app.upload_progress() is None:
            self.progress.grid_remove()     # підсумок минулого заливання — не до нового плану
        self.tree.delete(*self.tree.get_children())
        for n, task in enumerate(self.tasks, 1):
            self.tree.insert("", "end", iid=str(task.id), values=self._values(n, task),
                             tags=(TAGS.get(task.state, ""),))
        self._fit_table()
        self._refresh()

    def _values(self, n, task):
        folder = task.folder or ("" if task.state == uploader.PLANNING else "—")
        state = STATE_TEXT.get(task.state, task.state)
        if task.state == uploader.UPLOADING and task.fraction:
            state = f"заливаю… {task.fraction * 100:.0f}%"
        # Причина — у широкій колонці теки: у вузькій «Стан» вона обрізалась.
        if task.note and (task.state in (uploader.ERROR, uploader.NEED_CHOICE) or
                          task.state == uploader.PLANNED and task.renamed):
            folder = f"— {task.note}" if not task.folder else f"{task.folder} — {task.note}"
        source = ftpcat.SOURCE_LABELS.get(task.source, "—") if task.path else "—"
        return (n, task.product_id, task.name, folder, source, state)

    def update_task(self, task):
        iid = str(task.id)
        if not self.tree.exists(iid):
            return
        n = self.tree.index(iid) + 1
        self.tree.item(iid, values=self._values(n, task), tags=(TAGS.get(task.state, ""),))
        self._refresh()

    def ready(self):
        """Що заливати кнопкою: визначені й невдалі з текою — повторне
        натискання «Залити» доливає те, що обірвалось."""
        return [t for t in self.tasks if uploader.is_video(t.local) and (
                t.state in uploader.READY or
                (t.state in (uploader.ERROR, uploader.CANCELLED) and t.path))]

    def _refresh(self):
        count = lambda *states: sum(1 for t in self.tasks if t.state in states)
        ready = len(self.ready())
        n = len(self.tasks)
        self.lbl_title.configure(text=f"Заливання на FTP: {n} {plural(n, 'файл', 'файли', 'файлів')}")
        parts = []
        section = self.app.uploads_section()
        if section:
            parts.append(f"розділ {section}")
        full = sorted(self.app.uploads.full_sections())
        if full:
            parts.append("забиті: " + ", ".join(full))
        for states, label in (((uploader.PLANNING,), "визначаю"),
                              ((uploader.NEED_CHOICE,), "оберіть теку"),
                              ((uploader.QUEUED, uploader.UPLOADING), "заливається"),
                              ((uploader.UPLOADED,), "залито"),
                              ((uploader.ALREADY,), "уже на FTP"),
                              ((uploader.ERROR,), "не залито — «Залити» ще раз")):
            c = count(*states)
            if c:
                parts.append(f"{label}: {c}")
        renamed = sum(1 for t in self.tasks if t.renamed and t.state not in
                      (uploader.ALREADY, uploader.ERROR, uploader.CANCELLED))
        if renamed:
            parts.append(f"під новим ім'ям (_2, _3…): {renamed}")
        parts.append("подвійний клік — обрати теку")
        self.lbl_summary.configure(text="  ·  ".join(parts))
        busy = count(uploader.QUEUED, uploader.UPLOADING)
        self._show_progress(busy)
        if ready:
            self.btn_upload.configure(text=f"↑  Залити на FTP ({ready})", state="normal")
        elif busy:
            self.btn_upload.configure(text=f"Заливається: {busy}…", state="disabled")
        else:
            self.btn_upload.configure(text="↑  Залити на FTP", state="disabled")

    def _show_progress(self, busy):
        """Смужка — поки щось заливається, і ще трохи після: «Залито 10 з 10»."""
        progress = uploader.batch_progress(self.app.upload_batch)
        if progress is None or (not busy and not self.progress.winfo_manager()):
            if self.progress.winfo_manager() and progress is None:
                self.progress.grid_remove()
            return
        fraction = progress[5]
        self.bar.set(fraction if fraction is not None else 0)
        self.lbl_progress.configure(text=uploader.describe_batch(progress),
                                    text_color=uikit.STATE_INFO if busy else uikit.STATE_OK)
        if not self.progress.winfo_manager():
            self.progress.grid()
            self.app.after_idle(self.app._fit_cards)

    # ── дії ──
    def upload(self):
        tasks = self.ready()
        if tasks:
            self.app.start_upload(tasks)
            self._refresh()

    def _task_at(self, event):
        iid = self.tree.focus() if event.type == tkinter.EventType.KeyPress else \
            self.tree.identify_row(event.y)
        return next((t for t in self.tasks if str(t.id) == iid), None)

    def _on_double(self, event):
        task = self._task_at(event)
        if task is None or task.state not in EDITABLE:
            return "break"
        FolderPicker(self, task, self.app.ftp_sections(), self._chosen,
                     refresh=self.app.refresh_ftp_tree)
        return "break"

    def _chosen(self, task, section, path, remember):
        try:
            self.app.uploads.choose(task, section, path, remember=remember)
        except ValueError as exc:
            messagebox.showwarning("R-TubeUA", str(exc), parent=self)
            return
        self.update_task(task)


class FolderPicker(ctk.CTkToplevel):
    """Вибір теки в дереві розділу. Корінь розділу вибрати не можна."""

    def __init__(self, master, task, sections, on_choose, refresh=None):
        """refresh(розділи, по_завершенні(помилка)) — перечитати теки з FTP."""
        super().__init__(master)
        self.task, self.on_choose, self.refresh = task, on_choose, refresh
        self.title(f"Тека для {task.product_id or task.name}")
        self.configure(fg_color=uikit.SURFACE_SUNKEN)
        self.transient(master.winfo_toplevel())
        self.geometry("620x560")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self, text=task.site or "Категорія товару на сайті невідома", font=FONT_SMALL,
                     text_color=uikit.TEXT_MUTED, anchor="w", justify="left", wraplength=580).grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", padx=16)
        top.grid_columnconfigure(1, weight=1)
        known = [s for s in sections if ftpstate.tree(s) is not None] or list(sections)
        self.section = ctk.StringVar(value=task.section if task.section in known else known[0])
        ctk.CTkOptionMenu(top, values=known, variable=self.section, width=120,
                          command=lambda _: self._fill()).grid(row=0, column=0)
        # Без textvariable: з нею CTkEntry не показує підказку «Пошук теки…».
        entry = self.entry = ctk.CTkEntry(top, placeholder_text="Пошук теки…", font=FONT_UI)
        entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        entry.bind("<KeyRelease>", lambda e: self._fill())
        entry.bind("<<Paste>>", lambda e: self.after(10, self._fill), add="+")

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        if hasattr(master, "apply_style"):
            master.apply_style()            # темна чи світла тема для Batch.Treeview
        self.tree = ttk.Treeview(frame, show="tree", selectmode="browse", style="Batch.Treeview")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda e: self._ok())

        hint = ctk.CTkFrame(self, fg_color="transparent")
        hint.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.lbl_refresh = ctk.CTkLabel(hint, text="Немає потрібної теки? Можливо, її створили "
                                                   "нещодавно.", font=FONT_SMALL,
                                        text_color=uikit.TEXT_MUTED)
        self.lbl_refresh.pack(side="left")
        self.btn_refresh = uikit.SecondaryButton(hint, text="Оновити список тек", width=150,
                                                 height=26, font=FONT_SMALL,
                                                 command=self._refresh_tree)
        if refresh:
            self.btn_refresh.pack(side="left", padx=(8, 0))
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 14))
        bottom.grid_columnconfigure(0, weight=1)
        self.remember = ctk.BooleanVar(value=bool(task.mpath))
        ctk.CTkCheckBox(bottom, text="Запам'ятати: відео товарів цієї категорії — сюди",
                        variable=self.remember, font=FONT_SMALL,
                        state="normal" if task.mpath else "disabled").grid(row=0, column=0,
                                                                          sticky="w")
        ctk.CTkButton(bottom, text="Обрати", width=100, font=FONT_UI_BOLD, fg_color=GREEN,
                      hover_color=GREEN_HOVER, command=self._ok).grid(row=0, column=1, padx=(8, 0))
        uikit.SecondaryButton(bottom, text="Скасувати", width=100,
                              command=self.destroy).grid(row=0, column=2, padx=(8, 0))
        self._fill()
        self.after(50, lambda: (self.grab_set(), entry.focus_set()))

    def _fill(self):
        """Дерево розділу; з пошуком — лише теки, що містять запит, і їхні батьки."""
        self.tree.delete(*self.tree.get_children())
        tree = ftpstate.tree(self.section.get()) or {(): []}
        query = self.entry.get().strip().lower()
        wanted = None
        if query:
            needles = {query, ftpcat.translit(query)}
            wanted = set()
            for path in tree:
                if path and any(n in path[-1].lower() for n in needles):
                    wanted |= {path[:i] for i in range(1, len(path) + 1)}

        def add(parent_iid, path):
            for name in sorted(tree.get(path, [])):
                child = path + (name,)
                if not ftpcat.usable(name) or (wanted is not None and child not in wanted):
                    continue
                iid = "\x1f".join(child)
                self.tree.insert(parent_iid, "end", iid=iid, text=name,
                                 open=wanted is not None or child == tuple(self.task.path[:len(child)]))
                add(iid, child)

        add("", ())
        current = "\x1f".join(self.task.path)
        if self.task.section == self.section.get() and current and self.tree.exists(current):
            self.tree.selection_set(current)
            self.tree.see(current)

    def _refresh_tree(self):
        """Перечитати теки лише цього розділу з FTP (кілька секунд)."""
        self.btn_refresh.configure(state="disabled", text="Читаю…")

        def done(error):
            if not self.winfo_exists():
                return
            self.btn_refresh.configure(state="normal", text="Оновити список тек")
            if error:
                self.lbl_refresh.configure(text=f"Не вдалося: {error}"[:70],
                                           text_color=uikit.STATE_ERROR)
            else:
                self.lbl_refresh.configure(text="Список тек оновлено", text_color=uikit.STATE_OK)
                self._fill()

        self.refresh([self.section.get()], done)

    def _ok(self):
        selected = self.tree.selection()
        if not selected:
            return
        path = tuple(selected[0].split("\x1f"))
        self.destroy()
        self.on_choose(self.task, self.section.get(), path, bool(self.remember.get()))
