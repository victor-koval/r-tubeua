"""Картка пакета: кілька посилань, плейлист або канал — одні налаштування на всіх.

Ролики тут не аналізуються наперед (на каналі їх сотні): кожен стає в чергу з
prefs, а якість і доріжку завантажувач визначає сам, коли до нього дійде
черга (downloader.apply_prefs) — за тими ж правилами, що й картка одного відео.
"""

import customtkinter as ctk

from . import downloader, formats, settings, uikit
from .settings_dialog import AUDIO_OPTIONS, QUALITY_OPTIONS, _label_for, _value_for
from .uikit import FONT_SMALL, FONT_UI_BOLD, GREEN, GREEN_HOVER

AUDIO_ONLY_LABEL = "🎵  Лише звук (без відео)"
SUBS_OPTIONS = [("Без субтитрів", "none"), ("Українські від автора, якщо є", "author_uk")]
LIMITS = (10, 25, 50, 100)
PREVIEW = 5


class BatchCard(uikit.Card):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.entries = []
        self.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self, text="", font=uikit.FONT_VIDEO_TITLE, anchor="w",
                                      justify="left")
        self.lbl_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 0))
        self.lbl_title.bind("<Configure>", lambda e: self.lbl_title.configure(
            wraplength=max(200, e.width - 4)))
        self.lbl_preview = ctk.CTkLabel(self, text="", font=FONT_SMALL, anchor="w",
                                        justify="left", text_color=uikit.TEXT_MUTED)
        self.lbl_preview.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 6))

        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16)
        opts.grid_columnconfigure(1, weight=1)

        def label(text, row):
            ctk.CTkLabel(opts, text=text, font=FONT_UI_BOLD, anchor="w", width=130).grid(
                row=row, column=0, sticky="w", pady=4)

        label("Скільки взяти", 0)
        self.opt_count = ctk.CTkOptionMenu(opts, values=["—"], width=220, dynamic_resizing=False,
                                           command=lambda _: self._sync())
        self.opt_count.grid(row=0, column=1, sticky="w", pady=4)

        label("Якість", 1)
        self.quality_labels = [lab for lab, _ in QUALITY_OPTIONS] + [AUDIO_ONLY_LABEL]
        self.opt_quality = ctk.CTkOptionMenu(opts, values=self.quality_labels, width=300,
                                             dynamic_resizing=False,
                                             command=lambda _: self._sync())
        self.opt_quality.grid(row=1, column=1, sticky="w", pady=4)

        label("Звукова доріжка", 2)
        self.opt_audio = ctk.CTkOptionMenu(opts, values=[o[0] for o in AUDIO_OPTIONS], width=300,
                                           dynamic_resizing=False)
        self.opt_audio.grid(row=2, column=1, sticky="w", pady=4)
        self.keep_original_var = ctk.BooleanVar()
        self.chk_original = ctk.CTkCheckBox(opts, text="+ оригінал другою доріжкою",
                                            variable=self.keep_original_var, font=FONT_SMALL)
        self.chk_original.grid(row=2, column=2, sticky="w", padx=(12, 0))

        label("Субтитри", 3)
        self.opt_subs = ctk.CTkOptionMenu(opts, values=[o[0] for o in SUBS_OPTIONS], width=300,
                                          dynamic_resizing=False)
        self.opt_subs.grid(row=3, column=1, sticky="w", pady=4)

        label("Формат файлу", 4)
        self.container = ctk.CTkSegmentedButton(opts, values=["mp4", "mkv"], selected_color=GREEN,
                                                selected_hover_color=GREEN_HOVER)
        self.container.grid(row=4, column=1, sticky="w", pady=4)

        label("Зберегти в", 5)
        folder = ctk.CTkFrame(opts, fg_color="transparent")
        folder.grid(row=5, column=1, columnspan=2, sticky="ew", pady=4)
        folder.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(folder, textvariable=app.dir_var, state="readonly").grid(
            row=0, column=0, sticky="ew")
        uikit.SecondaryButton(folder, text="Змінити…", width=96,
                              command=app._choose_dir).grid(row=0, column=1, padx=(8, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=16, pady=(6, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.btn_download = ctk.CTkButton(buttons, text="", height=44, font=uikit.FONT_BIG_BUTTON,
                                          command=app.download_batch)
        self.btn_download.grid(row=0, column=0, sticky="ew")
        uikit.SecondaryButton(buttons, text="Скасувати", width=110, height=44,
                              command=app.close_batch).grid(row=0, column=1, padx=(8, 0))

    def show(self, title, entries):
        """entries — [(посилання, назва), …]."""
        self.entries = list(entries)
        n = len(self.entries)
        self.lbl_title.configure(text=f"Пакет: {n} відео" + (f" — {title}" if title else ""))
        names = [t for _, t in self.entries[:PREVIEW]]
        more = f"\n… і ще {n - PREVIEW}" if n > PREVIEW else ""
        self.lbl_preview.configure(text="\n".join(f"•  {t}" for t in names) + more)

        counts = [f"Усі ({n})"] + [f"Перші {k}" for k in LIMITS if k < n]
        self.opt_count.configure(values=counts)
        self.opt_count.set(counts[0])

        # Початкові значення — з налаштувань, щоразу заново.
        self.opt_quality.set(_label_for(QUALITY_OPTIONS, settings.get("max_height"), default=3))
        self.opt_audio.set(_label_for(AUDIO_OPTIONS, settings.get("preferred_audio")))
        self.opt_subs.set(SUBS_OPTIONS[1][0])
        self.keep_original_var.set(bool(settings.get("keep_original")))
        self._audio_only = None
        self._sync()

    def _is_audio_only(self):
        return self.opt_quality.get() == AUDIO_ONLY_LABEL

    def _count(self):
        value = self.opt_count.get()
        if value.startswith("Перші "):
            return min(int(value.split()[1]), len(self.entries))
        return len(self.entries)

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
                               prefs=dict(prefs))
                for url, title in self.entries[:self._count()]]
