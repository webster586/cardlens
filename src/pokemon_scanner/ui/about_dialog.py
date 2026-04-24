"""About and first-run Disclaimer dialogs for CardLens."""
from __future__ import annotations

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
    QVBoxLayout,
    QWidget,
)

_APP_VERSION = "0.4.0"

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
<a href="https://pokemontcg.io/terms">https://pokemontcg.io/terms</a><br><br>

<b>TCGPlayer API</b><br>
Third-party web service operated by TCGPlayer, Inc. — not affiliated with Nintendo or The Pokémon Company.<br>
Used for sealed-product pricing (ETB, Booster Bundle). Subject to their Terms of Service.<br>
<a href="https://store.tcgplayer.com/legal">https://store.tcgplayer.com/legal</a><br><br>

<b>Trading Card Game Trademarks</b><br>
CardLens is not affiliated with or endorsed by any TCG publisher.<br>
All trademarks, card artworks, and logos are property of their respective owners:<br>
Pokémon TCG — Nintendo / The Pokémon Company International ·
Magic: The Gathering — Wizards of the Coast LLC (Hasbro, Inc.) ·
Yu-Gi-Oh! — Konami Digital Entertainment Co., Ltd. ·
Disney Lorcana — The Walt Disney Company / Ravensburger AG ·
One Piece Card Game / Dragon Ball Super Card Game / Digimon Card Game — Bandai Co., Ltd. ·
Flesh and Blood — Legend Story Studios Ltd. ·
Weiss Schwarz — Bushiroad Inc. ·
KeyForge — Fantasy Flight Games / Asmodee Group.
"""

_DISCLAIMER_TEXT = """\
<b>Wichtige Hinweise vor der ersten Nutzung</b><br><br>

<b>1. pokemontcg.io API</b><br>
CardLens ruft Karteninformationen und Preise über die <b>pokemontcg.io API</b> ab.<br>
Du bist der Vertragspartner gegenüber pokemontcg.io — nicht der Entwickler dieser App.<br>
Mit der Nutzung akzeptierst du deren \
<a href="https://pokemontcg.io/terms">Nutzungsbedingungen</a>.<br>
Ein eigener API-Key erhöht das Anfragen-Limit auf 20.000/Tag (kostenlos registrieren).<br><br>

<b>2. Markenzeichen der Kartenspiele (TCG)</b><br>
CardLens hat <b>keine offizielle Verbindung</b> zu den folgenden Unternehmen.<br>
Alle Markenzeichen, Karten-Artworks und Logos sind Eigentum ihrer jeweiligen Rechteinhaber
und werden ausschließlich zur Identifikation verwendet.<br><br>

• <b>Pokémon TCG</b> — Markenzeichen von Nintendo / The Pokémon Company International.<br>
• <b>Magic: The Gathering</b> — Markenzeichen von Wizards of the Coast LLC (Hasbro, Inc.).<br>
• <b>Yu-Gi-Oh!</b> — Markenzeichen von Konami Digital Entertainment Co., Ltd.<br>
• <b>Disney Lorcana</b> — Markenzeichen von The Walt Disney Company / Ravensburger AG.<br>
• <b>One Piece Card Game</b> — © Eiichiro Oda/Shueisha, Toei Animation — veröffentlicht von Bandai Co., Ltd.<br>
• <b>Dragon Ball Super Card Game</b> — © Bird Studio/Shueisha, Toei Animation — Bandai Co., Ltd.<br>
• <b>Digimon Card Game</b> — © Akiyoshi Hongo, Toei Animation — Bandai Co., Ltd.<br>
• <b>Flesh and Blood</b> — Markenzeichen von Legend Story Studios Ltd.<br>
• <b>Weiss Schwarz</b> — Markenzeichen von Bushiroad Inc.<br>
• <b>KeyForge</b> — Markenzeichen von Fantasy Flight Games / Asmodee Group.<br><br>

Alle weiteren Kartenspiel-Markenzeichen sind Eigentum ihrer jeweiligen Inhaber.<br><br>

<b>3. Lizenz (eigener Code)</b><br>
CardLens-eigener Quellcode steht unter der <b>MIT-Lizenz</b>.<br>
Drittanbieter-Bibliotheken unterliegen ihren eigenen Lizenzen (LGPL, Apache 2.0, BSD).<br><br>

<b>4. TCGPlayer API</b><br>
Kartenwerte (ETB, Booster Bundle) werden über die <b>TCGPlayer API</b> abgerufen.<br>
Du bist der Vertragspartner gegenüber TCGPlayer, Inc. — nicht der Entwickler dieser App.<br>
Mit der Nutzung akzeptierst du deren <a href="https://store.tcgplayer.com/legal">Nutzungsbedingungen</a>.<br><br>

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
