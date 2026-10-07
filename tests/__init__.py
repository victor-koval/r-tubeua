"""Тести не чіпають справжній %APPDATA%\\R-TubeUA: лог, налаштування, чергу й
done.json пишуть у тимчасову теку (settings.CONFIG_DIR читає RTUBE_HOME).

Спрацьовує, лише коли тести імпортуються пакетом:
    python -m unittest discover -s tests -t .
Димовий тест запускає вікно окремим процесом — змінна переходить і туди.
"""

import atexit
import os
import shutil
import tempfile

os.environ["RTUBE_HOME"] = tempfile.mkdtemp(prefix="rtube-tests-")
# Реєструємо до import logging: atexit іде у зворотному порядку, тож тека
# прибирається вже після logging.shutdown, коли лог закрито.
atexit.register(shutil.rmtree, os.environ["RTUBE_HOME"], ignore_errors=True)
