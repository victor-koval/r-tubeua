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
from rtube import app, appupdate, credentials, formats, settings, tools

tmp = tempfile.mkdtemp()
real_get = settings.get
off = {"resume_queue", "check_updates_on_start", "watch_clipboard", "notify_done",
       "taskbar_progress", "auto_report"}
settings_get = mock.patch.object(settings, "get", side_effect=lambda k: False if k in off
                                 else (tmp if k == "download_dir" else real_get(k)))
settings_set = mock.patch.object(settings, "set_many")
settings_get.start(); settings_set.start()
# Справжній Диспетчер облікових даних не чіпаємо: закриття Налаштувань без
# «Запам'ятати» видаляє збережений пароль FTP.
for _name in ("save", "delete"):
    mock.patch.object(credentials, _name).start()
mock.patch.object(credentials, "load", return_value=None).start()
# На сервері CI немає ffmpeg — «Завантажити» спитало б, чи його поставити.
mock.patch.object(tools, "find_ffmpeg", return_value="ffmpeg.exe").start()
# Несподіване вікно-питання без людини поруч висіло б до тайм-ауту — хай
# краще тест одразу падає й каже, що за вікно.
import tkinter.messagebox as _mb
for _name in ("askyesno", "showerror", "showinfo", "showwarning"):
    mock.patch.object(_mb, _name, side_effect=lambda *a, _n=_name, **k: (
        print("FAIL: несподіване вікно", _n, a[1:2], flush=True), os._exit(1))).start()

with open(os.path.join("tests", "fixture_dubbed.json"), encoding="utf-8") as f:
    INFO = json.load(f)

a = app.RTubeApp()
m = a.manager
p = a.jobs_panel
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
check(p.compact, "список згорнутий, поки картка відкрита")
a.close_video(); pump()
check(not a.video_card.winfo_manager() and not p.compact, "«Скасувати» ховає картку")
a._show_info(INFO["webpage_url"], INFO, formats.build_choices(INFO, 1080, "uk"), None)
pump()
a.download(); pump()
check(not p.compact, "після «Завантажити» список розгорнутий")
check(len(p.jobs) == 1 and len(p.rows) == 1, "одне завдання в списку")
check(not a.video_card.winfo_manager(), "картка відео сховалась")
check(a.ent_url.get() == "", "поле очищене")

# пакет
entries = [(f"https://www.youtube.com/watch?v=abcdefghij{i}", f"t{i}", str(590000000 + i))
           for i in range(3)]
a._show_batch("", entries); pump()
check(a.batch_card.winfo_manager(), "картка пакета показана")
b = a.batch_card
check(len(b.tree.get_children()) == 3, "у таблиці 3 рядки")
b._remove(["1"]); pump()
check(len(b.entries) == 2 and "2 відео" in b.btn_download.cget("text"), "✕ прибрав рядок")
b.entries.append(entries[1])
b._render(); pump()

# список, вставлений у поле: поле очищається, у таблиці всі рядки
a.ent_url.insert(0, "\n".join(["590312170 https://youtu.be/pn6mZ0Bcugo ;", "шапка",
                                "590312171 https://youtu.be/1G01ROKhAAw"]))
a.analyze(); pump()
check(a.ent_url.get() == "", "поле після списку порожнє")
check(len(b.tree.get_children()) == 2, "список у таблиці")
check("пропущено: 1" in a.lbl_url_hint.cget("text"), "підказка про пропущений рядок")
a._show_batch("", entries); pump()
a.download_batch(); pump()

# повтори в пакеті: одне завдання на відео, решта товарів — у also_for
same = [(entries[0][0], "t", "700000001"), (entries[0][0], "t", "700000002"),
        ("https://www.youtube.com/watch?v=zzzzzzzzzzz", "t", "700000003")]
a._show_batch("", same); pump()
check("2 відео (3 товари)" in b.btn_download.cget("text"), "кнопка рахує відео й товари")
before = len(p.jobs)
a.download_batch(); pump()
added = sorted(p.jobs.values(), key=lambda j: j.id)[before:]
check(len(added) == 2 and added[0].also_for == ["700000002"], "одне завдання на відео")
for j in added:
    m.cancel(j)
pump()
p.clear_finished(); pump()
check(len(p.jobs) == 4, "4 завдання")
check(not a.batch_card.winfo_manager(), "картка пакета сховалась")

# стани: готово / помилка → «Невдалі (1)», «Звіт»
jobs = sorted(p.jobs.values(), key=lambda j: j.id)
jobs[0].state = "done"; m._emit("state", jobs[0], "done", "Готово")
jobs[1].state = "error"; m._emit("state", jobs[1], "error", "Приватне відео")
pump()
check(p.btn_retry_failed.winfo_manager() and "(1)" in p.btn_retry_failed.cget("text"),
      "кнопка «Невдалі (1)»")
check(p.btn_report.winfo_manager(), "кнопка «Звіт»")

# фільтр: «Помилки» — лише рядок з помилкою
check(p.seg_filter.winfo_manager(), "фільтр видно, коли є помилка")
p.set_filter("Помилки"); pump()
shown = [r.job for r in p.rows.values() if r.winfo_manager()]
check(shown == [jobs[1]], "у фільтрі «Помилки» лише помилка")
check(not p.lbl_more.winfo_manager(), "у фільтрі немає підпису про чергу")
p.set_filter("Усі"); pump()
check(len([r for r in p.rows.values() if r.winfo_manager()]) == len(p.rows), "«Усі» — усі рядки")

# звіт
with mock.patch.object(app.uikit, "select_in_explorer", return_value=True):
    p.save_report()
check(any(n.startswith("zvit_") for n in os.listdir(tmp)), "звіт записано")
for n in os.listdir(tmp):
    os.remove(os.path.join(tmp, n))

# автозвіт: пакет (2 товари) спорожнів — звіт пишеться сам
off.discard("auto_report")
p.session = {jobs[0].id, jobs[1].id}
a._on_queue_idle()
check(any(n.startswith("zvit_") for n in os.listdir(tmp)), "автозвіт після пакета")
off.add("auto_report")

# пауза
p.toggle_pause(); pump()
check(m.paused and "пауза" in p.lbl_jobs.cget("text"), "пауза")
p.toggle_pause(); pump()
check(not m.paused, "продовжено")

# повтор невдалих і прибирання завершених
p.set_filter("Помилки"); pump()
p.retry_failed(); pump()
check(len(p.jobs) == 4, "повтор не міняє кількість")
check(p.filter == "Усі", "після «Невдалі» фільтр знову «Усі»")
p.clear_finished(); pump()
check(len(p.jobs) == 3, "завершене прибрано")

# готове оновлення: зелена шапка й кнопка в Налаштуваннях
check("Налаштування" in a.btn_settings.cget("text"), "без оновлення шапка звичайна")
a.open_settings(); pump()
d = a._settings_window
check(not d.update_line.winfo_manager(), "без оновлення кнопки немає")
d.destroy(); pump()
appupdate.state.update(version="9.9.9", path=None)
a.show_update_badge(); pump()
check("Є оновлення" in a.btn_settings.cget("text"), "шапка: «Є оновлення»")
a.open_settings(); pump()
d = a._settings_window
check(d.update_line.winfo_manager() and "9.9.9" in d.btn_restart.cget("text"),
      "кнопка «Оновити до 9.9.9…» в Налаштуваннях")
check(d.tab == "Оновлення", "з готовим оновленням Налаштування відкриваються на «Оновлення»")
# активне завантаження без «продовжувати після перезапуску» — не перезапускаємо
with mock.patch.object(a, "restart") as restart:
    d.btn_restart.invoke(); pump()
    check(d.winfo_exists() and "Дочекайтесь" in d.lbl_check.cget("text") and not restart.called,
          "перешкода показана у вікні налаштувань")
    for j in p.active_jobs():
        m.cancel(j)
    pump()
    d.btn_restart.invoke(); pump()
    check(restart.called and not d.winfo_exists(), "кнопка перезапускає й закриває вікно")
appupdate.state.update(version=None, path=None)

# FTP: план → заливання на фальшивий сервер → стан у рядку; вибір теки вручну
import time
from rtube import downloader, ftpclient, ftpstate, uploader
# Своє дерево тек фальшивого сервера, а не вшита база справжнього FTP.
mock.patch.object(ftpstate, "BASE_NAME", "nema_bazy.json").start(); ftpstate.reset_cache()
from tests.fakeftp import Server
server = Server(dirs=["/video/odyag_vzuttya_ta_aksesuari/odyag", "/video/krasa_ta_zdorovya/apteka"])
hoodie = {"crumbs_ua": ["Одяг, взуття та аксесуари", "Одяг", "Чоловічі худі"], "crumbs_ru": [],
          "slugs": [], "mpath": ["1162030", "2033137", "4637959"]}
a.ftp_login = {"host": "h", "user": "u", "password": "secret"}
a.uploads = uploader.UploadManager(
    lambda: ftpclient.FtpClient("h", "u", "secret", factory=server.connect).connect(),
    lambda: ["video"], product_info={"590312170": hoodie}.get)
files = []
for pid in ("590312170", "111"):
    path = os.path.join(tmp, pid + ".mp4")
    with open(path, "wb") as f:
        f.write(os.urandom(40000))
    job = downloader.Job(url=f"https://youtu.be/x{pid}", title=pid, out_dir=tmp, product_id=pid)
    p.enqueue([job])
    job.state, job.filepath = "done", path
    m._emit("state", job, "done", "Готово")
    files.append(job)
pump()
check("(2)" in p.btn_ftp.cget("text"), "кнопка «На FTP (2)»")

def until(cond, what, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump()
        if cond():
            return
        time.sleep(0.05)
    print("FAIL:", what); sys.exit(1)

a.open_ftp(); pump()
card = a.ftp_card
check(card.winfo_manager(), "картка FTP показана")
tasks = {t.product_id: t for t in card.tasks}
until(lambda: not a.uploads.is_busy() and tasks["590312170"].state == uploader.PLANNED,
      "худі — тека визначена")
check(tasks["590312170"].folder == "video/odyag_vzuttya_ta_aksesuari/odyag", "тека для худі")
check(tasks["111"].state == uploader.NEED_CHOICE, "невідомий товар — «оберіть теку»")
check("(1)" in card.btn_upload.cget("text"), "кнопка «Залити на FTP (1)»")

# вибір теки вручну для невідомого
tasks["111"].mpath = ["777"]
from rtube.ftp_card import FolderPicker
picker = FolderPicker(card, tasks["111"], ["video"], card._chosen); pump()
picker.tree.selection_set("krasa_ta_zdorovyaapteka")
picker._ok()
until(lambda: not a.uploads.is_busy() and tasks["111"].state == uploader.PLANNED, "обрана тека")
check(ftpstate.rules("video").get("777") == ["krasa_ta_zdorovya", "apteka"], "вибір запам'ятався")

card.upload()
until(lambda: not a.uploads.is_busy() and all(t.state == uploader.UPLOADED for t in tasks.values()),
      "обидва залито")
check("/video/odyag_vzuttya_ta_aksesuari/odyag/590312170.mp4" in server.files, "файл на FTP")
row = p.rows[files[0].id]
# Потік уже позначив «залито» — рядок оновиться з наступним опитуванням вікна.
until(lambda: row.lbl_ftp.winfo_manager() and "залито" in row.lbl_ftp.cget("text"),
      "стан FTP у рядку")
check(not p.btn_ftp.winfo_manager(), "усе залито — кнопки «На FTP» немає")
a.close_ftp(); pump()

# Налаштування відкриваються на розділі FTP
a.open_settings(section="ftp"); pump()
d = a._settings_window
check(d.ftp_user.get() == "u", "вхід FTP у Налаштуваннях")
check(d.tab == "FTP", "Налаштування відкрито на вкладці FTP")
# розділи — прапорцями; останній зняти не можна
check(list(d.ftp_section_vars) == ["video", "video2", "video3", "video4", "video5"],
      "п'ять прапорців розділів")
for _name in ("video2", "video3", "video4", "video5"):
    d.ftp_section_vars[_name].set(False); d._toggle_section(_name)
d.ftp_section_vars["video"].set(False); d._toggle_section("video")
check(d.ftp_section_vars["video"].get() and "Хоча б один" in d.lbl_sections.cget("text"),
      "останній розділ не знімається")
for _name in ("video2", "video3", "video4", "video5"):
    d.ftp_section_vars[_name].set(True); d._toggle_section(_name)
# Ctrl+V/C на будь-якій розкладці — для всіх полів (клас Entry), і в Налаштуваннях
from types import SimpleNamespace
from rtube import uikit as _u
check("<Control-Key>" in a.bind_class("Entry") and "<Button-3>" in a.bind_class("Entry"),
      "Ctrl+C/V/X/A і меню правою кнопкою — для всіх полів")
host = d.ftp_host_entry._entry
host.delete(0, "end"); a.clipboard_clear(); a.clipboard_append("45.128.216.49")
ua_ctrl_v = SimpleNamespace(state=0x4, keycode=_u.KEY_V, keysym="Cyrillic_em", widget=host)
check(_u._key_action(ua_ctrl_v) == "paste", "Ctrl+V на українській розкладці впізнано")
_u._text_action(host, "paste"); pump()
check(d.ftp_host.get() == "45.128.216.49", "вставка в поле «Сервер»")
pwd = d.ftp_pass_entry._entry
pwd.delete(0, "end"); pwd.insert(0, "secret"); pwd.selection_range(0, "end")
a.clipboard_clear(); a.clipboard_append("щось інше")
_u._text_action(pwd, "copy"); pump()
check(a.clipboard_get() == "щось інше", "з поля пароля не копіюється")
_l, _t, _w, _h = app.uikit.work_rect()
check(not _h or d.winfo_height() + 30 <= _h, "вікно Налаштувань влазить в екран")
# два залиті вище файли вже в історії video
check(d.btn_history.cget("text") == "Оновити базу" and "video — 2" in d.lbl_base.cget("text"),
      "«База розкладання»: кнопка «Оновити базу» і скільки товарів відомо")
check(d.btn_ftp_check.cget("text") == "Перевірити вхід", "кнопка «Перевірити вхід»")
d.destroy(); pump()

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
