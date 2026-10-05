"""Сторож зависань вікна: якщо головний потік довго не відповідає — стек у лог.

Зависання в .exe колеги інакше не розібрати: вікно «не відповідає», а в
лозі порожньо. Вікно опитує себе 10 разів на секунду (_poll) і щоразу
відмічається тут (beat). Якщо відмітки немає STALL секунд, фоновий потік
записує в лог, де саме стоїть головний потік, — з цим одразу видно причину
(так знайшлася нескінченна розмітка довгої назви у 1.3.0).

Не кожна пауза — зависання: поки відкрите системне вікно (питання,
вибір файлу) або користувач тягне вікно за заголовок, Tk теж мовчить. Тож
пишемо лише тоді, коли головний потік стоїть у нашому коді (rtube) і не в
діалозі.
"""

import os
import sys
import threading
import time
import traceback

from . import applog

STALL = 10          # с без відмітки — уже зависання
REPEAT = 60         # повторний стек, якщо досі висить (видно, чи воно рухається)
DIALOGS = ("messagebox.py", "filedialog.py", "commondialog.py", "simpledialog.py")


def _ours(filename):
    return f"{os.sep}rtube{os.sep}" in filename or "/rtube/" in filename


class Watchdog:
    def __init__(self, thread_ident, stall=STALL, repeat=REPEAT, clock=time.monotonic,
                 log=None, ours=_ours, interval=1.0):
        self.thread_ident = thread_ident
        self.stall = stall
        self.repeat = repeat
        self.clock = clock
        self.log = log or applog.warning
        self.ours = ours
        self.interval = interval
        self._last = clock()
        self._reported = None       # коли востаннє писали про це зависання
        self._stop = threading.Event()

    def beat(self):
        """Головний потік живий. Якщо щойно висів — записати, скільки."""
        now = self.clock()
        if self._reported is not None:
            applog.info(f"Вікно знову відповідає (не відповідало ~{now - self._last:.0f} с)")
            self._reported = None
        self._last = now

    def check(self):
        """Один крок сторожа. True — записано стек."""
        now = self.clock()
        stalled = now - self._last
        if stalled < self.stall:
            return False
        if self._reported is not None and now - self._reported < self.repeat:
            return False
        frame = sys._current_frames().get(self.thread_ident)
        if frame is None:
            return False
        stack = traceback.extract_stack(frame)
        files = [f.filename for f in stack]
        if not any(self.ours(f) for f in files) or any(f.endswith(DIALOGS) for f in files):
            return False
        self.log(f"Вікно не відповідає вже {stalled:.0f} с. Головний потік зараз тут:\n"
                 + "".join(traceback.format_list(stack)).rstrip())
        self._reported = now
        return True

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.check()
            except Exception as exc:
                applog.error("Сторож зависань зламався — вимикаю", exc)
                return

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="watchdog").start()

    def stop(self):
        self._stop.set()
