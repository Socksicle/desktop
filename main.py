#!/usr/bin/env python3
"""Application entry point for Socksicle (Windows and Linux).

The shared startup flow lives here; platform-specific behaviour is delegated
to :mod:`utils.startup_utils`, which no-ops on every platform where a
Windows-only feature does not apply.
"""
import argparse
import logging
import sys

from utils.startup_utils import (  # noqa: E402
    acquire_single_instance_lock,
    apply_high_dpi_policy,
    apply_language_from_settings,
    apply_render_backend_from_settings,
    configure_pixmap_cache,
    desktop_file_name,
    find_usable_backend,
    initialize,
    install_native_handlers,
    show_already_running,
    show_backend_missing,
)
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtGui import QIcon

from ui.main_window import RoundedWindow
from utils.platform_utils import get_app_dir


def _apply_platform_style(app):
    """Pin Fusion + M3 dark palette on non-Windows; style QToolTip globally."""
    try:
        from utils.theme import M3Theme

        theme = M3Theme()
        app.setStyleSheet(theme.get_tooltip_style())

        if sys.platform == "win32":
            return

        from PySide6.QtGui import QColor, QPalette

        palette = QPalette()
        roles = {
            QPalette.ColorRole.Window: theme.surface,
            QPalette.ColorRole.WindowText: theme.on_surface,
            QPalette.ColorRole.Base: theme.surface_container_highest,
            QPalette.ColorRole.AlternateBase: theme.surface_container,
            QPalette.ColorRole.ToolTipBase: theme.surface_container_highest,
            QPalette.ColorRole.ToolTipText: theme.on_surface,
            QPalette.ColorRole.Text: theme.on_surface,
            QPalette.ColorRole.Button: theme.surface_container_high,
            QPalette.ColorRole.ButtonText: theme.on_surface,
            QPalette.ColorRole.BrightText: theme.error,
            QPalette.ColorRole.Highlight: theme.primary,
            QPalette.ColorRole.HighlightedText: theme.on_primary,
            QPalette.ColorRole.Link: theme.primary,
            QPalette.ColorRole.PlaceholderText: theme.on_surface_variant,
        }
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Disabled):
            for role, color in roles.items():
                palette.setColor(group, role, QColor(color))
        app.setStyle("Fusion")
        app.setPalette(palette)
    except (AttributeError, TypeError, RuntimeError, ValueError) as e:
        logging.getLogger(__name__).debug("Platform style fallback skipped: %s", e)


def parse_args(argv=None):
    """Parse command line arguments for Socksicle."""
    parser = argparse.ArgumentParser(description="Socksicle Proxy Client")
    parser.add_argument(
        "-m", "--minimized",
        action="store_true",
        help="Start application minimized to system tray",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run the sing-box core with full debug logging",
    )
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.debug:
        from utils.engines import singbox_config
        singbox_config.set_debug_logs(True)
    initialize()
    apply_high_dpi_policy()
    apply_render_backend_from_settings()

    # Single-instance guard: refuse a second instance before it can race on
    # local port binds, PID markers, or settings.json writes.
    instance_lock = acquire_single_instance_lock()

    app = QApplication(sys.argv)
    app.setApplicationName("Socksicle")
    app.setDesktopFileName(desktop_file_name())

    if instance_lock is None:
        apply_language_from_settings()
        show_already_running()
        sys.exit(0)
    app._instance_lock = instance_lock  # holds the lock until process exit

    configure_pixmap_cache()
    _apply_platform_style(app)

    icon_path = get_app_dir() / "icon.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    if find_usable_backend() is None:
        show_backend_missing()
        sys.exit(1)

    from utils.engines.engine_manager import cleanup_stale_core_processes
    cleanup_stale_core_processes()

    window = RoundedWindow()
    install_native_handlers(app, window)

    if args.minimized:
        if QSystemTrayIcon.isSystemTrayAvailable():
            logging.getLogger("startup").info("Starting minimized to system tray (--minimized)")
        else:
            logging.getLogger("startup").info(
                "System tray is not available; showing main window instead of starting minimized."
            )
            window.show()
    else:
        window.show()

    from utils.font_utils import init_app_fonts
    QTimer.singleShot(50, init_app_fonts)

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        logging.getLogger("startup").info("Received KeyboardInterrupt, shutting down...")
        if hasattr(window, "quit_app") and callable(window.quit_app):
            window.quit_app()
        sys.exit(0)


if __name__ == "__main__":
    main()