"""Рядок унизу вікна: yt-dlp / ffmpeg / JS, встановлення ffmpeg, готові оновлення."""

import threading

import customtkinter as ctk

from . import applog, appupdate, ffinstall, uikit, ytupdate
from .uikit import FONT_SMALL


class StatusBar(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._ffmpeg_was_running = False
        self._ytdlp_ready = None
        self.grid_columnconfigure(0, weight=1)
        self.lbl_status = ctk.CTkLabel(self, text="Перевірка ffmpeg і JS-рантайму…",
                                       font=FONT_SMALL, text_color=uikit.TEXT_MUTED, anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="ew")
        self.lbl_update = ctk.CTkLabel(self, text="", font=FONT_SMALL, anchor="e",
                                       text_color=uikit.STATE_OK)
        self.lbl_update.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.btn_restart = ctk.CTkButton(self, text="Перезапустити", width=110, height=24,
                                         font=FONT_SMALL, command=app.restart)
        self.btn_ffmpeg = ctk.CTkButton(self, text="Встановити ffmpeg", width=140, height=24,
                                        font=FONT_SMALL, command=self.install_ffmpeg)
        self.btn_ffmpeg_cancel = ctk.CTkButton(self, text="Скасувати", width=90, height=24,
                                               font=FONT_SMALL, fg_color=uikit.DANGER,
                                               hover_color=uikit.DANGER_HOVER,
                                               command=ffinstall.cancel)

    def set_status(self, text, color=uikit.TEXT_MUTED):
        self.lbl_status.configure(text=text, text_color=color)

    def set_update_note(self, text, color):
        self.lbl_update.configure(text=text, text_color=color)

    # ── ffmpeg ──
    def install_ffmpeg(self):
        if ffinstall.start():
            applog.info("Встановлення ffmpeg розпочато")
        self.btn_ffmpeg.grid_remove()
        self.btn_ffmpeg_cancel.grid(row=0, column=3, padx=(8, 0))
        self._ffmpeg_was_running = True

    def track_ffmpeg_install(self):
        """Прогрес встановлення ffmpeg — у рядку статусу; по завершенні —
        повторна перевірка оточення, щоб «ffmpeg ✗» змінився на «✓»."""
        st = ffinstall.status()
        if st["running"]:
            self._ffmpeg_was_running = True
            pct = f" ({st['fraction'] * 100:.0f}%)" if st["fraction"] else ""
            self.set_status(st["text"] + pct, uikit.STATE_INFO)
            self.btn_ffmpeg_cancel.grid(row=0, column=3, padx=(8, 0))
        elif self._ffmpeg_was_running:
            self._ffmpeg_was_running = False
            self.btn_ffmpeg_cancel.grid_remove()
            if st["cancelled"]:
                self.set_status("Встановлення ffmpeg скасовано", uikit.STATE_WARN)
                self.btn_ffmpeg.configure(text=f"Встановити ffmpeg (~{ffinstall.APPROX_SIZE_MB} МБ)")
                self.btn_ffmpeg.grid(row=0, column=3, padx=(8, 0))
            elif st["error"]:
                self.set_status(f"ffmpeg не встановився: {st['error']}"[:220], uikit.STATE_ERROR)
                self.btn_ffmpeg.configure(text="Спробувати ще раз")
                self.btn_ffmpeg.grid(row=0, column=3, padx=(8, 0))
            else:
                threading.Thread(target=self.app.send_environment, daemon=True).start()

    # ── оточення ──
    def show_environment(self, version, ffmpeg, runtimes):
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
        self.set_status(text, color)
        applog.info(f"Оточення: {text}; ffmpeg={ffmpeg}; js={runtimes}")

    # ── оновлення, що чекають на перезапуск ──
    def show_ytdlp_ready(self, version):
        self._ytdlp_ready = version
        self.show_update_ready()

    def show_update_ready(self):
        """Що чекає на перезапуск: нова версія програми та/або yt-dlp."""
        app_version = appupdate.state["version"]
        if app_version:
            text = f"Є R-TubeUA {app_version} — завантажено й перевірено"
            button = "Оновити й перезапустити"
        elif self._ytdlp_ready:
            text = f"yt-dlp {self._ytdlp_ready} завантажено — застосується після перезапуску"
            button = "Перезапустити"
        else:
            return
        self.set_update_note(text, uikit.STATE_OK)
        self.btn_restart.configure(text=button, width=180 if app_version else 110)
        self.btn_restart.grid(row=0, column=2, padx=(8, 0))
