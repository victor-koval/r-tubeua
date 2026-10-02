"""Системні сповіщення Windows через pystray — як трей у Splitter_auto.

Іконка в треї з'являється лише на час сповіщення: постійна іконка програмі,
яка не згортається в трей, нічого не дає. pystray показує сповіщення
від імені самої іконки, тож у Windows видно логотип R-TubeUA.

Усе — у фоновому потоці pystray; Tk тут не чіпаємо.
"""

import os
import threading
import time

from . import applog, uikit

SHOW_SECONDS = 10


def available():
    try:
        import pystray  # noqa: F401
        return True
    except Exception:
        return False


def show(title, message):
    """Показує сповіщення; False — якщо pystray недоступний."""
    try:
        import pystray
        from PIL import Image
        image = Image.open(uikit.resource_path(os.path.join("assets", "logo.ico")))
    except Exception as exc:
        applog.warning(f"Сповіщення недоступні: {exc}")
        return False

    def setup(icon):
        try:
            icon.visible = True
            icon.notify(message, title)
            time.sleep(SHOW_SECONDS)
            icon.remove_notification()
        except Exception as exc:
            applog.warning(f"Сповіщення не показалось: {exc}")
        finally:
            icon.stop()

    def run():
        try:
            pystray.Icon("R-TubeUA", image, "R-TubeUA").run(setup=setup)
        except Exception as exc:
            applog.warning(f"Сповіщення не показалось: {exc}")

    threading.Thread(target=run, daemon=True).start()
    return True
