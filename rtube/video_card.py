"""Картка одного відео: прев'ю, якість, доріжка, субтитри, формат, ID товару."""

import io
import re
import urllib.request

import customtkinter as ctk

from . import downloader, formats, settings, tools, uikit
from .uikit import FONT_SMALL, FONT_UI_BOLD, GREEN, GREEN_HOVER

THUMB_SIZE = (224, 126)
SUBS_EMBED, SUBS_FILE = "Вшити у відео", "Окремий файл .srt"
VIDEO_CONTAINERS = ("mp4", "mkv")
AUDIO_CONTAINERS = ("m4a", "mp3")
CONTAINER_HINTS = {
    "mp4": "відкривається скрізь, зокрема на телефоні й телевізорі",
    "mkv": "краща якість звуку (Opus), але не всі телевізори читають",
    "m4a": "без перекодування, як є на YouTube",
    "mp3": "перекодування в 192 кбіт/с — для старих плеєрів",
}


def unique_labels(labels):
    """Підписи в CTkOptionMenu мусять бути різними — інакше вибір не
    зіставити назад із варіантом (напр. «iw» і «he» — обидва «Іврит»)."""
    seen, out = {}, []
    for label in labels:
        n = seen.get(label, 0)
        seen[label] = n + 1
        out.append(label if n == 0 else f"{label} ({n + 1})")
    return out


def load_thumbnail(info):
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


class VideoCard(uikit.Card):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.info = self.choices = self.url = None
        self._thumb_image = None
        self.video_labels = self.audio_labels = self.sub_labels = []
        # Card розтягує колонку 0 — тут у ній прев'ю, і назва відʼїжджала
        # на середину вікна.
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        self.lbl_thumb = ctk.CTkLabel(self, text="", width=THUMB_SIZE[0], height=THUMB_SIZE[1],
                                      fg_color=uikit.SURFACE_RAISED, corner_radius=uikit.RADIUS)
        self.lbl_thumb.grid(row=0, column=0, sticky="nw", padx=(16, 14), pady=(16, 8))

        head = ctk.CTkFrame(self, fg_color="transparent")
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
        opts = ctk.CTkFrame(self, fg_color="transparent")
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
                                           command=lambda _: self._sync_controls())
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
        ctk.CTkEntry(folder, textvariable=app.dir_var, state="readonly").grid(
            row=0, column=0, sticky="ew")
        uikit.SecondaryButton(folder, text="Змінити…", width=96,
                              command=app._choose_dir).grid(row=0, column=1, padx=(8, 0))
        uikit.SecondaryButton(folder, text="📁", width=40,
                              command=lambda: uikit.open_path(app.dir_var.get())).grid(
            row=0, column=2, padx=(8, 0))

        self.btn_download = ctk.CTkButton(self, text="⬇  Завантажити", height=44,
                                          font=uikit.FONT_BIG_BUTTON, command=app.download)
        self.btn_download.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 14))

    # ── показ ──
    def show(self, url, info, choices, thumb, product_id=""):
        self.info, self.choices, self.url = info, choices, url
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

        self.video_labels = unique_labels([v.label for v in choices.videos])
        self.opt_video.configure(values=self.video_labels)
        self.opt_video.set(self.video_labels[choices.default_video])

        self.audio_labels = unique_labels([a.label for a in choices.audios]) or ["—"]
        self.opt_audio.configure(values=self.audio_labels)
        self.opt_audio.set(self.audio_labels[choices.default_audio] if choices.audios else "—")

        self.sub_labels = unique_labels([s.label for s in choices.subs])
        self.opt_subs.configure(values=self.sub_labels)
        self.opt_subs.set(self.sub_labels[choices.default_sub])

        # Кожна нова картка починається зі значень із налаштувань, а не з
        # того, що обирали для попереднього відео.
        self.keep_original_var.set(bool(settings.get("keep_original")))
        self.subs_mode.set(SUBS_FILE if settings.get("subs_mode") == "file" else SUBS_EMBED)
        # ID із рядка «590312170 https://…» — інакше поле порожнє для кожного нового відео.
        self.id_var.set(product_id or "")
        self.container.configure(values=list(VIDEO_CONTAINERS))
        self.container.set(settings.get("container") if settings.get("container")
                           in VIDEO_CONTAINERS else VIDEO_CONTAINERS[0])

        has_uk = any(formats.base_lang(a.lang) == "uk" for a in choices.audios)
        if has_uk:
            hint, color = "✓ Є українська доріжка — обрано її", uikit.STATE_OK
        elif choices.default_sub:
            hint, color = ("Української доріжки немає — увімкнено українські субтитри від "
                           "автора", uikit.STATE_WARN)
        elif any(s.key and formats.base_lang(s.key[0]) == "uk" for s in choices.subs):
            hint, color = ("Української доріжки немає — автопереклад субтитрів можна обрати "
                           "вручну", uikit.STATE_WARN)
        else:
            hint, color = "Української доріжки й субтитрів немає", uikit.STATE_WARN
        self.lbl_audio_hint.configure(text=hint, text_color=color)
        self._on_video_change()

    def clear(self):
        self.info = self.choices = self.url = None

    # ── взаємозалежність контролів ──
    @staticmethod
    def _selected(labels, items, menu):
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

    def _on_video_change(self):
        video = self._video()
        audio_only = bool(video) and video.key[0] == formats.AUDIO_ONLY
        values = AUDIO_CONTAINERS if audio_only else VIDEO_CONTAINERS
        current = self.container.get()
        self.container.configure(values=list(values))
        if current not in values:
            saved = settings.get("audio_container" if audio_only else "container")
            self.container.set(saved if saved in values else values[0])
        self._sync_controls()

    def product_id(self):
        """Лише цифри з поля «ID товару» (пробіли, «;» з таблиці відкидаються)."""
        return re.sub(r"\D", "", self.id_var.get())

    def _update_filename_hint(self):
        """Яким буде ім'я файлу — щоб не дізнаватися про це вже в теці."""
        if not self.choices:
            return
        ext = self.container.get() or "mp4"
        product_id = self.product_id()
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
        self.lbl_container_hint.configure(text=CONTAINER_HINTS.get(self.container.get(), ""))
        self._update_filename_hint()

    # ── завдання ──
    def build_job(self, out_dir):
        """(Job, progressive) за вибором у картці або None, якщо картка порожня."""
        if not self.info or not self.choices:
            return None
        video, audio, sub = self._video(), self._audio(), self._sub()
        if video is None:
            return None
        audio_only = video.key[0] == formats.AUDIO_ONLY
        keep_original = bool(self.keep_original_var.get()) and \
            self.chk_original.cget("state") == "normal"
        job = downloader.Job(
            url=self.url, title=self.info.get("title") or self.url, out_dir=out_dir,
            info=self.info, video_key=video.key, audio_lang=audio.lang if audio else "",
            audio_label=audio.label if audio else "",
            container=self.container.get(), keep_original=keep_original,
            sub_key=sub.key if sub and not audio_only else (),
            subs_mode="file" if self.subs_mode.get() == SUBS_FILE else "embed",
            product_id=self.product_id())
        return job, video.has_audio
