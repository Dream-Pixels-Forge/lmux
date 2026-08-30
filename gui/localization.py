"""Localization system for lmux — support for multiple languages."""
import json
import os
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("lmux.localization")

# Default locale directory
LOCALE_DIR = Path(__file__).parent / "locales"

# Default strings (English)
DEFAULT_STRINGS: Dict[str, str] = {
    # Menu items
    "menu.file": "File",
    "menu.file.new_workspace": "New Workspace",
    "menu.file.settings": "Settings",
    "menu.file.quit": "Quit",
    "menu.terminal": "Terminal",
    "menu.terminal.split_vertical": "Split Vertical",
    "menu.terminal.split_horizontal": "Split Horizontal",
    "menu.terminal.close_pane": "Close Pane",
    "menu.terminal.new_tab": "New Tab",
    "menu.terminal.close_tab": "Close Tab",
    "menu.view": "View",
    "menu.view.sidebar": "Sidebar",
    "menu.view.command_palette": "Command Palette",
    "menu.view.browser_panel": "Browser Panel",
    "menu.view.canvas_layout": "Canvas Layout",
    "menu.tools": "Tools",
    "menu.tools.import_browser": "Import from Browser",
    
    # Sidebar
    "sidebar.title": "Workspaces",
    "sidebar.create": "Create Workspace",
    "sidebar.no_workspaces": "No workspaces yet",
    
    # Workspace groups
    "groups.title": "Workspace Groups",
    "groups.create": "Create Group",
    "groups.add_workspace": "Add Workspace",
    "groups.remove_workspace": "Remove Workspace",
    "groups.empty": "No groups yet",
    
    # Browser
    "browser.navigate": "Navigate",
    "browser.reload": "Reload",
    "browser.back": "Back",
    "browser.forward": "Forward",
    "browser.open_external": "Open in External Browser",
    "browser.developer_tools": "Developer Tools",
    
    # Settings
    "settings.title": "lmux Settings",
    "settings.font_family": "Font Family",
    "settings.font_size": "Font Size",
    "settings.theme": "Theme",
    "settings.opacity": "Opacity",
    "settings.scrollback_lines": "Scrollback Lines",
    "settings.audible_bell": "Audible Bell",
    "settings.save": "Save",
    "settings.cancel": "Cancel",
    
    # Command palette
    "palette.title": "Command Palette",
    "palette.placeholder": "Type a command...",
    
    # Dialogs
    "dialog.cancel": "Cancel",
    "dialog.ok": "OK",
    "dialog.yes": "Yes",
    "dialog.no": "No",
    "dialog.close": "Close",
    
    # Errors
    "error.title": "Error",
    "error.connection_failed": "Connection failed",
    "error.workspace_not_found": "Workspace not found",
    "error.surface_not_found": "Surface not found",
    "error.pane_not_found": "Pane not found",
    
    # Success
    "success.title": "Success",
    "success.workspace_created": "Workspace created",
    "success.workspace_closed": "Workspace closed",
    "success.import_complete": "Import Complete",
    
    # Update
    "update.title": "Update Available",
    "update.version": "Update available: lmux {version}",
    "update.install": "Install",
    "update.skip": "Skip This Version",
    "update.dismiss": "Dismiss",
    
    # General
    "general.loading": "Loading...",
    "general.ready": "Ready",
    "general.done": "Done",
    "general.error": "Error",
    "general.warning": "Warning",
    "general.info": "Information",
}


class Localization:
    """Manages localization strings for lmux."""

    def __init__(self, locale: str = None):
        self._locale = locale or self._detect_locale()
        self._strings: Dict[str, str] = DEFAULT_STRINGS.copy()
        self._load_locale_strings()

    def _detect_locale(self) -> str:
        """Detect the system locale."""
        # Check environment variables
        for var in ("LMUX_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG"):
            locale = os.environ.get(var, "")
            if locale:
                # Extract language code (e.g., "en_US.UTF-8" -> "en")
                return locale.split("_")[0].split(".")[0]
        return "en"

    def _load_locale_strings(self):
        """Load locale strings from file."""
        locale_file = LOCALE_DIR / f"{self._locale}.json"
        if not locale_file.exists():
            # Try with full locale (e.g., "en_US")
            locale_file = LOCALE_DIR / f"{self._locale}.json"
        
        if locale_file.exists():
            try:
                with open(locale_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._strings.update(data)
                logger.info(f"Loaded locale: {self._locale}")
            except Exception as e:
                logger.warning(f"Failed to load locale {self._locale}: {e}")

    def get(self, key: str, **kwargs) -> str:
        """Get a localized string by key."""
        template = self._strings.get(key, key)
        if kwargs:
            try:
                return template.format(**kwargs)
            except (KeyError, IndexError):
                return template
        return template

    def set_locale(self, locale: str):
        """Change the current locale."""
        self._locale = locale
        self._strings = DEFAULT_STRINGS.copy()
        self._load_locale_strings()

    def get_locale(self) -> str:
        """Get the current locale."""
        return self._locale

    def get_available_locales(self) -> list:
        """Get list of available locales."""
        locales = []
        if LOCALE_DIR.exists():
            for f in LOCALE_DIR.glob("*.json"):
                locales.append(f.stem)
        return sorted(locales)

    def export_locale(self, locale: str, path: Path):
        """Export a locale to a file."""
        # Start with default strings
        strings = DEFAULT_STRINGS.copy()
        
        # Override with locale-specific strings
        locale_file = LOCALE_DIR / f"{locale}.json"
        if locale_file.exists():
            with open(locale_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            strings.update(data)
        
        # Write to file
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(strings, f, indent=2, ensure_ascii=False)

    def create_locale(self, locale: str, translations: Dict[str, str]):
        """Create or update a locale."""
        locale_file = LOCALE_DIR / f"{locale}.json"
        locale_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing strings if any
        existing = {}
        if locale_file.exists():
            with open(locale_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        
        # Merge translations
        existing.update(translations)
        
        # Write back
        with open(locale_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)


# Singleton instance
_localization: Optional[Localization] = None


def get_localization() -> Localization:
    """Get the singleton localization instance."""
    global _localization
    if _localization is None:
        _localization = Localization()
    return _localization


def _(key: str, **kwargs) -> str:
    """Shorthand for getting a localized string."""
    return get_localization().get(key, **kwargs)


def set_locale(locale: str):
    """Set the current locale."""
    get_localization().set_locale(locale)


def get_locale() -> str:
    """Get the current locale."""
    return get_localization().get_locale()


# Create default locale files
def create_default_locales():
    """Create default locale files for common languages."""
    locales = {
        "en": DEFAULT_STRINGS,
        "fr": {
            "menu.file": "Fichier",
            "menu.file.new_workspace": "Nouvel espace de travail",
            "menu.file.settings": "Paramètres",
            "menu.file.quit": "Quitter",
            "menu.terminal": "Terminal",
            "menu.terminal.split_vertical": "Division verticale",
            "menu.terminal.split_horizontal": "Division horizontale",
            "menu.terminal.close_pane": "Fermer le panneau",
            "menu.terminal.new_tab": "Nouvel onglet",
            "menu.terminal.close_tab": "Fermer l'onglet",
            "menu.view": "Affichage",
            "menu.view.sidebar": "Barre latérale",
            "menu.view.command_palette": "Palette de commandes",
            "menu.view.browser_panel": "Panneau du navigateur",
            "menu.view.canvas_layout": "Disposition du canevas",
            "menu.tools": "Outils",
            "menu.tools.import_browser": "Importer depuis le navigateur",
            "sidebar.title": "Espaces de travail",
            "sidebar.create": "Créer un espace de travail",
            "sidebar.no_workspaces": "Pas encore d'espaces de travail",
            "groups.title": "Groupes d'espaces de travail",
            "groups.create": "Créer un groupe",
            "groups.add_workspace": "Ajouter un espace de travail",
            "groups.remove_workspace": "Supprimer l'espace de travail",
            "groups.empty": "Pas encore de groupes",
            "browser.navigate": "Naviguer",
            "browser.reload": "Recharger",
            "browser.back": "Retour",
            "browser.forward": "Avancer",
            "browser.open_external": "Ouvrir dans le navigateur externe",
            "browser.developer_tools": "Outils de développement",
            "settings.title": "Paramètres lmux",
            "settings.font_family": "Famille de polices",
            "settings.font_size": "Taille de police",
            "settings.theme": "Thème",
            "settings.opacity": "Opacité",
            "settings.scrollback_lines": "Lignes de défilement",
            "settings.audible_bell": "Sonnerie audible",
            "settings.save": "Enregistrer",
            "settings.cancel": "Annuler",
            "palette.title": "Palette de commandes",
            "palette.placeholder": "Tapez une commande...",
            "dialog.cancel": "Annuler",
            "dialog.ok": "OK",
            "dialog.yes": "Oui",
            "dialog.no": "Non",
            "dialog.close": "Fermer",
            "error.title": "Erreur",
            "error.connection_failed": "Échec de la connexion",
            "error.workspace_not_found": "Espace de travail introuvable",
            "error.surface_not_found": "Surface introuvable",
            "error.pane_not_found": "Panneau introuvable",
            "success.title": "Succès",
            "success.workspace_created": "Espace de travail créé",
            "success.workspace_closed": "Espace de travail fermé",
            "success.import_complete": "Importation terminée",
            "update.title": "Mise à jour disponible",
            "update.version": "Mise à jour disponible: lmux {version}",
            "update.install": "Installer",
            "update.skip": "Ignorer cette version",
            "update.dismiss": "Fermer",
            "general.loading": "Chargement...",
            "general.ready": "Prêt",
            "general.done": "Terminé",
            "general.error": "Erreur",
            "general.warning": "Avertissement",
            "general.info": "Information",
        },
        "de": {
            "menu.file": "Datei",
            "menu.file.new_workspace": "Neuer Arbeitsbereich",
            "menu.file.settings": "Einstellungen",
            "menu.file.quit": "Beenden",
            "menu.terminal": "Terminal",
            "menu.terminal.split_vertical": "Vertikal teilen",
            "menu.terminal.split_horizontal": "Horizontal teilen",
            "menu.terminal.close_pane": "Bereich schließen",
            "menu.terminal.new_tab": "Neuer Tab",
            "menu.terminal.close_tab": "Tab schließen",
            "menu.view": "Ansicht",
            "menu.view.sidebar": "Seitenleiste",
            "menu.view.command_palette": "Befehlspalette",
            "menu.view.browser_panel": "Browser-Bereich",
            "menu.view.canvas_layout": "Canvas-Layout",
            "menu.tools": "Werkzeuge",
            "menu.tools.import_browser": "Aus Browser importieren",
            "sidebar.title": "Arbeitsbereiche",
            "sidebar.create": "Arbeitsbereich erstellen",
            "sidebar.no_workspaces": "Noch keine Arbeitsbereiche",
            "groups.title": "Arbeitsbereichsgruppen",
            "groups.create": "Gruppe erstellen",
            "groups.add_workspace": "Arbeitsbereich hinzufügen",
            "groups.remove_workspace": "Arbeitsbereich entfernen",
            "groups.empty": "Noch keine Gruppen",
            "browser.navigate": "Navigieren",
            "browser.reload": "Neu laden",
            "browser.back": "Zurück",
            "browser.forward": "Vorwärts",
            "browser.open_external": "Im externen Browser öffnen",
            "browser.developer_tools": "Entwicklertools",
            "settings.title": "lmux Einstellungen",
            "settings.font_family": "Schriftfamilie",
            "settings.font_size": "Schriftgröße",
            "settings.theme": "Thema",
            "settings.opacity": "Deckkraft",
            "settings.scrollback_lines": "Scrollzeilen",
            "settings.audible_bell": "Hörbare Glocke",
            "settings.save": "Speichern",
            "settings.cancel": "Abbrechen",
            "palette.title": "Befehlspalette",
            "palette.placeholder": "Befehl eingeben...",
            "dialog.cancel": "Abbrechen",
            "dialog.ok": "OK",
            "dialog.yes": "Ja",
            "dialog.no": "Nein",
            "dialog.close": "Schließen",
            "error.title": "Fehler",
            "error.connection_failed": "Verbindung fehlgeschlagen",
            "error.workspace_not_found": "Arbeitsbereich nicht gefunden",
            "error.surface_not_found": "Oberfläche nicht gefunden",
            "error.pane_not_found": "Bereich nicht gefunden",
            "success.title": "Erfolg",
            "success.workspace_created": "Arbeitsbereich erstellt",
            "success.workspace_closed": "Arbeitsbereich geschlossen",
            "success.import_complete": "Import abgeschlossen",
            "update.title": "Update verfügbar",
            "update.version": "Update verfügbar: lmux {version}",
            "update.install": "Installieren",
            "update.skip": "Diese Version überspringen",
            "update.dismiss": "Schließen",
            "general.loading": "Laden...",
            "general.ready": "Bereit",
            "general.done": "Fertig",
            "general.error": "Fehler",
            "general.warning": "Warnung",
            "general.info": "Information",
        },
    }
    
    for locale, strings in locales.items():
        locale_file = LOCALE_DIR / f"{locale}.json"
        locale_file.parent.mkdir(parents=True, exist_ok=True)
        with open(locale_file, "w", encoding="utf-8") as f:
            json.dump(strings, f, indent=2, ensure_ascii=False)


# Create default locales on import
create_default_locales()
