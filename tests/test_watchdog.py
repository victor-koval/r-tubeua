"""Сторож зависань: стек головного потоку в лог, але без хибних тривог."""

import threading
import unittest
from unittest import mock

from rtube import watchdog


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def stuck_in_our_code(ready, release):
    ready.set()
    release.wait(5)


def stuck_in_dialog(ready, release):
    # Ім'я файлу кадру не підробити, тож перевіряємо фільтр діалогів окремо.
    ready.set()
    release.wait(5)


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.ready, self.release = threading.Event(), threading.Event()
        self.clock = Clock()
        self.logged = []
        self.patch = mock.patch.object(watchdog, "applog", mock.MagicMock())
        self.patch.start()

    def tearDown(self):
        self.release.set()
        self.patch.stop()

    def start(self, target, ours):
        thread = threading.Thread(target=target, args=(self.ready, self.release), daemon=True)
        thread.start()
        self.ready.wait(5)
        return watchdog.Watchdog(thread.ident, stall=10, repeat=60, clock=self.clock,
                                 log=self.logged.append, ours=ours)

    def test_reports_stack_once_then_repeats(self):
        dog = self.start(stuck_in_our_code, ours=lambda f: f.endswith("test_watchdog.py"))
        self.clock.now = 5
        self.assertFalse(dog.check())
        self.clock.now = 11
        self.assertTrue(dog.check())
        self.assertIn("stuck_in_our_code", self.logged[0])
        self.assertIn("не відповідає вже 11 с", self.logged[0])
        self.clock.now = 30
        self.assertFalse(dog.check())        # те саме зависання — не засмічуємо лог
        self.clock.now = 72
        self.assertTrue(dog.check())
        dog.beat()                           # ожило
        watchdog.applog.info.assert_called_once()
        self.clock.now = 75
        self.assertFalse(dog.check())

    def test_ignores_pause_outside_our_code(self):
        dog = self.start(stuck_in_our_code, ours=lambda f: False)
        self.clock.now = 100
        self.assertFalse(dog.check())
        self.assertEqual(self.logged, [])

    def test_ignores_dialogs(self):
        dog = self.start(stuck_in_dialog, ours=lambda f: True)
        self.clock.now = 100
        with mock.patch.object(watchdog, "DIALOGS", ("test_watchdog.py",)):
            self.assertFalse(dog.check())
        self.assertEqual(self.logged, [])

    def test_beats_keep_it_quiet(self):
        dog = self.start(stuck_in_our_code, ours=lambda f: True)
        for t in range(0, 100, 5):
            self.clock.now = t
            dog.beat()
            self.assertFalse(dog.check())


if __name__ == "__main__":
    unittest.main()
