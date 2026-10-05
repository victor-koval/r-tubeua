"""Звіт по завантаженнях у xlsx: що вийшло з пакета — до заливання на FTP.

Рядок на кожне завдання зі списку: ID товару, посилання, ім'я файлу, статус.
Тривалість — текстом «3,27» (хвилин,секунд), як її копіює клік у програмі:
числом Excel показав би «3,2» замість «3,20».
"""

import os
import time

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .uikit import format_min_sec

HEADERS = ("№", "ID товару", "Посилання", "Назва", "Файл", "Статус", "Деталі",
           "Тривалість", "Розмір, МБ")
MAX_WIDTH = 60

ALREADY_NOTE = "Уже є в теці"     # початок downloader.ALREADY_NOTE


def status_label(state, text):
    """Коротко для фільтра в Excel: «Готово», «Копія», «Помилка»…"""
    text = text or ""
    if state == "done":
        if text.startswith("Готово — копія"):
            return "Копія"
        if text.startswith(ALREADY_NOTE):
            return "Уже було"
        if "без субтитрів" in text:
            return "Без субтитрів"
        return "Готово"
    return {"error": "Помилка", "cancelled": "Скасовано", "queued": "У черзі",
            "running": "Качається"}.get(state, state)


def default_name(now=None):
    return time.strftime("zvit_%Y-%m-%d_%H%M.xlsx", time.localtime(now))


def write_report(path, items):
    """items — словники з ключами product_id, url, title, filepath, state,
    text, duration (секунди або None). Повертає path."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Звіт"
    ws.append(HEADERS)
    total = 0
    for n, item in enumerate(items, 1):
        path_ = item.get("filepath") or ""
        size = None
        if item.get("state") == "done" and path_ and os.path.isfile(path_):
            size = round(os.path.getsize(path_) / 1024 / 1024, 1)
        duration = item.get("duration")
        if duration and item.get("state") == "done":
            total += duration
        ws.append([
            n,
            item.get("product_id") or "",
            item.get("url") or "",
            item.get("title") or "",
            os.path.basename(path_),
            status_label(item.get("state"), item.get("text")),
            item.get("text") or "",
            format_min_sec(duration) if duration else "",
            size,
        ])
    if total:
        ws.append([])
        ws.append(["", "", "", "Разом завантажено", "", "", "", format_min_sec(total), None])
        ws.cell(ws.max_row, 4).font = Font(bold=True)
        ws.cell(ws.max_row, 8).font = Font(bold=True)

    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(items) + 1}"
    for i in range(1, len(HEADERS) + 1):
        letter = get_column_letter(i)
        width = max(len(str(c.value)) for c in ws[letter] if c.value is not None)
        ws.column_dimensions[letter].width = min(MAX_WIDTH, width + 2)
    wb.save(path)
    return path
