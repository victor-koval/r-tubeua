"""Перенос довгих назв: вікно не має зависати в перерахунку розмітки."""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TITLE = "NOSTALGIA -  PHONK MIX FOR NIGHT DRIVE - BEST LXST CXNTURY TYPE - 3 HOUR CAR MUSIC 2025"

# Окремий процес: якщо розмітка зациклиться, впаде за тайм-аутом лише він,
# а не весь прогін тестів (і не збірка, яка їх запускає).
SCRIPT = r"""
import sys
import customtkinter as ctk
from rtube import uikit

# Додаткове масштабування поверх системного DPI: 1.25 відтворює ноутбук зі
# 125 % у Windows на будь-якому екрані.
extra = float(sys.argv[3])
ctk.set_widget_scaling(extra)
ctk.set_window_scaling(extra)
root = ctk.CTk()
root.tk.call("tk", "scaling", extra * root.winfo_fpixels("1i") / 72)
root.geometry("1000x300")
root.grid_columnconfigure(0, weight=1)
head = ctk.CTkFrame(root, fg_color="transparent")
head.grid(row=0, column=0, sticky="new", padx=(250, 16))
head.grid_columnconfigure(0, weight=1)
label = ctk.CTkLabel(head, text=sys.argv[1], font=("Segoe UI", 20, "bold"), anchor="w",
                     justify="left")
label.grid(row=0, column=0, sticky="ew")
if sys.argv[2] == "old":
    label.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - 4)))
else:
    uikit.wrap_to_width(label)
root.update()
print(label.winfo_width(), label.cget("wraplength"), label._get_widget_scaling())
root.destroy()
"""


def run(mode, scaling=1.0, timeout=30):
    return subprocess.run([sys.executable, "-c", SCRIPT, TITLE, mode, str(scaling)], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


class WrapTest(unittest.TestCase):
    def test_long_title_does_not_hang(self):
        # winfo_width — у фізичних пікселях, а wraplength CTkLabel — у
        # логічних (CTk сам множить його на масштаб). Тож очікування рахуємо
        # з фактичного масштабу мітки, а не з ширини напряму: інакше тест
        # проходив лише при 100 % у Windows (на 125 %: 729 != 913).
        for scaling in (1.0, 1.25):
            with self.subTest(scaling=scaling):
                result = run("new", scaling)
                self.assertEqual(result.returncode, 0, result.stderr)
                width, wrap, scale = (float(x) for x in result.stdout.split())
                self.assertAlmostEqual(wrap, width / scale - 4, delta=1)


class OnScreenTest(unittest.TestCase):
    def test_on_screen(self):
        """Збережене місце вікна з відключеного монітора не приймається."""
        from rtube import uikit
        left, top, width, _height = uikit.work_rect()
        if not width:
            self.skipTest("робоча область екрана невідома")
        self.assertTrue(uikit.on_screen(left + 100, top + 100, 800))
        self.assertFalse(uikit.on_screen(-50000, -50000, 800))


if __name__ == "__main__":
    unittest.main()
