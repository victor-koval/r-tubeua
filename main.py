"""Точка входу R-TubeUA."""

import sys
import threading


def _selftest():
    """«R-TubeUA.exe --selftest <тека> <версія>» — перевірка свіжого yt-dlp
    окремим процесом (див. rtube/ytupdate.py). Жодного вікна."""
    from rtube import ytupdate
    path, version = sys.argv[sys.argv.index("--selftest") + 1:][:2]
    sys.exit(ytupdate.selftest_main(path, version))


def _app_selftest():
    """«R-TubeUA.exe --app-selftest <версія>» — чи запускається завантажене
    оновлення програми (див. rtube/appupdate.py). Жодного вікна."""
    from rtube import appupdate
    version = sys.argv[sys.argv.index("--app-selftest") + 1]
    sys.exit(appupdate.selftest_main(version))


def _install_excepthooks():
    """У .exe без консолі необроблений виняток інакше зник би безслідно."""
    from rtube import applog

    def on_error(exc_type, exc, tb):
        applog.get_logger().error("Необроблений виняток", exc_info=(exc_type, exc, tb))

    sys.excepthook = on_error
    threading.excepthook = lambda args: on_error(args.exc_type, args.exc_value,
                                                 args.exc_traceback)


def main():
    if "--selftest" in sys.argv:
        _selftest()
    if "--app-selftest" in sys.argv:
        _app_selftest()

    # Довіру до системних сертифікатів вмикаємо ДО будь-яких мережевих запитів:
    # за корпоративним проксі з підміною TLS інакше все падає з
    # CERTIFICATE_VERIFY_FAILED. Перевірка лишається справжньою — просто
    # за сховищем Windows, а не за вбудованим certifi.
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass
    _install_excepthooks()

    # Свіжий yt-dlp з %APPDATA% — ДО першого import yt_dlp (його тягне rtube.app).
    from rtube import ytupdate
    ytupdate.activate()

    import customtkinter as ctk

    from rtube import settings, uikit
    from rtube.app import THEMES, RTubeApp
    ctk.set_appearance_mode(THEMES.get(settings.get("theme"), "Dark"))
    uikit.apply_theme()
    app = RTubeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
