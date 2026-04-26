"""About and first-run Disclaimer dialogs for CardLens."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

_APP_VERSION = "0.6.2"

_THIRD_PARTY = """\
<b>PySide6 / Qt</b><br>
License: GNU Lesser General Public License v3 (LGPL v3)<br>
<a href="https://www.qt.io/licensing">https://www.qt.io/licensing</a><br><br>

<b>EasyOCR</b><br>
Copyright © JaidedAI — License: Apache 2.0<br>
<a href="https://github.com/JaidedAI/EasyOCR">https://github.com/JaidedAI/EasyOCR</a><br><br>

<b>PyTorch</b><br>
Copyright © Meta Platforms — License: BSD 3-Clause<br>
<a href="https://github.com/pytorch/pytorch/blob/main/LICENSE">https://pytorch.org/</a><br><br>

<b>OpenCV (cv2)</b><br>
License: Apache 2.0<br>
<a href="https://opencv.org">https://opencv.org</a><br><br>

<b>NumPy</b><br>
License: BSD 3-Clause<br>
<a href="https://numpy.org">https://numpy.org</a><br><br>

<b>Requests</b><br>
Copyright © Kenneth Reitz — License: Apache 2.0<br>
<a href="https://requests.readthedocs.io">https://requests.readthedocs.io</a><br><br>

<b>openpyxl</b><br>
License: MIT<br>
<a href="https://openpyxl.readthedocs.io">https://openpyxl.readthedocs.io</a><br><br>

<b>pokemontcg.io API</b><br>
Third-party web service — not affiliated with Nintendo or The Pokémon Company.<br>
You must agree to their Terms of Service when using this app.<br>
<a href="https://pokemontcg.io">https://pokemontcg.io</a><br><br>

<b>TCGPlayer API</b><br>
Third-party web service operated by TCGPlayer, Inc. — not affiliated with Nintendo or The Pokémon Company.<br>
Used for sealed-product pricing (ETB, Booster Bundle). Subject to their Terms of Service.<br>
<a href="https://www.tcgplayer.com">https://www.tcgplayer.com</a><br><br>

<b>Trading Card Game Trademarks</b><br>
CardLens is not affiliated with or endorsed by Nintendo or The Pokémon Company International.<br>
All trademarks, card artworks, and logos are property of their respective owners:<br>
Pokémon TCG — Nintendo / The Pokémon Company International.
"""

_DISCLAIMER_TEXT = """\
<b>Wichtige Hinweise vor der ersten Nutzung</b><br><br>

<b>1. pokemontcg.io API</b><br>
CardLens ruft Karteninformationen und Preise über die <b>pokemontcg.io API</b> ab.<br>
Du bist der Vertragspartner gegenüber pokemontcg.io — nicht der Entwickler dieser App.<br>
Mit der Nutzung akzeptierst du deren \
<a href="https://pokemontcg.io">Nutzungsbedingungen</a>.<br>
Ein eigener API-Key erhöht das Anfragen-Limit auf 20.000/Tag (kostenlos registrieren).<br><br>

<b>2. Markenzeichen</b><br>
CardLens hat <b>keine offizielle Verbindung</b> zu Nintendo / The Pokémon Company International.<br>
Alle Markenzeichen, Karten-Artworks und Logos sind Eigentum ihrer jeweiligen Rechteinhaber
und werden ausschließlich zur Identifikation verwendet.<br><br>

• <b>Pokémon TCG</b> — Markenzeichen von Nintendo / The Pokémon Company International.<br><br>

Alle weiteren Kartenspiel-Markenzeichen sind Eigentum ihrer jeweiligen Inhaber.<br><br>

<b>3. Lizenz (eigener Code)</b><br>
CardLens-eigener Quellcode steht unter der <b>MIT-Lizenz</b>.<br>
Drittanbieter-Bibliotheken unterliegen ihren eigenen Lizenzen (LGPL, Apache 2.0, BSD).<br><br>

<b>4. TCGPlayer API</b><br>
Kartenwerte (ETB, Booster Bundle) werden über die <b>TCGPlayer API</b> abgerufen.<br>
Du bist der Vertragspartner gegenüber TCGPlayer, Inc. — nicht der Entwickler dieser App.<br>
Mit der Nutzung akzeptierst du deren <a href="https://www.tcgplayer.com">Nutzungsbedingungen</a>.<br><br>

<b>5. Datenspeicherung</b><br>
Alle Daten (Sammlung, Logs) werden <b>lokal</b> auf deinem PC gespeichert.<br>
Kartenbilder und Set-Logos werden von <b>pokemontcg.io</b> geladen und lokal
in <code>%APPDATA%\CardLens\data\catalog_images\</code> gecacht, um wiederholte
Netzwerkanfragen zu vermeiden. Die Bilder gehören The Pokémon Company International
bzw. deren Lizenzgebern und werden ausschließlich zur Anzeige genutzt.<br>
Es werden keine persönlichen Daten an Dritte übermittelt.
"""


class DisclaimerDialog(QDialog):
    """Shown once on first launch. User must accept before using the app."""

    def __init__(self, current_api_key: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CardLens — Ersteinrichtung")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._accepted = False

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Disclaimer text in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.StyledPanel)
        inner = QLabel(_DISCLAIMER_TEXT)
        inner.setWordWrap(True)
        inner.setOpenExternalLinks(True)
        inner.setTextFormat(Qt.RichText)
        inner.setContentsMargins(10, 10, 10, 10)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        # API key input
        api_group = QGroupBox("pokemontcg.io API-Key (optional)")
        api_row = QHBoxLayout(api_group)
        self._api_key_input = QLineEdit(current_api_key)
        self._api_key_input.setPlaceholderText("API-Key eingeben (leer lassen für ~1000 Anfragen/Tag)")
        self._api_key_input.setEchoMode(QLineEdit.Password)
        btn_show = QPushButton("👁")
        btn_show.setFixedWidth(32)
        btn_show.setCheckable(True)
        btn_show.toggled.connect(
            lambda checked: self._api_key_input.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        reg_link = QLabel('<a href="https://dev.pokemontcg.io/">Key registrieren</a>')
        reg_link.setOpenExternalLinks(True)
        api_row.addWidget(self._api_key_input, 1)
        api_row.addWidget(btn_show)
        api_row.addWidget(reg_link)
        layout.addWidget(api_group)

        # Buttons
        buttons = QDialogButtonBox()
        self._btn_accept = buttons.addButton("Akzeptieren && Starten", QDialogButtonBox.AcceptRole)
        self._btn_cancel = buttons.addButton("Abbrechen", QDialogButtonBox.RejectRole)
        self._btn_accept.setStyleSheet(
            "font-weight: bold; background-color: #2563eb; color: white; padding: 4px 16px;"
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self._accepted = True
        self.accept()

    @property
    def accepted_disclaimer(self) -> bool:
        return self._accepted

    @property
    def api_key(self) -> str:
        return self._api_key_input.text().strip()


class ApiKeyDialog(QDialog):
    """Settings dialog: API key + app preferences."""

    def __init__(self, current_api_key: str = "", start_maximized: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("⚙ Einstellungen")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Trage deinen <b>pokemontcg.io API-Key</b> ein, um das Anfragen-Limit auf "
            "20.000 pro Tag zu erhöhen (kostenlos).<br>"
            "Leer lassen = ~1.000 Anfragen/Tag ohne Key.<br><br>"
            'Registrierung: <a href="https://dev.pokemontcg.io/">https://dev.pokemontcg.io/</a><br>'
            'AGB: <a href="https://pokemontcg.io/terms">https://pokemontcg.io/terms</a>'
        )
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setTextFormat(Qt.RichText)
        layout.addWidget(info)

        row = QHBoxLayout()
        self._input = QLineEdit(current_api_key)
        self._input.setPlaceholderText("API-Key (leer = kein Key)")
        self._input.setEchoMode(QLineEdit.Password)
        btn_show = QPushButton("👁")
        btn_show.setFixedWidth(32)
        btn_show.setCheckable(True)
        btn_show.toggled.connect(
            lambda checked: self._input.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        row.addWidget(self._input, 1)
        row.addWidget(btn_show)
        layout.addLayout(row)

        # ── App preferences ──────────────────────────────────────────────────
        self._cb_maximized = QCheckBox("App immer maximiert starten")
        self._cb_maximized.setChecked(start_maximized)
        layout.addWidget(self._cb_maximized)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def api_key(self) -> str:
        return self._input.text().strip()

    @property
    def start_maximized(self) -> bool:
        return self._cb_maximized.isChecked()


class AboutDialog(QDialog):
    """About dialog with version, license summary and third-party notices."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Über CardLens {_APP_VERSION}")
        self.setMinimumWidth(540)
        self.setMinimumHeight(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        title = QLabel(f"<h2>CardLens</h2><b>Version {_APP_VERSION}</b>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        tagline = QLabel(
            "Desktop-App zur Verwaltung und Bewertung von TCG-Karten.<br>"
            "Eigener Quellcode lizenziert unter der <b>MIT-Lizenz</b>."
        )
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setWordWrap(True)
        tagline.setTextFormat(Qt.RichText)
        layout.addWidget(tagline)

        # Source code link
        src_label = QLabel(
            'Quellcode (MIT-Lizenz): '
            '<a href="https://github.com/webster586/cardlens">'
            "github.com/webster586/cardlens</a>"
        )
        src_label.setAlignment(Qt.AlignCenter)
        src_label.setOpenExternalLinks(True)
        src_label.setTextFormat(Qt.RichText)
        layout.addWidget(src_label)

        # Third-party licenses in scroll area
        tp_group = QGroupBox("Drittanbieter-Bibliotheken & Lizenzen")
        tp_layout = QVBoxLayout(tp_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        tp_label = QLabel(_THIRD_PARTY)
        tp_label.setWordWrap(True)
        tp_label.setOpenExternalLinks(True)
        tp_label.setTextFormat(Qt.RichText)
        tp_label.setContentsMargins(8, 8, 8, 8)
        scroll.setWidget(tp_label)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tp_layout.addWidget(scroll)
        layout.addWidget(tp_group, 1)

        # LGPL note
        lgpl_note = QLabel(
            "<i>PySide6/Qt ist unter LGPL v3 lizenziert. Der Quellcode dieser App ist "
            "öffentlich verfügbar, damit Nutzer PySide6 austauschen können (LGPL-Pflicht).</i>"
        )
        lgpl_note.setWordWrap(True)
        lgpl_note.setTextFormat(Qt.RichText)
        lgpl_note.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(lgpl_note)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _md_to_html(md: str) -> str:
    """Minimal Markdown → HTML conversion for CHANGELOG display."""
    lines = md.splitlines()
    html_lines: list[str] = []
    in_ul = False
    for raw in lines:
        line = raw.rstrip()
        # Heading levels
        if line.startswith("## "):
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            html_lines.append(f"<h2 style='color:#60a5fa;margin-top:16px;margin-bottom:2px;'>{line[3:]}</h2>")
            continue
        if line.startswith("### "):
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            html_lines.append(f"<h3 style='color:#94a3b8;margin-top:8px;margin-bottom:2px;'>{line[4:]}</h3>")
            continue
        if line.startswith("# "):
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            html_lines.append(f"<h1 style='color:#e2e8f0;'>{line[2:]}</h1>")
            continue
        # Bullet
        if line.startswith("- "):
            if not in_ul:
                html_lines.append("<ul style='margin:2px 0 2px 16px;'>")
                in_ul = True
            item = _inline_md(line[2:])
            html_lines.append(f"<li style='margin:1px 0;'>{item}</li>")
            continue
        # Close list on blank / other line
        if in_ul:
            html_lines.append("</ul>")
            in_ul = False
        if line == "":
            html_lines.append("<br>")
        else:
            html_lines.append(f"<p style='margin:2px 0;'>{_inline_md(line)}</p>")
    if in_ul:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _inline_md(text: str) -> str:
    """Convert inline **bold** and `code` in a line."""
    # backtick code
    text = re.sub(r"`([^`]+)`", r"<code style='background:#1e293b;padding:1px 4px;border-radius:3px;'>\1</code>", text)
    # bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    return text


class ChangelogDialog(QDialog):
    """Scrollable Changelog / Patchnotes viewer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Changelog / Patchnotes")
        self.setMinimumWidth(700)
        self.setMinimumHeight(640)
        self.setStyleSheet("background:#0f172a; color:#e2e8f0;")

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        from src.pokemon_scanner.core.paths import PROJECT_ROOT
        changelog_path = PROJECT_ROOT / "CHANGELOG.md"
        if changelog_path.exists():
            md_text = changelog_path.read_text(encoding="utf-8")
            html = _md_to_html(md_text)
        else:
            html = "<i>CHANGELOG.md nicht gefunden.</i>"

        browser = QTextBrowser()
        browser.setHtml(
            f"<html><body style='background:#0f172a;color:#e2e8f0;"
            f"font-family:Segoe UI,Arial,sans-serif;font-size:13px;padding:8px;'>"
            f"{html}</body></html>"
        )
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet(
            "QTextBrowser { background:#0f172a; border:1px solid #334155;"
            " border-radius:4px; color:#e2e8f0; }"
            "QScrollBar:vertical { background:#1e293b; width:10px; }"
            "QScrollBar::handle:vertical { background:#334155; border-radius:5px; }"
        )
        layout.addWidget(browser, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).setStyleSheet(
            "background:#252741; border:1px solid #334155; border-radius:4px;"
            " color:#e2e8f0; padding:4px 16px;"
        )
        layout.addWidget(buttons)

