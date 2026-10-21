import functools
import logging
import os
import platform
import re
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QPushButton, QLineEdit, QGraphicsBlurEffect
)
from PySide6.QtCore import Qt, QSize, QTimer, Signal, QRectF
from PySide6.QtGui import QFont, QPainter, QPixmap, QColor, QPainterPath, QGuiApplication

from utils.icons import icon as _theme_icon
from utils.i18n import tr
from utils.platform_utils import (
    get_app_dir, get_real_app_version, get_real_device_name, get_socksicle_user_agent,
)
from utils.subscription_store import get_installation_id
from utils.twinsock import VERSION as TWSCORE_VERSION
from .blob_avatar_view import BlobAvatarView
from .legal_dialog import LegalDialog
from .m3_scroll_area import M3ScrollArea


log = logging.getLogger("about")


@functools.lru_cache(maxsize=8)
def get_real_core_engine(selected_engine: str | None = None) -> str:
    """Detect and return the actual installed proxy core and its version string."""
    from utils.engines.engine_manager import get_engine, EngineType

    try:
        et = EngineType(selected_engine) if selected_engine else EngineType.SINGBOX
    except ValueError:
        et = EngineType.SINGBOX

    try:
        engine = get_engine(et)
        binary = engine.find_binary()
        if binary and binary.is_file():
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            proc = subprocess.run(
                [str(binary), "version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2,
                creationflags=flags,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            out = proc.stdout.strip()
            if proc.returncode == 0 and out:
                first_line = out.splitlines()[0].strip()
                if first_line.startswith("sing-box version "):
                    return f"sing-box {first_line[len('sing-box version '):]}"
                if first_line.startswith("Xray "):
                    parts = first_line.split()
                    if len(parts) >= 2:
                        return f"Xray {parts[1]}"
                return first_line
            return et.value
        return f"{et.value} {tr('about.not_installed')}"
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as e:
        log.debug("Core engine probe failed: %s", e)
        return et.value if isinstance(et, EngineType) else "sing-box"


def get_real_os_version() -> str:
    """Retrieve actual operating system name, release, and build number."""
    if sys.platform == "win32":
        try:
            ver = platform.version()
            build = ver.split(".")[-1] if "." in ver else ""
            try:
                build_num = int(build)
            except ValueError:
                build_num = 0
            # Windows 11 starts from build 22000
            os_name = "Windows 11" if build_num >= 22000 else f"Windows {platform.release()}"
            if build_num:
                return f"{os_name} (Build {build_num})"
            return os_name
        except (OSError, AttributeError, ValueError) as e:
            log.debug("Windows OS version probe failed: %s", e)

    elif sys.platform.startswith("linux"):
        # "6.16.5-2-cachyos" -> "6.16.5"; the suffix is distro flavour noise
        kernel_short = platform.release().split("-", 1)[0]
        try:
            os_release = Path("/etc/os-release")
            if os_release.is_file():
                for line in os_release.read_text(encoding="utf-8").splitlines():
                    if line.startswith("PRETTY_NAME="):
                        pretty = line.split("=", 1)[1].strip('"\'')
                        return f"{pretty} (linux {kernel_short})"
        except (OSError, ValueError, UnicodeDecodeError) as e:
            log.debug("Linux OS version probe failed: %s", e)
        return f"Linux {kernel_short}"

    return f"{platform.system()} {platform.release()}"


def get_real_hwid(settings: dict | None = None) -> str:
    """Retrieve personal device TwinSock Key (HWID)."""
    if settings:
        custom_hwid = str(settings.get("hwid_value", "")).strip()
        if custom_hwid:
            return custom_hwid.upper()
        return get_installation_id(settings).upper()
    return get_installation_id({}).upper()


@dataclass
class Contributor:
    nick: str
    role: str
    profile_url: str
    avatar_url: str
    avatar_cache: str


CONTRIBUTORS = [
    Contributor(
        nick="iwtsd",
        role="about.role_lead",
        profile_url="https://github.com/iwtsyddd",
        avatar_url="https://avatars.githubusercontent.com/u/212236894?v=4",
        avatar_cache="avatar_contrib_iwtsd.png",
    ),
    Contributor(
        nick="Peka_r",
        role="about.role_pekar",
        profile_url="https://github.com/itrashxd-dotcom",
        avatar_url="https://avatars.githubusercontent.com/u/242606899?v=4",
        avatar_cache="avatar_contrib_pekar.png",
    ),
]


class CircularAvatarView(QWidget):
    """52x52 circular avatar with initial-letter placeholder and async disk-cached image loader."""
    avatarLoaded = Signal(bytes)

    def __init__(self, initial_letter: str, avatar_url: str, cache_filename: str, parent=None):
        super().__init__(parent)
        self.initial_letter = initial_letter.upper()
        self.avatar_url = avatar_url
        self.cache_filename = cache_filename
        self._pixmap = None
        self._fetching = False
        self._accent_color = QColor("#D0BCFF")
        self._on_accent_color = QColor("#381E72")
        self.setFixedSize(52, 52)
        self.avatarLoaded.connect(self._on_avatar_loaded)
        self._load_avatar()

    def set_palette(self, accent_hex: str, on_accent_hex: str):
        self._accent_color = QColor(accent_hex)
        self._on_accent_color = QColor(on_accent_hex)
        self.update()

    def has_image(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    def _on_avatar_loaded(self, data: bytes):
        try:
            pm = QPixmap()
            if not data or not pm.loadFromData(data):
                log.warning("Contributor avatar: cannot decode image (%d bytes) from %s",
                            len(data) if data else 0, self.avatar_url)
                return
            cache_file = self._cache_file()
            if cache_file:
                try:
                    cache_file.write_bytes(data)
                except OSError:
                    pass
            self._pixmap = self._circular_crop(pm)
            self.update()
        finally:
            self._fetching = False

    def _cache_file(self):
        from utils.platform_utils import get_config_dir
        try:
            cache_dir = get_config_dir() / "avatars"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir / self.cache_filename
        except OSError:
            return None

    def _load_avatar(self):
        cache_file = self._cache_file()
        if cache_file and cache_file.is_file():
            pm = QPixmap(str(cache_file))
            if not pm.isNull():
                self._pixmap = self._circular_crop(pm)

        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        self.reload()

    def reload(self):
        """(Re)fetch the avatar when no image is shown yet. Safe to call often."""
        if self.has_image() or self._fetching or not self.avatar_url:
            return
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        self._fetching = True
        threading.Thread(target=self._fetch, daemon=True, name="ContribAvatarLoader").start()

    def _fetch(self):
        try:
            import urllib.request
            req = urllib.request.Request(
                self.avatar_url,
                headers={"User-Agent": get_socksicle_user_agent()}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            self.avatarLoaded.emit(bytes(data))
        except Exception as e:
            self._fetching = False
            log.warning("Contributor avatar download failed (%s): %s", self.avatar_url, e)

    @staticmethod
    def _circular_crop(src: QPixmap, size: int = 52) -> QPixmap:
        scaled = src.scaled(size * 2, size * 2, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        out = QPixmap(size * 2, size * 2)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size * 2, size * 2)
        painter.setClipPath(path)
        x = (size * 2 - scaled.width()) // 2
        y = (size * 2 - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        out.setDevicePixelRatio(2.0)
        return out

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addEllipse(0, 0, 52, 52)
        painter.fillPath(path, self._accent_color)

        if self._pixmap and not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.setPen(self._on_accent_color)
            font = QFont("Segoe UI", 18, QFont.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(0, 0, 52, 52), Qt.AlignCenter, self.initial_letter)


class _HwidField(QLineEdit):
    """Read-only TwinSock key field that blurs and reveals itself on click."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self._blur = None
        self.set_blurred(True)

    def is_blurred(self) -> bool:
        return self._blur is not None

    def set_blurred(self, blurred: bool):
        if blurred == self.is_blurred():
            return
        if blurred:
            self._blur = QGraphicsBlurEffect(self)
            self._blur.setBlurRadius(8)
            self.setGraphicsEffect(self._blur)
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setGraphicsEffect(None)
            self._blur = None
            self.setCursor(Qt.IBeamCursor)

    def mousePressEvent(self, event):
        self.set_blurred(not self.is_blurred())
        super().mousePressEvent(event)


class ScreenAbout(QWidget):
    """Full-page Material 3 About view matching mobile MainAboutDelegate.kt and activity_main.xml."""

    devUnlocked = Signal()

    def __init__(self, theme, settings: dict | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.settings = settings or {}
        self._hwid_tap_count = 0
        self._contrib_avatars = []
        self._contrib_git_buttons = []

        self._setup_ui()
        self.refresh_data()
        self.apply_theme(theme)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll_area = M3ScrollArea(self, invert_scroll=self.settings.get("invert_scroll", False))

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 115)
        layout.setSpacing(14)

        hero_layout = QVBoxLayout()
        hero_layout.setSpacing(6)
        hero_layout.setContentsMargins(0, 6, 0, 10)

        self.avatar = BlobAvatarView(parent=self, theme=self.theme)
        hero_layout.addWidget(self.avatar, 0, Qt.AlignCenter)

        self.title_label = QLabel("Socksicle")
        self.title_label.setObjectName("aboutAppTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 25px; font-weight: bold; background: transparent;")
        hero_layout.addWidget(self.title_label, 0, Qt.AlignCenter)

        self.version_label = QLabel()
        self.version_label.setObjectName("aboutAppVersion")
        self.version_label.setAlignment(Qt.AlignCenter)
        self.version_label.setStyleSheet("font-size: 14px; font-weight: bold; background: transparent;")
        hero_layout.addWidget(self.version_label, 0, Qt.AlignCenter)

        layout.addLayout(hero_layout)

        self.card_info = QFrame()
        self.card_info.setObjectName("aboutCard")
        ci_layout = QVBoxLayout(self.card_info)
        ci_layout.setContentsMargins(18, 16, 18, 16)
        ci_layout.setSpacing(0)

        self.val_device_name = self._add_info_row(ci_layout, tr("about.device"), "")
        self._add_divider(ci_layout)

        self.val_core_engine = self._add_info_row(ci_layout, tr("about.engine"), "")
        self._add_divider(ci_layout)

        self.val_twscore = self._add_info_row(ci_layout, tr("about.twscore"), TWSCORE_VERSION)
        self._add_divider(ci_layout)

        self.val_os_ver = self._add_info_row(ci_layout, tr("about.os"), "")

        layout.addWidget(self.card_info)

        self.card_hwid = QFrame()
        self.card_hwid.setObjectName("aboutCard")
        ch_layout = QVBoxLayout(self.card_hwid)
        ch_layout.setContentsMargins(18, 16, 18, 16)
        ch_layout.setSpacing(8)

        lbl_hwid_title = QLabel(tr("about.hwid_title"))
        lbl_hwid_title.setObjectName("aboutHwidTitle")
        lbl_hwid_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        lbl_hwid_title.setCursor(Qt.PointingHandCursor)
        ch_layout.addWidget(lbl_hwid_title)

        lbl_hwid_hint = QLabel(tr("about.hwid_hint"))
        lbl_hwid_hint.setObjectName("aboutHwidHint")
        lbl_hwid_hint.setWordWrap(True)
        lbl_hwid_hint.setStyleSheet("font-size: 12px; margin-bottom: 4px;")
        ch_layout.addWidget(lbl_hwid_hint)

        hwid_row = QHBoxLayout()
        hwid_row.setSpacing(10)

        self.hwid_field = QFrame()
        self.hwid_field.setObjectName("hwidField")
        self.hwid_field.setFixedHeight(42)
        hwid_field_layout = QHBoxLayout(self.hwid_field)
        hwid_field_layout.setContentsMargins(14, 0, 14, 0)
        hwid_field_layout.setSpacing(0)

        self.edit_hwid = _HwidField()
        self.edit_hwid.setObjectName("editHwid")
        self.edit_hwid.setFont(QFont("Segoe UI", 11, QFont.Bold))
        hwid_field_layout.addWidget(self.edit_hwid)
        hwid_row.addWidget(self.hwid_field, 1)

        self.btn_copy_hwid = QPushButton(tr("about.copy"))
        self.btn_copy_hwid.setObjectName("btnCopyHwid")
        self.btn_copy_hwid.setFixedHeight(42)
        self.btn_copy_hwid.setCursor(Qt.PointingHandCursor)
        self.btn_copy_hwid.clicked.connect(self._copy_hwid)
        hwid_row.addWidget(self.btn_copy_hwid)

        ch_layout.addLayout(hwid_row)
        layout.addWidget(self.card_hwid)

        lbl_hwid_title.mousePressEvent = self._on_hwid_card_clicked

        self.card_links = QFrame()
        self.card_links.setObjectName("aboutCard")
        cl_layout = QVBoxLayout(self.card_links)
        cl_layout.setContentsMargins(18, 18, 18, 18)
        cl_layout.setSpacing(6)

        lbl_issues_title = QLabel(tr("about.issues_title"))
        lbl_issues_title.setObjectName("communityTitle")
        lbl_issues_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        cl_layout.addWidget(lbl_issues_title)

        lbl_issues_sub1 = QLabel(tr("about.issues_sub1"))
        lbl_issues_sub1.setObjectName("communitySub")
        lbl_issues_sub1.setStyleSheet("font-size: 13.5px;")
        cl_layout.addWidget(lbl_issues_sub1)

        lbl_issues_sub2 = QLabel(tr("about.issues_sub2"))
        lbl_issues_sub2.setObjectName("communitySub")
        lbl_issues_sub2.setStyleSheet("font-size: 13.5px;")
        cl_layout.addWidget(lbl_issues_sub2)
        cl_layout.addSpacing(6)

        social_row = QHBoxLayout()
        social_row.setSpacing(20)
        social_row.setAlignment(Qt.AlignCenter)

        self.btn_gh = QPushButton()
        self.btn_gh.setObjectName("btnSocial")
        self.btn_gh.setFixedSize(48, 48)
        self.btn_gh.setToolTip("GitHub: Socksicle")
        self.btn_gh.setCursor(Qt.PointingHandCursor)
        self.btn_gh.clicked.connect(lambda: webbrowser.open("https://github.com/socksicle/"))
        social_row.addWidget(self.btn_gh)

        self.btn_tg = QPushButton()
        self.btn_tg.setObjectName("btnSocial")
        self.btn_tg.setFixedSize(48, 48)
        self.btn_tg.setToolTip("Telegram: @iwtsddev")
        self.btn_tg.setCursor(Qt.PointingHandCursor)
        self.btn_tg.clicked.connect(lambda: webbrowser.open("https://t.me/iwtsddev"))
        social_row.addWidget(self.btn_tg)

        self.btn_dc = QPushButton()
        self.btn_dc.setObjectName("btnSocial")
        self.btn_dc.setFixedSize(48, 48)
        self.btn_dc.setToolTip("Discord: iwtsyddd")
        self.btn_dc.setCursor(Qt.PointingHandCursor)
        self.btn_dc.clicked.connect(lambda: webbrowser.open("https://discord.com/users/745163148369068033"))
        social_row.addWidget(self.btn_dc)

        cl_layout.addLayout(social_row)
        layout.addWidget(self.card_links)

        self.card_legal = QFrame()
        self.card_legal.setObjectName("aboutCard")
        cle_layout = QVBoxLayout(self.card_legal)
        cle_layout.setContentsMargins(18, 16, 18, 16)
        cle_layout.setSpacing(0)

        lbl_legal_title = QLabel(tr("about.legal_title"))
        lbl_legal_title.setObjectName("legalSectionTitle")
        lbl_legal_title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 4px;")
        cle_layout.addWidget(lbl_legal_title)

        self.btn_licenses = self._add_legal_row(cle_layout, tr("about.legal_licenses"), "licenses")
        self._add_divider(cle_layout)
        self.btn_privacy = self._add_legal_row(cle_layout, tr("about.legal_privacy"), "privacy")
        self._add_divider(cle_layout)
        self.btn_terms = self._add_legal_row(cle_layout, tr("about.legal_terms"), "terms")

        layout.addWidget(self.card_legal)

        self.card_contrib = QFrame()
        self.card_contrib.setObjectName("aboutCard")
        cc_layout = QVBoxLayout(self.card_contrib)
        cc_layout.setContentsMargins(18, 16, 18, 16)
        cc_layout.setSpacing(14)

        lbl_contrib = QLabel(tr("about.contrib_title"))
        lbl_contrib.setObjectName("contribSectionTitle")
        lbl_contrib.setStyleSheet("font-size: 16px; font-weight: bold;")
        cc_layout.addWidget(lbl_contrib)

        for contrib in CONTRIBUTORS:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(12)

            avatar = CircularAvatarView(
                initial_letter=contrib.nick[:1],
                avatar_url=contrib.avatar_url,
                cache_filename=contrib.avatar_cache,
                parent=self
            )
            self._contrib_avatars.append(avatar)
            row_layout.addWidget(avatar)

            texts = QVBoxLayout()
            texts.setSpacing(2)
            lbl_name = QLabel(contrib.nick)
            lbl_name.setStyleSheet("font-size: 15px; font-weight: bold; background: transparent;")
            lbl_role = QLabel(tr(contrib.role))
            lbl_role.setObjectName("contribRole")
            lbl_role.setStyleSheet("font-size: 12.5px; background: transparent;")
            texts.addWidget(lbl_name)
            texts.addWidget(lbl_role)
            row_layout.addLayout(texts, 1)

            btn_git = QPushButton()
            btn_git.setObjectName("btnContribGit")
            btn_git.setFixedSize(40, 40)
            btn_git.setCursor(Qt.PointingHandCursor)
            btn_git.setToolTip(f"GitHub: {contrib.nick}")
            btn_git.clicked.connect(lambda _, url=contrib.profile_url: webbrowser.open(url))
            self._contrib_git_buttons.append(btn_git)
            row_layout.addWidget(btn_git)

            cc_layout.addLayout(row_layout)

        layout.addWidget(self.card_contrib)

        self.scroll_area.setWidget(content)
        root_layout.addWidget(self.scroll_area)

    def refresh_data(self):
        """Dynamically fetch and update real system, core, version, and HWID information."""
        self.hwid_value = get_real_hwid(self.settings)
        self.edit_hwid.setText(self.hwid_value)
        self.version_label.setText(get_real_app_version())
        self.val_device_name.setText(get_real_device_name())
        self.val_core_engine.setText(get_real_core_engine(self.settings.get("engine") if self.settings else None))
        self.val_twscore.setText(TWSCORE_VERSION)
        self.val_os_ver.setText(get_real_os_version())
        for av in self._contrib_avatars:
            av.reload()

    def hideEvent(self, event):
        self.edit_hwid.set_blurred(True)
        super().hideEvent(event)

    def _add_info_row(self, layout: QVBoxLayout, label: str, val: str) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 10)
        l = QLabel(label)
        l.setStyleSheet("font-size: 13.5px; font-weight: bold; background: transparent;")
        v = QLabel(val)
        v.setObjectName("aboutMutedVal")
        v.setStyleSheet("font-size: 13px; background: transparent;")
        row.addWidget(l, 1)
        row.addWidget(v, 0, Qt.AlignRight)
        layout.addLayout(row)
        return v

    def _add_divider(self, layout: QVBoxLayout):
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background-color: rgba(255, 255, 255, 0.07); border: none;")
        layout.addWidget(div)

    def _add_legal_row(self, layout: QVBoxLayout, label: str, doc_name: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("btnLegalRow")
        btn.setFixedHeight(40)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._open_legal_doc(label, doc_name))
        layout.addWidget(btn)
        return btn

    def _open_legal_doc(self, title: str, doc_name: str):
        dialog = LegalDialog(title=title, doc_name=doc_name, theme=self.theme, parent=self)
        dialog.exec()

    def _copy_hwid(self):
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.hwid_value)
        self.btn_copy_hwid.setText(tr("about.copied"))
        if not hasattr(self, "_copy_timer"):
            self._copy_timer = QTimer(self)
            self._copy_timer.setSingleShot(True)
            self._copy_timer.timeout.connect(lambda: self.btn_copy_hwid.setText(tr("about.copy")))
        self._copy_timer.start(1600)

    def _on_hwid_card_clicked(self, event):
        self._hwid_tap_count += 1
        if self._hwid_tap_count >= 5:
            self._hwid_tap_count = 0
            self.devUnlocked.emit()

    def apply_theme(self, theme):
        self.theme = theme
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        on_accent_hex = getattr(theme, "on_primary", "#381E72")
        surface_hex = getattr(theme, "surface_container", getattr(theme, "surface", "#1A1624"))
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")

        self.setStyleSheet(f"""
            QFrame#aboutCard {{
                background-color: {surface_hex};
                border-radius: 18px;
                border: 1px solid rgba(147, 143, 153, 0.15);
            }}
            QLabel {{
                color: {text_hex};
            }}
            QLabel#aboutAppTitle {{
                color: {text_hex};
            }}
            QLabel#aboutAppVersion, QLabel#aboutMutedVal, QLabel#aboutHwidHint, QLabel#contribRole {{
                color: {muted_hex};
            }}
            QLabel#aboutHwidTitle, QLabel#communityTitle, QLabel#communitySub,
            QLabel#legalSectionTitle, QLabel#contribSectionTitle {{
                color: {accent_hex};
            }}
            QLineEdit#editHwid {{
                background: transparent;
                color: {text_hex};
                border: none;
                padding: 0;
            }}
            QFrame#hwidField {{
                background-color: {surface_high};
                border: 1px solid rgba(147, 143, 153, 0.2);
                border-radius: 12px;
            }}
            QPushButton#btnSocial {{
                background-color: {surface_high};
                border: none;
                border-radius: 12px;
            }}
            QPushButton#btnSocial:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QPushButton#btnLegalRow {{
                background-color: transparent;
                color: {text_hex};
                font-size: 14px;
                font-weight: bold;
                text-align: left;
                padding: 0 4px;
                border: none;
            }}
            QPushButton#btnLegalRow:hover {{
                color: {accent_hex};
            }}
            QPushButton#btnContribGit {{
                background-color: transparent;
                border: none;
                border-radius: 20px;
            }}
            QPushButton#btnContribGit:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
        """)

        self.scroll_area.apply_theme(theme)
        self.avatar.apply_theme(theme)
        self.btn_copy_hwid.setStyleSheet(theme.get_button_style("filled"))

        for btn, name in ((self.btn_gh, "github"), (self.btn_tg, "telegram"), (self.btn_dc, "discord")):
            if (ic := _theme_icon(name, accent_hex, 24)) is not None:
                btn.setIcon(ic)
                btn.setIconSize(QSize(24, 24))

        for av in self._contrib_avatars:
            av.set_palette(accent_hex, on_accent_hex)

        for btn in self._contrib_git_buttons:
            if (ic := _theme_icon("github", accent_hex, 22)) is not None:
                btn.setIcon(ic)
                btn.setIconSize(QSize(22, 22))
