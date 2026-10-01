"""Лог у файл — щоб причину помилки можна було з'ясувати заднім числом.

У списку завантажень видно лише короткий текст; повний виняток і вивід
yt-dlp лягають сюди. Файл ротується, тож не росте нескінченно.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from .settings import CONFIG_DIR

LOG_DIR = os.path.join(CONFIG_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "app.log")

_logger = None


def get_logger():
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("rtube")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(LOG_PATH, maxBytes=1024 * 1024, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%d.%m.%Y %H:%M:%S"))
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())
    _logger = logger
    return _logger


def info(message):
    get_logger().info(message)


def warning(message):
    get_logger().warning(message)


def error(message, exc=None):
    get_logger().error(message, exc_info=exc is not None)


class YtdlpLogger:
    """Перехоплює повідомлення yt-dlp: у консоль .exe без вікна їх однаково
    ніхто не побачить, а в лозі вони рятують при розборі помилок."""

    def debug(self, msg):
        # yt-dlp шле сюди й звичайний вивід (рядки без [debug]) — прогрес
        # завантаження пропускаємо, бо він забив би лог за хвилину.
        if msg.startswith("[download]"):
            return
        if not msg.startswith("[debug] "):
            info(msg)

    def info(self, msg):
        if not msg.startswith("[download]"):
            info(msg)

    def warning(self, msg):
        warning(msg)

    def error(self, msg):
        error(msg)


def open_log_folder():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        os.startfile(LOG_DIR)
        return True
    except Exception:
        return False
