"""Фальшивий FTP-сервер у пам'яті з інтерфейсом ftplib.FTP — для тестів заливання."""

import ftplib


class Server:
    """Спільний стан між з'єднаннями: теки, файли, ліміти розділів."""

    def __init__(self, dirs=(), files=None, limits=None):
        self.dirs = {"/"} | {d.rstrip("/") for d in dirs}
        for d in list(self.dirs):
            parts = d.strip("/").split("/")
            for i in range(1, len(parts)):
                self.dirs.add("/" + "/".join(parts[:i]))
        self.files = dict(files or {})      # повний шлях → bytes
        self.limits = dict(limits or {})    # «/video» → скільки байт ще влазить
        self.drop_next_stor = False         # обірвати наступне STOR посередині
        self.connections = 0
        self.log = []

    def connect(self):
        self.connections += 1
        return Conn(self)


class Conn:
    def __init__(self, server):
        self.s = server
        self.closed = False

    def _check(self):
        if self.closed:
            raise EOFError("з'єднання закрите")

    def login(self, user, password):
        if password != "secret":
            raise ftplib.error_perm("530 Login authentication failed")

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True

    def voidcmd(self, cmd):
        self._check()
        return "200 OK"

    def mlsd(self, path, facts=None):
        self._check()
        path = path.rstrip("/") or "/"
        if path not in self.s.dirs:
            raise ftplib.error_perm("550 No such directory")
        prefix = "" if path == "/" else path
        for d in sorted(self.s.dirs):
            if d != "/" and d.rsplit("/", 1)[0] == prefix and d != path:
                yield d.rsplit("/", 1)[1], {"type": "dir"}
        for f, data in sorted(self.s.files.items()):
            if f.rsplit("/", 1)[0] == prefix:
                yield f.rsplit("/", 1)[1], {"type": "file", "size": str(len(data))}

    def size(self, path):
        self._check()
        if path not in self.s.files:
            raise ftplib.error_perm("550 Can't check for file existence")
        return len(self.s.files[path])

    def _section(self, path):
        return "/" + path.strip("/").split("/")[0]

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        self._check()
        path = cmd.split(" ", 1)[1]
        if path.rsplit("/", 1)[0] not in self.s.dirs:
            raise ftplib.error_perm("553 Can't open that file: No such file or directory")
        data = bytearray(self.s.files.get(path, b"")[:rest or 0])
        self.s.log.append(("STOR", path, rest or 0))
        section = self._section(path)
        while True:
            block = fp.read(blocksize)
            if not block:
                break
            if section in self.s.limits:
                if self.s.limits[section] < len(block):
                    self.s.files[path] = bytes(data)
                    raise ftplib.error_temp("452 Error during write to file")
                self.s.limits[section] -= len(block)
            data += block
            self.s.files[path] = bytes(data)
            if callback:
                callback(block)
            if self.s.drop_next_stor:
                self.s.drop_next_stor = False
                self.closed = True
                raise EOFError("з'єднання обірвалось")
        self.s.files[path] = bytes(data)

    def rename(self, src, dst):
        self._check()
        self.s.files[dst] = self.s.files.pop(src)
        self.s.log.append(("RENAME", src, dst))

    def delete(self, path):
        self._check()
        self.s.files.pop(path)
        self.s.log.append(("DELETE", path))
