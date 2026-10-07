"""Пароль FTP — у Диспетчері облікових даних Windows, а не у файлах програми.

Той самий механізм, яким користуються браузери й Outlook: пароль
зашифрований під обліковим записом Windows, і в settings.json його немає.
Лише ctypes — без нових залежностей.
"""

import ctypes
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
TARGET = "R-TubeUA FTP"


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


def _api():
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    return advapi


def save(user, password, target=TARGET):
    blob = password.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob) if blob else None
    cred = _CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=target, UserName=user,
                       CredentialBlobSize=len(blob), Persist=CRED_PERSIST_LOCAL_MACHINE,
                       CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)) if buf else None)
    if not _api().CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), "Пароль не збережено в Диспетчері облікових даних")


def load(target=TARGET):
    """(логін, пароль) або None, якщо нічого не збережено."""
    api = _api()
    ptr = ctypes.POINTER(_CREDENTIAL)()
    if not api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return None
        raise OSError(ctypes.get_last_error(), "Диспетчер облікових даних недоступний")
    try:
        cred = ptr.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize) \
            if cred.CredentialBlobSize else b""
        return cred.UserName or "", blob.decode("utf-16-le")
    finally:
        api.CredFree(ptr)


def delete(target=TARGET):
    """Прибирає збережене; False — нічого й не було."""
    if _api().CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    if ctypes.get_last_error() == ERROR_NOT_FOUND:
        return False
    raise OSError(ctypes.get_last_error(), "Пароль не видалено з Диспетчера облікових даних")
