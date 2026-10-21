"""Material 3 dialog for displaying legal documents based on MessageBox."""

import html
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.message_box import MessageBox, StandardButton, Icon
from utils.i18n import get_language, tr
from utils.platform_utils import get_app_dir


def load_legal_text(base: str, lang: str | None = None) -> str:
    """Load bundled legal document text from assets/legal/<base>_<ru|en>.txt."""
    legal_dir = get_app_dir() / "assets" / "legal"
    current = lang or get_language()
    # Chinese and Hebrew users get the English legal texts instead of the Russian default.
    fallbacks = ("en", "ru") if current in ("zh", "he") else ("ru", "en")
    for candidate_lang in (current, *fallbacks):
        file_path = legal_dir / f"{base}_{candidate_lang}.txt"
        if file_path.is_file():
            try:
                return file_path.read_text(encoding="utf-8")
            except OSError:
                continue
    return tr("legal.missing")


def format_legal_html(raw_text: str, theme) -> str:
    """Format legal document text into a clean, highly readable Material 3 HTML document.

    Replaces ascii '====' and '----' with elegant dividers, properly flows sentences into
    paragraphs (eliminating jagged hardcoded wrapping), styles bullet lists with aligned bullets,
    formats license disclaimers into clean callout cards, and turns URLs into clickable links.
    """
    accent = getattr(theme, "primary", "#D0BCFF")
    text_color = getattr(theme, "on_surface", "#E6E0E9")
    variant = getattr(theme, "on_surface_variant", "#CAC4D0")

    def _format_inline(text_str: str) -> str:
        esc = html.escape(text_str)
        esc = re.sub(
            r'(https?://[^\s)]+)',
            f'<a href="\\1" style="color: {accent}; text-decoration: none;">\\1</a>',
            esc
        )
        return esc

    lines = raw_text.splitlines()
    out = []
    out.append(
        f'<div style="color: {variant}; font-family: -apple-system, BlinkMacSystemFont, '
        f'\'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; font-size: 13px; line-height: 1.6;">'
    )

    i = 0
    n = len(lines)

    meta_pattern = re.compile(
        r'^(Репозиторий|Авторские права|Лицензия|Текст лицензии|Компонент|'
        r'Дата последнего обновления|Repository|Copyright|License|Component|Date):\s*(.*)',
        re.IGNORECASE
    )

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Check for title line followed by =====
        if i + 1 < n and re.match(r"^={4,}", lines[i + 1].strip()):
            escaped = _format_inline(stripped)
            out.append(
                f'<div style="font-size: 15px; font-weight: bold; color: {accent}; '
                f'letter-spacing: 0.3px; margin: 4px 0 6px 0;">{escaped}</div>'
            )
            out.append('<hr style="border: none; height: 1px; background-color: rgba(255, 255, 255, 0.12); margin: 6px 0 16px 0;">')
            i += 2
            continue

        # Check for section header followed by -----
        if i + 1 < n and re.match(r"^-{4,}", lines[i + 1].strip()):
            escaped = _format_inline(stripped)
            out.append(
                f'<div style="font-size: 14px; font-weight: bold; color: {text_color}; '
                f'margin: 22px 0 6px 0;">{escaped}</div>'
            )
            out.append('<hr style="border: none; height: 1px; background-color: rgba(255, 255, 255, 0.08); margin: 4px 0 12px 0;">')
            i += 2
            continue

        # Skip standalone separator lines
        if re.match(r"^[=\-]{4,}$", stripped):
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        # License code block / disclaimer detection
        if (stripped.startswith("Permission is hereby granted") or
                stripped.startswith("THE SOFTWARE IS PROVIDED") or
                stripped.startswith("ПРИЛОЖЕНИЕ ПРЕДОСТАВЛЯЕТСЯ")):
            license_paragraphs = []
            cur_p = [stripped]
            i += 1
            while i < n:
                cur = lines[i].strip()
                if i + 1 < n and (re.match(r"^={4,}", lines[i + 1].strip()) or re.match(r"^-{4,}", lines[i + 1].strip())):
                    break
                if re.match(r"^\d+\.\s+", cur):
                    break
                if not cur:
                    if cur_p:
                        license_paragraphs.append(" ".join(cur_p))
                        cur_p = []
                else:
                    cur_p.append(cur)
                i += 1
            if cur_p:
                license_paragraphs.append(" ".join(cur_p))

            card_content = "<br><br>".join(_format_inline(p) for p in license_paragraphs)
            out.append(
                f'<div style="background-color: rgba(255, 255, 255, 0.035); '
                f'border: 1px solid rgba(255, 255, 255, 0.09); border-radius: 12px; '
                f'padding: 12px 16px; margin: 12px 0; font-family: monospace, \'Consolas\', monospace; '
                f'font-size: 11.5px; line-height: 1.5; color: {variant};">{card_content}</div>'
            )
            continue

        # Key-Value metadata
        m = meta_pattern.match(stripped)
        if m:
            key, val = m.group(1), m.group(2)
            val_esc = _format_inline(val)
            out.append(
                f'<div style="margin: 4px 0; color: {variant}; font-size: 13px;">'
                f'<b style="color: {text_color};">{html.escape(key)}:</b> {val_esc}</div>'
            )
            i += 1
            continue

        # Bullet list item (- or •) with multi-line indent support
        if stripped.startswith("- ") or stripped.startswith("• "):
            item_text = stripped[2:].strip()
            i += 1
            while i < n:
                cand = lines[i]
                cand_s = cand.strip()
                if not cand_s:
                    break
                if cand_s.startswith("- ") or cand_s.startswith("• ") or meta_pattern.match(cand_s):
                    break
                if i + 1 < n and (re.match(r"^={4,}", lines[i + 1].strip()) or re.match(r"^-{4,}", lines[i + 1].strip())):
                    break
                if cand.startswith("  ") or cand.startswith("\t"):
                    item_text += " " + cand_s
                    i += 1
                else:
                    break
            esc = _format_inline(item_text)
            out.append(
                f'<table cellpadding="0" cellspacing="0" style="margin: 5px 0; border: none;"><tr>'
                f'<td valign="top" style="color: {accent}; font-weight: bold; padding-right: 8px; font-size: 14px; line-height: 1.6;">•</td>'
                f'<td style="color: {variant}; line-height: 1.6; font-size: 13px;">{esc}</td>'
                f'</tr></table>'
            )
            continue

        # Regular prose paragraph: accumulate consecutive non-empty lines
        p_lines = [stripped]
        i += 1
        while i < n:
            cand = lines[i]
            cand_s = cand.strip()
            if not cand_s:
                break
            if i + 1 < n and (re.match(r"^={4,}", lines[i + 1].strip()) or re.match(r"^-{4,}", lines[i + 1].strip())):
                break
            if cand_s.startswith("- ") or cand_s.startswith("• ") or meta_pattern.match(cand_s):
                break
            if (cand_s.startswith("Permission is hereby granted") or
                    cand_s.startswith("THE SOFTWARE IS PROVIDED") or
                    cand_s.startswith("ПРИЛОЖЕНИЕ ПРЕДОСТАВЛЯЕТСЯ")):
                break
            p_lines.append(cand_s)
            i += 1

        esc = _format_inline(" ".join(p_lines))
        out.append(f'<p style="margin: 0 0 10px 0; line-height: 1.6; color: {variant}; font-size: 13px;">{esc}</p>')

    out.append("</div>")
    return "".join(out)


class LegalDialog(MessageBox):
    """Material 3 dialog for displaying legal documents (licenses, privacy, terms)."""

    def __init__(self, title: str, doc_name: str, theme=None, parent=None):
        self.doc_name = doc_name
        self.doc_title = title
        self._raw_text = load_legal_text(doc_name)

        super().__init__(
            parent=parent,
            icon=Icon.Information,
            title=title,
            text="",
            buttons=StandardButton.Close,
            theme=theme,
        )

        if self._is_overlay:
            self._update_card_geometry()
        else:
            self.setMinimumSize(440, 420)
            self.setMaximumSize(850, 950)
            self.resize(540, 620)

        # Allow text view to expand to fill dialog height and scroll smoothly
        self.txt_informative.setMaximumHeight(16777215)
        self.card_layout.setStretchFactor(self.txt_informative, 1)
        self.txt_informative.setFont(QFont("Segoe UI", 10))
        self.txt_informative.setReadOnly(True)
        self.txt_informative.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)

        def _handle_link_click(event):
            anchor = self.txt_informative.anchorAt(event.position().toPoint())
            if anchor:
                import webbrowser
                webbrowser.open(anchor)
                event.accept()
                return
            super(self.txt_informative.__class__, self.txt_informative).mouseReleaseEvent(event)

        self.txt_informative.mouseReleaseEvent = _handle_link_click
        self.txt_informative.toPlainText = lambda: self._raw_text
        self.txt_informative.setVisible(True)

        self._style_legal_view(self.theme)

    def _update_geometry(self):
        self._update_card_geometry()

    def _update_card_geometry(self):
        if self._is_overlay:
            if self._target_parent:
                pw = self._target_parent.width()
                ph = self._target_parent.height()
                target_w = max(360, min(pw - 32, 700))
                target_h = max(380, min(ph - 40, 780))
                self.card.setFixedSize(target_w, target_h)
            else:
                self.card.setFixedSize(480, 560)
        else:
            self.setMinimumSize(440, 420)
            self.setMaximumSize(850, 950)
            self.resize(540, 620)

    def _style_legal_view(self, theme):
        if not theme:
            return
        surface_low = getattr(theme, "surface_container_lowest", "#0F0D13")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        on_accent_hex = getattr(theme, "on_primary", "#381E72")

        self.txt_informative.setStyleSheet(f"""
            QTextEdit#msgBoxInformative {{
                background-color: {surface_low};
                color: {text_hex};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
                padding: 14px 18px;
                selection-background-color: {accent_hex};
                selection-color: {on_accent_hex};
            }}
            QTextEdit#msgBoxInformative QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 6px 2px 6px 0;
            }}
            QTextEdit#msgBoxInformative QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.2);
                border-radius: 3px;
                min-height: 24px;
            }}
            QTextEdit#msgBoxInformative QScrollBar::handle:vertical:hover {{
                background: rgba(255, 255, 255, 0.35);
            }}
            QTextEdit#msgBoxInformative QScrollBar::add-line:vertical,
            QTextEdit#msgBoxInformative QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QTextEdit#msgBoxInformative QScrollBar::add-page:vertical,
            QTextEdit#msgBoxInformative QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)
        self.txt_informative.setHtml(format_legal_html(self._raw_text, theme))

    def apply_theme(self, theme):
        super().apply_theme(theme)
        self._style_legal_view(theme)
