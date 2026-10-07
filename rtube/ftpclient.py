"""FTP для заливання відео: дерево тек, список файлів, заливання з докачуванням.

Сервер Rozetka (Pure-FTPd) уміє лише звичайний FTP — AUTH TLS відхиляє, але
спробу робимо: раптом увімкнуть. Назви тек бувають кирилицею, тож з'єднання
читає байти як latin-1 (без втрат), а для показу назви декодуються з UTF-8 чи
cp1251; назад на сервер іде саме те, що прийшло (_wire).

Захист від помилок, які на сервері не виправиш:
- у корінь розділу (video/, video2/…) не пишемо ніколи — лише глибше;
- теки не створюємо, нічого не видаляємо, крім власного «.rtube-part»;
- файл спершу йде під тимчасовим ім'ям і стає ID.mp4 лише після звірки розміру.
"""

import ftplib
import os
import socket
import time

from . import applog

PART_SUFFIX = ".rtube-part"
BLOCK = 256 * 1024
MAX_DEPTH = 8
# Коди FTP «немає місця»: 452 — недостатньо місця, 552 — перевищено квоту.
NO_SPACE_CODES = ("452", "552")
NO_SPACE_WORDS = ("space", "quota", "disk full", "no room", "storage")


class FtpError(Exception):
    """Помилка FTP зі зрозумілим текстом."""


class NoSpace(FtpError):
    """Розділ забитий — треба брати наступний."""


class Cancelled(FtpError):
    pass


class RootForbidden(FtpError):
    pass


class MissingFolder(FtpError):
    """Теки на сервері немає (перейменували чи прибрали) — треба перечитати дерево."""


def is_no_space(exc):
    text = str(exc).lower()
    return text[:3] in NO_SPACE_CODES or (text[:3] in ("451", "426", "550") and
                                          any(w in text for w in NO_SPACE_WORDS))


def show(raw):
    """Назва з сервера (latin-1 з байтів) → як її показати."""
    data = raw.encode("latin-1", "replace")
    for enc in ("utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw


def to_wire(name):
    """Назва для сервера, якщо її не бачили в списках (UTF-8, як каже FEAT)."""
    return name.encode("utf-8").decode("latin-1")


class FtpClient:
    def __init__(self, host, user, password, port=21, timeout=60, factory=None):
        self.host, self.user, self.port, self.timeout = host, user, port, timeout
        self._password = password
        self._factory = factory         # для тестів: () → об'єкт з інтерфейсом ftplib.FTP
        self.ftp = None
        self.kind = ""
        self._wire = {}                 # показана назва в теці → як її шле сервер
        self._last = 0.0

    # ── з'єднання ──
    def connect(self):
        self.close()
        if self._factory:
            self.ftp = self._factory()
            self.ftp.login(self.user, self._password)
            self.kind = "FTP"
        else:
            self.ftp, self.kind = self._open()
        self._last = time.monotonic()
        return self

    def _open(self):
        tls = ftplib.FTP_TLS(timeout=self.timeout, encoding="latin-1")
        tls.connect(self.host, self.port)
        try:
            tls.auth()
        except Exception as exc:
            applog.info(f"FTP {self.host}: TLS недоступний ({exc}) — звичайний FTP")
            try:
                tls.close()
            except Exception:
                pass
        else:
            tls.login(self.user, self._password)
            tls.prot_p()
            return tls, "FTPS"
        ftp = ftplib.FTP(timeout=self.timeout, encoding="latin-1")
        ftp.connect(self.host, self.port)
        ftp.login(self.user, self._password)
        return ftp, "FTP"

    def close(self):
        if self.ftp is not None:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
        self.ftp = None

    def _call(self, fn):
        """Дія з одним перепідключенням: сервер рве з'єднання після 15 хв тиші."""
        if self.ftp is None:
            self.connect()
        try:
            result = fn(self.ftp)
        except (EOFError, ConnectionError, socket.timeout, OSError, ftplib.error_temp) as exc:
            if isinstance(exc, ftplib.error_temp) and not str(exc).startswith("421"):
                raise
            applog.info(f"FTP {self.host}: з'єднання обірвалось ({exc}) — перепідключаюсь")
            self.connect()
            result = fn(self.ftp)
        self._last = time.monotonic()
        return result

    def keepalive(self, idle=240):
        if self.ftp is not None and time.monotonic() - self._last > idle:
            try:
                self._call(lambda f: f.voidcmd("NOOP"))
            except Exception:
                self.ftp = None

    # ── шляхи ──
    def _wire_path(self, parts):
        out, base = [], ""
        for name in parts:
            key = f"{base}/{name}"
            out.append(self._wire.get(key, to_wire(name)))
            base = key
        return "/" + "/".join(out)

    def list_dir(self, parts):
        """[(назва, це_тека, розмір)] у теці (шлях — список складових від кореня)."""
        path = self._wire_path(parts)

        def work(ftp):
            try:
                return [(name, facts.get("type") == "dir", int(facts.get("size") or 0))
                        for name, facts in ftp.mlsd(path, facts=["type", "size"])
                        if facts.get("type") in ("dir", "file")]
            except ftplib.error_perm:
                pass
            lines = []
            ftp.retrlines(f"LIST {path}", lines.append)
            items = []
            for line in lines:
                bits = line.split(None, 8)
                if len(bits) < 9 or bits[8] in (".", ".."):
                    continue
                items.append((bits[8], line.startswith("d"), int(bits[4]) if bits[4].isdigit() else 0))
            return items

        base = "/" + "/".join(parts) if parts else ""
        result = []
        for raw, is_dir, size in self._call(work):
            name = show(raw)
            self._wire[f"{base}/{name}"] = raw
            result.append((name, is_dir, size))
        return result

    def read_tree(self, sections, progress=None, cancel=None):
        """{розділ: [[тека], [тека, підтека], …]} — лише теки, без файлів."""
        tree = {}
        for section in sections:
            paths = []

            def walk(parts, depth):
                if cancel and cancel():
                    raise Cancelled()
                for name, is_dir, _size in self.list_dir([section] + parts):
                    if is_dir:
                        paths.append(parts + [name])
                        if progress:
                            progress(section, len(paths))
                        if depth < MAX_DEPTH:
                            walk(parts + [name], depth + 1)

            walk([], 1)
            tree[section] = paths
        return tree

    def list_files(self, parts):
        """{ім'я файлу: розмір} у теці."""
        return {name: size for name, is_dir, size in self.list_dir(parts) if not is_dir}

    def size(self, parts):
        path = self._wire_path(parts)

        def work(ftp):
            ftp.voidcmd("TYPE I")
            try:
                return ftp.size(path)
            except ftplib.error_perm:
                return None

        return self._call(work)

    # ── заливання ──
    def upload(self, local, parts, name, progress=None, cancel=None, overwrite=False):
        """Заливає local у теку parts (від кореня, напр. ["video", "odyag", "x"]) під
        ім'ям name. Докачує обірване, звіряє розмір. NoSpace — розділ забитий."""
        if len(parts) < 2:
            raise RootForbidden(f"У корінь розділу {'/'.join(parts) or '/'} не заливаємо")
        total = os.path.getsize(local)
        target, temp = parts + [name], parts + [name + PART_SUFFIX]
        if not overwrite and self.size(target) is not None:
            raise FtpError(f"{'/'.join(target)} уже є на FTP")
        temp_path = self._wire_path(temp)
        sent = [0]

        def on_block(block):
            if cancel and cancel():
                raise Cancelled()
            sent[0] += len(block)
            if progress:
                progress(sent[0], total)

        def work(ftp):
            # Скільки вже на сервері — при кожній спробі: після обриву докачуємо
            # з того місця, а не з нуля.
            ftp.voidcmd("TYPE I")
            try:
                offset = ftp.size(temp_path) or 0
            except ftplib.error_perm:
                offset = 0
            if offset >= total:
                offset = 0
            sent[0] = offset
            with open(local, "rb") as fh:
                fh.seek(offset)
                ftp.storbinary(f"STOR {temp_path}", fh, BLOCK, on_block, rest=offset or None)

        try:
            self._call(work)
        except Cancelled:
            self.close()            # передача перервана на півдорозі — з'єднання не придатне
            raise
        except ftplib.all_errors as exc:
            if is_no_space(exc):
                applog.warning(f"FTP {'/'.join(parts)}: немає місця — {exc}")
                self._remove_part(temp)
                raise NoSpace(str(exc)) from exc
            if str(exc)[:3] in ("550", "553") and "no such" in str(exc).lower():
                raise MissingFolder(f"Теки {'/'.join(parts)} немає на FTP") from exc
            raise FtpError(f"Не вдалося залити {name}: {exc}") from exc
        got = self.size(temp)
        if got != total:
            raise FtpError(f"{name}: на FTP {got} байт замість {total} — заливання не завершено")
        self._call(lambda ftp: ftp.rename(temp_path, self._wire_path(target)))
        return "/".join(target)

    def _remove_part(self, temp):
        """Лише наш тимчасовий файл — нічого іншого програма не видаляє."""
        if not temp[-1].endswith(PART_SUFFIX):
            return
        try:
            self._call(lambda ftp: ftp.delete(self._wire_path(temp)))
        except Exception:
            pass
