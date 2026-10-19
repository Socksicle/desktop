"""Material 3 dialog for displaying legal documents based on MessageBox."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.message_box import MessageBox, StandardButton, Icon
from utils.i18n import get_language, tr
from utils.platform_utils import get_app_dir


def load_legal_text(base: str, lang: str | None = None) -> str:
    """Load bundled legal document text from assets/legal/<base>_<ru|en>.txt."""
    legal_dir = get_app_dir() / "assets" / "legal"
    current = lang or get_language()
    # Chinese users get the English legal texts instead of the Russian default.
    fallbacks = ("en", "ru") if current == "zh" else ("ru", "en")
    for candidate_lang in (current, *fallbacks):
        file_path = legal_dir / f"{base}_{candidate_lang}.txt"
        if file_path.is_file():
            try:
                return file_path.read_text(encoding="utf-8")
            except OSError:
                continue
    return tr("legal.missing")


class LegalDialog(MessageBox):
    """Material 3 dialog for displaying legal documents (licenses, privacy, terms)."""

    def __init__(self, title: str, doc_name: str, theme=None, parent=None):
        super().__init__(
            parent=parent,
            icon=Icon.Information,
            title=title,
            text="",
            buttons=StandardButton.Close,
            theme=theme,
        )
        self.doc_name = doc_name
        self.doc_title = title



        self.setMinimumSize(420, 380)
        self.setMaximumSize(750, 850)
        self.resize(520, 580)

        # Allow text view to expand to fill dialog height and scroll smoothly
        self.txt_informative.setMaximumHeight(16777215)
        self.card_layout.setStretchFactor(self.txt_informative, 1)
        self.txt_informative.setFont(QFont("Segoe UI", 10))
        self.txt_informative.setPlainText(load_legal_text(doc_name))
        self.txt_informative.setVisible(True)
