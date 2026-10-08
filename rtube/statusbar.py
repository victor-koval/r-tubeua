"""Рядок унизу вікна: yt-dlp / ffmpeg / JS, встановлення ffmpeg і JS-рантайму,
готові оновлення."""

import threading

import customtkinter as ctk

from . import applog, appupdate, ffinstall, jsinstall, uikit, ytupdate
from .uikit import FONT_SMALL


class StatusBar(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._was_running = {}          # встановлювач → чи йшов на минулому опитуванні
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
        # Встановлювач → (кнопка, «Скасувати», колонка, підпис кнопки).
        self._installers = {}
        for installer, column, width in ((ffinstall, 3, 180), (jsinstall, 4, 210)):
            text = f"Встановити {installer._background.name} (~{installer.APPROX_SIZE_MB} МБ)"
            button = ctk.CTkButton(self, text=text, width=width, height=24, font=FONT_SMALL,
                                   command=lambda i=installer: self._install(i))
            cancel = ctk.CTkButton(self, text="Скасувати", width=90, height=24,
                                   font=FONT_SMALL, fg_color=uikit.DANGER,
                                   hover_color=uikit.DANGER_HOVER, command=installer.cancel)
            self._installers[installer] = (button, cancel, column, text)

    def set_status(self, text, color=uikit.TEXT_MUTED):
        self.lbl_status.configure(text=text, text_color=color)

    def set_update_note(self, text, color):
        self.lbl_update.configure(text=text, text_color=color)

    # ── встановлення ffmpeg і JS-рантайму ──
    def install_ffmpeg(self):
        self._install(ffinstall)

    def _install(self, installer):
        if installer.start():
            applog.info(f"Встановлення {installer._background.name} розпочато")
        button, cancel, column, _text = self._installers[installer]
        button.grid_remove()
        cancel.grid(row=0, column=column, padx=(8, 0))
        self._was_running[installer] = True

    def installing(self):
        return any(installer.in_progress() for installer in self._installers)

    def track_installs(self):
        """Прогрес встановлення — у рядку статусу; по завершенні — повторна
        перевірка оточення, щоб «✗» змінився на «✓»."""
        for installer, (button, cancel, column, text) in self._installers.items():
            st = installer.status()
            name = installer._background.name
            if st["running"]:
                self._was_running[installer] = True
                pct = f" ({st['fraction'] * 100:.0f}%)" if st["fraction"] else ""
                self.set_status(st["text"] + pct, uikit.STATE_INFO)
                cancel.grid(row=0, column=column, padx=(8, 0))
            elif self._was_running.get(installer):
                self._was_running[installer] = False
                cancel.grid_remove()
                if st["cancelled"]:
                    self.set_status(f"Встановлення {name} скасовано", uikit.STATE_WARN)
                    button.configure(text=text)
                    button.grid(row=0, column=column, padx=(8, 0))
                elif st["error"]:
                    self.set_status(f"{name} не встановився: {st['error']}"[:220],
                                    uikit.STATE_ERROR)
                    button.configure(text=f"{name}: спробувати ще раз")
                    button.grid(row=0, column=column, padx=(8, 0))
                else:
                    threading.Thread(target=self.app.send_environment, daemon=True).start()

    def _offer(self, installer, needed):
        """Кнопка встановлення — коли програми немає й вона не ставиться просто зараз."""
        button, _cancel, column, text = self._installers[installer]
        if needed and not installer.in_progress():
            button.configure(text=text)
            button.grid(row=0, column=column, padx=(8, 0))
        else:
            button.grid_remove()

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
        notes, color = [], uikit.TEXT_MUTED
        if not ffmpeg:
            notes.append("без ffmpeg не склеїти відео зі звуком")
            color = uikit.STATE_ERROR
        # Без JS-рантайму yt-dlp ходить обхідним клієнтом, і частину публічних
        # роликів YouTube так не віддає: «This video is not available» (перевірено
        # 08.10.2026 у колеги — три дитячі ролики). Тож це не порада, а нестача.
        if not usable:
            notes.append("без JS-рантайму YouTube віддає не всі відео" +
                         (" (наявний застарий)" if outdated else ""))
            if color == uikit.TEXT_MUTED:
                color = uikit.STATE_WARN
        self._offer(ffinstall, not ffmpeg)
        self._offer(jsinstall, not usable)
        if notes:
            text += "   —   " + "; ".join(notes)
        applog.info(f"Оточення: {text}; ffmpeg={ffmpeg}; js={runtimes}")
        if self.installing():
            return      # рядок зараз показує прогрес встановлення
        self.set_status(text, color)

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
