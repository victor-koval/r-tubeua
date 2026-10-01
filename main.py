"""Точка входу R-TubeUA."""

import customtkinter as ctk

# Довіру до системних сертифікатів вмикаємо ДО будь-яких мережевих запитів:
# за корпоративним проксі з підміною TLS інакше все падає з
# CERTIFICATE_VERIFY_FAILED. Перевірка лишається справжньою — просто
# за сховищем Windows, а не за вбудованим certifi.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


def main():
    from rtube import settings, uikit
    from rtube.app import THEMES, RTubeApp
    ctk.set_appearance_mode(THEMES.get(settings.get("theme"), "Dark"))
    uikit.apply_theme()
    app = RTubeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
