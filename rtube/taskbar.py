"""Прогрес на іконці в панелі задач (ITaskbarList3) — на чистому ctypes.

COM-інтерфейс викликаємо напряму через таблицю віртуальних методів, щоб не
тягнути comtypes/pywin32 заради трьох функцій. Індекси методів — з
shobjidl.h: IUnknown (0–2), ITaskbarList (3–7), ITaskbarList2 (8),
ITaskbarList3: SetProgressValue = 9, SetProgressState = 10.

Викликати лише з головного потоку (того, де живе вікно). Будь-яка помилка
вимикає функцію мовчки: смужка на іконці — приємність, а не обов'язок.
"""

import ctypes
import uuid
from ctypes import wintypes

from . import applog

NOPROGRESS, INDETERMINATE, NORMAL, ERROR, PAUSED = 0, 1, 2, 4, 8

_CLSID_TASKBARLIST = "{56FDF344-FD6D-11D0-958A-006097C9A090}"
_IID_ITASKBARLIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
_CLSCTX_INPROC_SERVER = 1
_COINIT_APARTMENTTHREADED = 2


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class Taskbar:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._ptr = None
        self._state = None
        self._value = None
        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)   # уже ініціалізовано — теж гаразд
            ptr = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(_GUID.from_string(_CLSID_TASKBARLIST)), None, _CLSCTX_INPROC_SERVER,
                ctypes.byref(_GUID.from_string(_IID_ITASKBARLIST3)), ctypes.byref(ptr))
            if hr != 0 or not ptr.value:
                raise OSError(f"CoCreateInstance: 0x{hr & 0xFFFFFFFF:08X}")
            self._ptr = ptr
            vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            proto = ctypes.WINFUNCTYPE
            self._hr_init = proto(ctypes.c_long, ctypes.c_void_p)(vtable[3])
            self._set_value = proto(ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
                                    ctypes.c_ulonglong, ctypes.c_ulonglong)(vtable[9])
            self._set_state = proto(ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
                                    ctypes.c_int)(vtable[10])
            self._hr_init(self._ptr)
        except Exception as exc:
            applog.warning(f"Прогрес у панелі задач недоступний: {exc}")
            self._ptr = None

    @property
    def available(self):
        return self._ptr is not None

    def set_state(self, state):
        if not self._ptr or state == self._state:
            return
        try:
            self._set_state(self._ptr, self.hwnd, state)
            self._state = state
            if state in (NOPROGRESS, INDETERMINATE):
                self._value = None
        except Exception as exc:
            applog.warning(f"Панель задач: {exc}")
            self._ptr = None

    def set_progress(self, fraction, state=NORMAL):
        """fraction 0…1. Однакові значення не надсилаємо — опитування йде 10 разів на секунду."""
        if not self._ptr:
            return
        self.set_state(state)
        value = int(max(0.0, min(1.0, fraction)) * 1000)
        if value == self._value:
            return
        try:
            self._set_value(self._ptr, self.hwnd, value, 1000)
            self._value = value
        except Exception as exc:
            applog.warning(f"Панель задач: {exc}")
            self._ptr = None

    def clear(self):
        self.set_state(NOPROGRESS)
