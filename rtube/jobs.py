"""Список завантажень: рядки завдань, пауза, повтор невдалих, звіт."""

import os
import queue
from tkinter import messagebox

import customtkinter as ctk

from . import applog, downloader, report, tools, uikit
from .uikit import FONT_SMALL, FONT_UI, FONT_UI_BOLD, GREEN

ACTIVE = ("queued", "running")
FINISHED = ("done", "error", "cancelled")
# Рядок-віджет — ~25 мс на створення й розкладку: канал на 300 відео заморожував
# вікно на 7 секунд. Тож рядки є в завершених, поточного й лише найближчих у
# черзі; решта — одним підписом «… і ще N у черзі», рядки з'являються по ходу.
QUEUED_ROWS = 20


def job_duration(job):
    """Тривалість ролика в секундах або None (ще не проаналізовано, пряма трансляція)."""
    duration = (job.info or {}).get("duration") or job.duration
    return duration if isinstance(duration, (int, float)) and duration > 0 else None


def _show(widget, visible, **grid):
    """grid / grid_remove лише при зміні: викликається з опитування 10 разів на секунду."""
    if visible and not widget.winfo_manager():
        widget.grid(row=0, padx=(0, 8), **grid)
    elif not visible and widget.winfo_manager():
        widget.grid_remove()


def display_title(job):
    """Назва для рядка: до аналізу ролика з пакета це посилання — коротко."""
    title = job.title or job.url
    return tools.short_url(title) if title.startswith("http") else title


def unique_seconds(jobs):
    """Сума тривалостей завершених — кожен файл один раз: товар, що
    посилається на чужий файл («те саме відео»), часу не додає."""
    seen, total = set(), 0
    for job in jobs:
        if job.state != "done":
            continue
        key = os.path.normcase(os.path.abspath(job.filepath)) if job.filepath else job.id
        if key in seen:
            continue
        seen.add(key)
        total += job_duration(job) or 0
    return total


def report_items(jobs):
    """Рядки звіту: по рядку на кожен товар — і для тих, що їдуть «пасажирами»
    в завдання з тим самим відео (also_for): їм дістається його файл і стан."""
    items = []
    for job in sorted(jobs, key=lambda j: j.id):
        base = {"url": job.url, "title": job.title, "duration": job_duration(job)}
        items.append(dict(base, product_id=job.product_id, filepath=job.filepath,
                          state=job.state, text=job.status))
        for pid in job.also_for:
            if job.state == "done" and job.filepath:
                text = downloader.same_video_note(job.filepath)
            elif job.state in ACTIVE:
                text = f"У черзі · те саме відео, що й у {job.product_id}"
            else:
                text = f"{job.status} (те саме відео, що й у {job.product_id})"
            items.append(dict(base, product_id=pid, state=job.state, text=text,
                              filepath=job.filepath if job.state == "done" else ""))
    return items


def row_order(job):
    """Порядок у списку: що качається — угорі, далі черга в порядку
    завантаження, внизу завершені (свіжі вище). Раніше нові були просто
    зверху, і в пакеті поточний ролик губився під десятками «У черзі»."""
    if job.state == "running":
        return (0, job.id)
    if job.state == "queued":
        return (1, job.id)
    return (2, -job.id)


class JobRow(ctk.CTkFrame):
    """Рядок у списку завантажень: назва, параметри, прогрес, дії."""

    def __init__(self, master, panel, job):
        super().__init__(master, fg_color=uikit.SURFACE_RAISED, corner_radius=uikit.RADIUS)
        self.panel = panel
        self.job = job
        self.fraction = 0.0
        self.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self, text=display_title(job), font=FONT_UI_BOLD,
                                      anchor="w", justify="left")
        self.lbl_title.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=(8, 0))
        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=0, sticky="ew", padx=(12, 8))
        self.lbl_summary = ctk.CTkLabel(meta, text=job.summary(), font=FONT_SMALL, anchor="w",
                                        text_color=uikit.TEXT_MUTED)
        self.lbl_summary.pack(side="left")
        # Тривалість — клік копіює «3,27» (хвилин,секунд).
        self.lbl_duration = uikit.CopyLabel(meta, font=FONT_SMALL, text_color=uikit.STATE_INFO)
        self.lbl_duration.pack(side="left", padx=(10, 0))
        self.refresh_duration()
        # Смужка — лише коли ролик пішов: у черзі на ній була зелена крапка
        # «0%», а рядки займали зайве місце.
        self.bar = ctk.CTkProgressBar(self, height=8, progress_color=GREEN)
        self.bar.set(0)
        self.bar.grid(row=2, column=0, sticky="ew", padx=(12, 8), pady=(6, 2))
        self.bar.grid_remove()
        self.lbl_status = ctk.CTkLabel(self, text="У черзі", font=FONT_SMALL, anchor="w",
                                       text_color=uikit.TEXT_MUTED)
        self.lbl_status.grid(row=3, column=0, sticky="ew", padx=(12, 8), pady=(0, 8))

        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.grid(row=0, column=1, rowspan=4, sticky="e", padx=(0, 10))
        self.btn_cancel = ctk.CTkButton(self.actions, text="Скасувати", width=96, height=30,
                                        fg_color=uikit.DANGER, hover_color=uikit.DANGER_HOVER,
                                        command=lambda: panel.cancel_job(job))
        self.btn_cancel.pack(side="left")
        self.btn_open = uikit.SecondaryButton(self.actions, text="▶ Відкрити", width=96,
                                              height=30, command=self._open)
        self.btn_folder = uikit.SecondaryButton(self.actions, text="📁 У теці", width=84,
                                                height=30, command=self._folder)
        self.btn_retry = uikit.SecondaryButton(self.actions, text="↻ Повторити", width=104,
                                               height=30, command=lambda: panel.retry([job]))
        uikit.wrap_to_width(self.lbl_title)
        if job.state != "queued":
            self.set_state(job.state, job.status)     # рядок створено, коли завдання вже йшло

    def set_meta(self, title, summary):
        """Після відкладеного аналізу: справжня назва й обрана якість/доріжка."""
        self.lbl_title.configure(text=display_title(self.job))
        self.lbl_summary.configure(text=summary)
        self.refresh_duration()

    def refresh_duration(self):
        """Тривалість відома лише після аналізу (для пакета — коли дійде черга)."""
        seconds = job_duration(self.job)
        if seconds:
            value = uikit.format_min_sec(seconds)
            self.lbl_duration.set_value(value, f"⏱ {value}")
        else:
            self.lbl_duration.set_value("", "")

    def set_progress(self, fraction, text):
        self.bar.grid()
        if fraction is None:
            if self.bar.cget("mode") != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start()
        else:
            if self.bar.cget("mode") != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.fraction = max(0.0, min(1.0, fraction))
            self.bar.set(self.fraction)
        self.lbl_status.configure(text=text, text_color=uikit.TEXT_MUTED)

    def set_state(self, state, text):
        if state != "queued" or self.fraction:
            self.bar.grid()
        colors = {"done": uikit.STATE_OK, "error": uikit.STATE_ERROR,
                  "cancelled": uikit.STATE_WARN, "running": uikit.STATE_INFO}
        self.lbl_status.configure(text=text, text_color=colors.get(state, uikit.TEXT_MUTED))
        if state in FINISHED:
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.fraction = 1.0 if state == "done" else 0.0
            self.bar.set(self.fraction)
            self.btn_cancel.pack_forget()
        if state == "done":
            # «Готово», яке не зовсім «готово», — жовтим: інакше пропущене
            # завантаження виглядало як дуже швидке (так і сталося в колеги).
            if "без субтитрів" in text or text == downloader.ALREADY_NOTE:
                self.lbl_status.configure(text_color=uikit.STATE_WARN)
            if text.startswith(downloader.SAME_VIDEO):
                # Окремого файлу немає — ім'я спільного вже в самому тексті.
                self.lbl_status.configure(text_color=uikit.STATE_INFO)
            elif self.job.filepath:
                shown = f"{text}  ·  {os.path.basename(self.job.filepath)}"
                if self.job.also_for:
                    shown += f"  ·  спільний ще для {len(self.job.also_for)}"
                self.lbl_status.configure(text=shown)
            self.btn_open.pack(side="left", padx=(0, 6))
            self.btn_folder.pack(side="left")
        elif state in ("error", "cancelled"):
            self.btn_retry.pack(side="left")
        elif state == "running":
            self.bar.configure(mode="determinate")
            self.fraction = 0.0
            self.bar.set(0)
        elif state == "queued":
            # Після паузи: смужка не має крутитись, наче щось качається.
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.bar.set(self.fraction)

    def _open(self):
        path = self.job.filepath
        if path and os.path.isfile(path):
            try:
                os.startfile(path)
                return
            except OSError:
                pass
        uikit.open_path(self.job.out_dir)

    def _folder(self):
        if not uikit.select_in_explorer(self.job.filepath):
            uikit.open_path(self.job.out_dir)


class JobsPanel(uikit.Card):
    """Картка «Завантаження»: усі завдання сеансу, кнопки над ними, лічильники."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.manager = app.manager
        self.jobs = {}                      # job.id → Job: усе, що в списку
        self.rows = {}                      # job.id → JobRow — не для всіх, див. _materialize
        self.session = set()                # id завдань від останнього «все порожньо»
        self.compact = False

        self.grid_rowconfigure(1, weight=1)
        # Заголовок і кнопки — двома рядками: в один на 880–1000 px кнопки
        # налазили на «Завершено: N · ⏱».
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        head.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        self.lbl_jobs = ctk.CTkLabel(title, text="Завантаження", font=uikit.FONT_TITLE, anchor="w")
        self.lbl_jobs.pack(side="left")
        # Сума тривалостей завершених відео — клік копіює «12,34» (хвилин,секунд).
        self.lbl_total = uikit.CopyLabel(title, font=FONT_UI, text_color=uikit.STATE_INFO)
        self.lbl_total.pack(side="left", padx=(16, 0))
        # Кнопки з'являються, коли мають сенс; порядок сталий — колонки сітки.
        self.buttons = buttons = ctk.CTkFrame(head, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="w", pady=(6, 0))
        buttons.grid_remove()       # з'явиться, коли буде хоч одна кнопка (refresh)
        self.btn_pause = uikit.SecondaryButton(buttons, text="⏸ Пауза", width=100, height=28,
                                               command=self.toggle_pause)
        self.btn_cancel_all = ctk.CTkButton(buttons, text="Скасувати все", width=120, height=28,
                                            fg_color=uikit.DANGER, hover_color=uikit.DANGER_HOVER,
                                            command=self.cancel_all)
        self.btn_retry_failed = uikit.SecondaryButton(buttons, text="↻ Невдалі", width=120,
                                                      height=28, command=self.retry_failed)
        self.btn_report = uikit.SecondaryButton(buttons, text="📊 Звіт", width=86, height=28,
                                                command=self.save_report)
        self.btn_clear = uikit.SecondaryButton(buttons, text="Прибрати завершені", width=150,
                                               height=28, command=self.clear_finished)
        self.jobs_list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.jobs_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self.jobs_list.grid_columnconfigure(0, weight=1)
        self.lbl_empty = ctk.CTkLabel(self.jobs_list, text="Поки що нічого. Можна додати "
                                      "кілька відео підряд — вони качатимуться по черзі.",
                                      font=FONT_SMALL, text_color=uikit.TEXT_MUTED)
        self.lbl_empty.grid(row=0, column=0, pady=18)
        self.lbl_more = ctk.CTkLabel(self.jobs_list, text="", font=FONT_SMALL,
                                     text_color=uikit.TEXT_MUTED)

    def set_compact(self, compact):
        """Поки відкрита картка відео чи пакета — лише заголовок з лічильниками:
        на невисокому вікні інакше картка не вміщалася й «Завантажити» зникала."""
        # Свій прапорець: CTkScrollableFrame живе в canvas, і winfo_manager()
        # у нього завжди «canvas», хоч сховано, хоч ні.
        if compact == self.compact:
            return
        self.compact = compact
        if compact:
            self.jobs_list.grid_remove()
        else:
            self.jobs_list.grid()

    # ── черга ──
    def active_jobs(self):
        return [j for j in self.jobs.values() if j.state in ACTIVE]

    def enqueue(self, jobs):
        """Ставить у чергу, пропускаючи ролики, що вже чекають чи качаються. Повертає, скільки додано."""
        # Ключ — ролик І товар: той самий ролик для двох товарів — це два файли.
        busy = {(j.url, j.product_id) for j in self.active_jobs()}
        added = 0
        for job in jobs:
            key = (job.url, job.product_id)
            if key in busy:
                continue
            busy.add(key)
            self.jobs[job.id] = job
            self.session.add(job.id)
            self.manager.submit(job)
            added += 1
        if added:
            self._materialize()
        return added

    def cancel_job(self, job):
        self.manager.cancel(job)
        self.app.mark_queue_dirty()

    def cancel_all(self):
        active = self.active_jobs()
        if len(active) >= 2 and not messagebox.askyesno(
                self.app.title_text, f"Скасувати всі завантаження ({len(active)})?",
                parent=self):
            return
        # Спершу ті, що чекають, — інакше черга встигла б узяти наступне,
        # поки зупиняється поточне.
        for job in sorted(active, key=lambda j: j.state == "running"):
            self.manager.cancel(job)
        self.app.mark_queue_dirty()

    def retry(self, jobs):
        clones = []
        for job in jobs:
            clones.append(job.clone())
            self.jobs.pop(job.id, None)
            old = self.rows.pop(job.id, None)
            if old:
                old.destroy()
        return self.app.enqueue(clones)

    def retry_failed(self):
        failed = sorted((j for j in self.jobs.values() if j.state in ("error", "cancelled")),
                        key=lambda j: j.id)
        if failed:
            self.retry(failed)
            self.app.hint(f"Знову в черзі: {len(failed)} відео", uikit.STATE_OK)

    def toggle_pause(self):
        if self.manager.paused:
            self.manager.resume()
            applog.info("Черга: продовжено")
        else:
            self.manager.pause()
            applog.info("Черга: пауза")
        self.btn_pause.configure(text="▶ Продовжити" if self.manager.paused else "⏸ Пауза")

    def clear_finished(self):
        for job_id, job in list(self.jobs.items()):
            if job.state in FINISHED:
                del self.jobs[job_id]
                row = self.rows.pop(job_id, None)
                if row:
                    row.destroy()
        self._materialize()

    # ── звіт ──
    def save_report(self):
        """Звіт xlsx по всьому, що зараз у списку, — у теку з відео."""
        jobs = sorted(self.jobs.values(), key=lambda j: j.id)
        if not jobs:
            self.app.hint("Список завантажень порожній — звітувати нема про що", uikit.STATE_WARN)
            return
        dirs = {j.out_dir for j in jobs}
        out_dir = dirs.pop() if len(dirs) == 1 else self.app.dir_var.get()
        items = report_items(jobs)
        path = os.path.join(out_dir, report.default_name())
        try:
            report.write_report(path, items)
        except PermissionError:
            self.app.hint(f"{os.path.basename(path)} відкритий в Excel — закрийте й спробуйте "
                          "ще раз", uikit.STATE_ERROR)
            return
        except Exception as exc:
            applog.error(f"Звіт {path} не записався", exc)
            self.app.hint(f"Звіт не записався: {exc}"[:220], uikit.STATE_ERROR)
            return
        applog.info(f"Звіт: {path} ({len(items)} рядків)")
        self.app.hint(f"Звіт збережено: {path}", uikit.STATE_OK)
        if not uikit.select_in_explorer(path):
            uikit.open_path(out_dir)

    # ── рядки ──
    def _materialize(self, force=()):
        """Створює рядки для завдань, що от-от почнуться (до QUEUED_ROWS у
        черзі), і для force — тих, що вже почались. Завдання без рядка, яке
        скасували в черзі, рядка не отримує: воно лише в лічильнику й у звіті."""
        queued = sum(1 for r in self.rows.values() if r.job.state == "queued")
        new = {i for i in force if i in self.jobs and i not in self.rows}
        for job in sorted(self.jobs.values(), key=lambda j: j.id):
            if queued >= QUEUED_ROWS:
                break
            if job.id not in self.rows and job.id not in new and job.state == "queued":
                new.add(job.id)
                queued += 1
        for job_id in new:
            self.rows[job_id] = JobRow(self.jobs_list, self, self.jobs[job_id])
        self._regrid_rows()

    def _regrid_rows(self):
        order = sorted(self.rows.values(), key=lambda r: row_order(r.job))
        hidden = [j for j in self.jobs.values() if j.id not in self.rows]
        waiting = sum(1 for j in hidden if j.state in ACTIVE)
        parts = []
        if waiting:
            parts.append(f"… і ще {waiting} у черзі — рядки з'являться, коли дійде черга")
        if len(hidden) > waiting:
            parts.append(f"скасовано без рядка: {len(hidden) - waiting} (є у звіті)")
        # Підпис про приховані — одразу під видимою чергою (це її продовження),
        # завершені — нижче.
        widgets = [r for r in order if r.job.state in ACTIVE]
        if parts:
            self.lbl_more.configure(text="  ·  ".join(parts))
            widgets.append(self.lbl_more)
        else:
            self.lbl_more.grid_remove()
        widgets += [r for r in order if r.job.state not in ACTIVE]
        for i, widget in enumerate(widgets, start=1):
            if widget.grid_info().get("row") != i:
                if widget is self.lbl_more:
                    widget.grid(row=i, column=0, pady=(2, 6))
                else:
                    widget.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
        if self.jobs:
            self.lbl_empty.grid_remove()
        else:
            self.lbl_empty.grid()

    # ── події менеджера ──
    def handle_events(self, limit=200):
        """Розбирає події завантажувача. Повертає (чи змінився стан, чи була помилка)."""
        started, changed, error = [], False, False
        for _ in range(limit):
            try:
                kind, job_id, payload = self.manager.events.get_nowait()
            except queue.Empty:
                break
            job = self.jobs.get(job_id)
            if job is None:
                continue
            if kind == "state":
                job.status = payload[1]
                changed = True
                error = error or payload[0] == "error"
                if payload[0] == "running" and job_id not in self.rows:
                    started.append(job_id)      # рядок створиться вже з цим станом
            row = self.rows.get(job_id)
            if row is None:
                continue
            if kind == "progress":
                row.set_progress(*payload)
            elif kind == "meta":
                row.set_meta(*payload)
            elif kind == "state":
                row.set_state(*payload)
                row.refresh_duration()
        if started or changed:
            # Черга посунулась — підтягуємо наступні рядки (і лічильник «ще N»).
            self._materialize(force=started)
        return changed, error

    def refresh(self):
        """Заголовок, лічильники й кнопки — з опитування вікна."""
        active = self.active_jobs()
        paused = self.manager.paused
        failed = sum(1 for j in self.jobs.values() if j.state in ("error", "cancelled"))
        _show(self.btn_pause, bool(active) or paused, column=0)
        _show(self.btn_cancel_all, bool(active), column=1)
        _show(self.btn_retry_failed, bool(failed), column=2)
        finished = any(j.state in FINISHED for j in self.jobs.values())
        _show(self.btn_report, finished, column=3)
        _show(self.btn_clear, finished, column=4)
        if bool(active) or paused or finished:
            if not self.buttons.winfo_manager():
                self.buttons.grid()
        elif self.buttons.winfo_manager():
            self.buttons.grid_remove()
        if failed:
            self.btn_retry_failed.configure(text=f"↻ Невдалі ({failed})")
        if active:
            state = "пауза" if paused else "у роботі"
            self.lbl_jobs.configure(text=f"Завантаження · {state} {len(active)}")
        else:
            self.lbl_jobs.configure(text="Завантаження · пауза" if paused else "Завантаження")
        self._update_total()
        return active

    def _update_total(self):
        """«Завершено: 4 · ⏱ 12,34» — лише ті, що зараз у списку: «Прибрати
        завершені» обнуляє лічильник разом зі списком."""
        done = [j for j in self.jobs.values() if j.state == "done"]
        if not done:
            self.lbl_total.set_value("", "")
            return
        seconds = unique_seconds(done)
        value = uikit.format_min_sec(seconds)
        self.lbl_total.set_value(value, f"Завершено: {len(done)}  ·  ⏱ {value}")

    # ── для панелі задач і сповіщень ──
    def session_jobs(self):
        return [self.jobs[i] for i in self.session if i in self.jobs]

    def running_fraction(self):
        """Частка поточного завантаження або None, якщо невідома (аналіз, склеювання)."""
        for job in self.session_jobs():
            row = self.rows.get(job.id)
            if job.state == "running" and row is not None:
                return row.fraction if row.bar.cget("mode") == "determinate" else None
        return None
