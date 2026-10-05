"""Димовий тест вікна: основні сценарії проходять без винятків і зависань.

Справжнє вікно в окремому процесі (зависання розмітки вбиває лише його, а не
прогін тестів), без мережі: info ролика — з fixture_dubbed.json, менеджер
завантажень нічого не качає, стани завдань підкидаються вручну.
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import json, os, sys, tempfile
from unittest import mock
sys.path.insert(0, os.getcwd())
from rtube import app, formats, settings

tmp = tempfile.mkdtemp()
real_get = settings.get
off = ("resume_queue", "ytdlp_autoupdate", "app_autoupdate", "watch_clipboard", "notify_done",
       "taskbar_progress")
settings_get = mock.patch.object(settings, "get", side_effect=lambda k: False if k in off
                                 else (tmp if k == "download_dir" else real_get(k)))
settings_set = mock.patch.object(settings, "set_many")
settings_get.start(); settings_set.start()

with open(os.path.join("tests", "fixture_dubbed.json"), encoding="utf-8") as f:
    INFO = json.load(f)

a = app.RTubeApp()
m = a.manager
m.submit = lambda job: m._emit("state", job, "queued", "У черзі")
a.update()

def pump():
    a._drain_events()
    a.update()

def check(cond, what):
    if not cond:
        print("FAIL:", what); sys.exit(1)

# одне відео: картка → «Завантажити» → рядок у черзі, картка сховалась
a._show_info(INFO["webpage_url"], INFO, formats.build_choices(INFO, 1080, "uk"), None)
pump()
check(a.video_card.winfo_manager(), "картка відео показана")
a.download(); pump()
check(len(a.jobs) == 1 and len(a.rows) == 1, "одне завдання в списку")
check(not a.video_card.winfo_manager(), "картка відео сховалась")
check(a.ent_url.get() == "", "поле очищене")

# пакет
entries = [(f"https://www.youtube.com/watch?v=abcdefghij{i}", f"t{i}", str(590000000 + i))
           for i in range(3)]
a._show_batch("", entries); pump()
check(a.batch_card.winfo_manager(), "картка пакета показана")
a.download_batch(); pump()
check(len(a.jobs) == 4, "4 завдання")
check(not a.batch_card.winfo_manager(), "картка пакета сховалась")

# стани: готово / помилка → «Невдалі (1)», «Звіт»
jobs = sorted(a.jobs.values(), key=lambda j: j.id)
jobs[0].state = "done"; m._emit("state", jobs[0], "done", "Готово")
jobs[1].state = "error"; m._emit("state", jobs[1], "error", "Приватне відео")
pump()
check(a.btn_retry_failed.winfo_manager() and "(1)" in a.btn_retry_failed.cget("text"),
      "кнопка «Невдалі (1)»")
check(a.btn_report.winfo_manager(), "кнопка «Звіт»")

# звіт
with mock.patch.object(app.uikit, "select_in_explorer", return_value=True):
    a.save_report()
check(any(n.startswith("zvit_") for n in os.listdir(tmp)), "звіт записано")

# пауза
a.toggle_pause(); pump()
check(m.paused and "пауза" in a.lbl_jobs.cget("text"), "пауза")
a.toggle_pause(); pump()
check(not m.paused, "продовжено")

# повтор невдалих і прибирання завершених
a.retry_failed(); pump()
check(len(a.jobs) == 4, "повтор не міняє кількість")
a._clear_finished(); pump()
check(len(a.jobs) == 3, "завершене прибрано")

a.on_closing(force=True)
print("OK")
"""


class SmokeTest(unittest.TestCase):
    def test_main_flows(self):
        result = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=90,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertIn("OK", result.stdout, result.stdout + result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
