"""First-run onboarding wizard: Welcome → Disclaimer → API-Key setup."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

# ── Disclaimer text (same as about_dialog) ────────────────────────────────
_DISCLAIMER_TEXT = """\
<b>Wichtige Hinweise vor der ersten Nutzung</b><br><br>

<b>1. pokemontcg.io API</b><br>
CardLens ruft Karteninformationen und Preise über die <b>pokemontcg.io API</b> ab.<br>
Du bist der Vertragspartner gegenüber pokemontcg.io — nicht der Entwickler dieser App.<br>
Mit der Nutzung akzeptierst du deren \
<a href="https://pokemontcg.io/terms">Nutzungsbedingungen</a>.<br>
Ein eigener API-Key erhöht das Anfragen-Limit auf 20.000/Tag (kostenlos registrieren).<br><br>

<b>2. Markenzeichen</b><br>
CardLens hat <b>keine offizielle Verbindung</b> zu Nintendo / The Pokémon Company International.<br>
Alle Markenzeichen, Karten-Artworks und Logos sind Eigentum ihrer jeweiligen Rechteinhaber
und werden ausschließlich zur Identifikation verwendet.<br><br>

• <b>Pokémon TCG</b> — Markenzeichen von Nintendo / The Pokémon Company International.<br><br>

<b>3. Lizenz (eigener Code)</b><br>
CardLens-eigener Quellcode steht unter der <b>MIT-Lizenz</b>.<br>
Drittanbieter-Bibliotheken unterliegen ihren eigenen Lizenzen (LGPL, Apache 2.0, BSD).<br><br>

<b>4. TCGPlayer API</b><br>
Kartenwerte (ETB, Booster Bundle) werden über die <b>TCGPlayer API</b> abgerufen.<br>
Du bist der Vertragspartner gegenüber TCGPlayer, Inc. — nicht der Entwickler dieser App.<br>
Mit der Nutzung akzeptierst du deren <a href="https://store.tcgplayer.com/legal">Nutzungsbedingungen</a>.<br><br>

<b>5. Datenspeicherung</b><br>
Alle Daten (Sammlung, Logs) werden <b>lokal</b> auf deinem PC gespeichert.<br>
Keine Telemetrie, kein Cloud-Sync.<br>
Kartenbilder werden von pokemontcg.io geladen und lokal in<br>
<code>%APPDATA%\\CardLens\\data\\catalog_images\\</code> gecacht.
"""


# ── Shared icon pixmap (re-used from splash) ──────────────────────────────

def _make_logo_pixmap(size: int = 56) -> QPixmap:
    from src.pokemon_scanner.ui.splash import _make_splash_icon
    return _make_splash_icon(size)


# ── Page 1: Welcome ──────────────────────────────────────────────────────

class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Willkommen bei CardLens")
        self.setSubTitle(
            "Der smarte TCG-Kartenscanner für deine Sammlung."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(16)

        # Logo row
        logo_row = QHBoxLayout()
        logo_row.addStretch()
        logo_lbl = QLabel()
        logo_lbl.setPixmap(_make_logo_pixmap(72))
        logo_lbl.setFixedSize(72, 72)
        logo_row.addWidget(logo_lbl)
        logo_row.addStretch()
        layout.addLayout(logo_row)

        # Feature list
        features_lbl = QLabel(
            "<b>Was CardLens kann:</b><br><br>"
            "📷 &nbsp;Karten per Kamera oder Foto scannen<br>"
            "🔍 &nbsp;Automatische Erkennung via OCR + pokemontcg.io<br>"
            "💰 &nbsp;Aktuelle Marktpreise (Cardmarket / TCGPlayer)<br>"
            "📚 &nbsp;Sammlung verwalten, Alben anlegen, Statistiken<br>"
            "📤 &nbsp;Export als CSV, JSON oder Excel<br>"
        )
        features_lbl.setTextFormat(Qt.RichText)
        features_lbl.setWordWrap(True)
        features_lbl.setStyleSheet("font-size: 13px; line-height: 1.6;")
        layout.addWidget(features_lbl)

        layout.addStretch()

        note = QLabel(
            "Klicke auf <b>Weiter</b>, um die Nutzungsbedingungen zu lesen."
        )
        note.setTextFormat(Qt.RichText)
        note.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(note)


# ── Page 2: Disclaimer ────────────────────────────────────────────────────

class _DisclaimerPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Lizenz & Nutzungsbedingungen")
        self.setSubTitle(
            "Bitte lese und akzeptiere die folgenden Hinweise."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 10)
        layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        inner = QLabel(_DISCLAIMER_TEXT)
        inner.setWordWrap(True)
        inner.setOpenExternalLinks(True)
        inner.setTextFormat(Qt.RichText)
        inner.setContentsMargins(10, 10, 10, 10)
        inner.setStyleSheet("font-size: 12px;")
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        self._accept_cb = QCheckBox(
            "Ich habe die Hinweise gelesen und akzeptiere die Bedingungen."
        )
        self._accept_cb.toggled.connect(self.completeChanged)
        layout.addWidget(self._accept_cb)

        # Register a field so the wizard can validate via isComplete()
        self.registerField("disclaimer_accepted*", self._accept_cb)

    def isComplete(self) -> bool:
        return self._accept_cb.isChecked()


# ── Page 3: API Key ───────────────────────────────────────────────────────

class _ApiKeyPage(QWizardPage):
    def __init__(self, current_api_key: str = "") -> None:
        super().__init__()
        self.setTitle("API-Key einrichten (optional)")
        self.setSubTitle(
            "Ein kostenloser pokemontcg.io API-Key erhöht das Anfragen-Limit auf 20.000/Tag."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(12)

        info = QLabel(
            "<b>Ohne Key:</b> ~1.000 Anfragen pro Tag — für normale Nutzung ausreichend.<br>"
            "<b>Mit eigenem Key:</b> 20.000 Anfragen/Tag (kostenlos).<br><br>"
            'Kostenlos registrieren: '
            '<a href="https://dev.pokemontcg.io/">dev.pokemontcg.io</a>'
        )
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setTextFormat(Qt.RichText)
        info.setStyleSheet("font-size: 13px;")
        layout.addWidget(info)

        key_group = QGroupBox("pokemontcg.io API-Key")
        key_layout = QHBoxLayout(key_group)
        self._key_input = QLineEdit(current_api_key)
        self._key_input.setPlaceholderText("Leer lassen für Nutzung ohne Key")
        self._key_input.setEchoMode(QLineEdit.Password)
        btn_show = QPushButton("👁")
        btn_show.setFixedWidth(34)
        btn_show.setCheckable(True)
        btn_show.setToolTip("Key anzeigen / verstecken")
        btn_show.toggled.connect(
            lambda checked: self._key_input.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        key_layout.addWidget(self._key_input, 1)
        key_layout.addWidget(btn_show)
        layout.addWidget(key_group)

        note = QLabel(
            "Den Key kannst du jederzeit unter <b>Hilfe → API-Schlüssel konfigurieren</b> ändern."
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.RichText)
        note.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(note)

        layout.addStretch()

    @property
    def api_key(self) -> str:
        return self._key_input.text().strip()


# ── Wizard ────────────────────────────────────────────────────────────────

PAGE_WELCOME = 0
PAGE_DISCLAIMER = 1
PAGE_API_KEY = 2


class OnboardingWizard(QWizard):
    """3-step first-run setup wizard: Welcome → Disclaimer → API Key."""

    def __init__(self, current_api_key: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CardLens — Ersteinrichtung")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(560, 500)

        # Remove the default Help button
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.HaveHelpButton, False)
        self.setButtonText(QWizard.NextButton, "Weiter →")
        self.setButtonText(QWizard.BackButton, "← Zurück")
        self.setButtonText(QWizard.FinishButton, "Fertig — CardLens starten")
        self.setButtonText(QWizard.CancelButton, "Abbrechen")

        self._welcome_page = _WelcomePage()
        self._disclaimer_page = _DisclaimerPage()
        self._api_key_page = _ApiKeyPage(current_api_key)

        self.addPage(self._welcome_page)
        self.addPage(self._disclaimer_page)
        self.addPage(self._api_key_page)

    @property
    def api_key(self) -> str:
        return self._api_key_page.api_key
