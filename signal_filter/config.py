"""Configuration for Orca Signal Filter.

Values live in Orca's own per-extension settings store (dconf, under
``/org/gnome/orca/<profile>/extensions/signal_filter/settings``), which is
what the Orca 51 extension system reads and writes. Signal Filter used to
keep them in a private GSettings schema, ``org.gnome.Orca.SignalFilter``;
settings from that schema are imported once, automatically, on first run
-- see ``migrate_legacy_settings``.

Every setting here is a plain toggle or a number, so unlike Orca's other
add-ons Signal Filter has no dialog of its own: it declares these to Orca
and gets the settings pane in Orca Preferences -> User Extensions.
"""

from __future__ import annotations

import logging
import os

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

_log = logging.getLogger("orca-signal-filter")

# The private schema Signal Filter used before the Orca 51 extension system.
LEGACY_SCHEMA_ID = "org.gnome.Orca.SignalFilter"

# Bumped when the shape of the stored settings changes. Its presence also
# marks the legacy import as done, so it never runs twice -- including
# when the user has deliberately reset everything back to defaults.
SETTINGS_VERSION = 2
_VERSION_KEY = "settings-version"

XDG_DATA_HOME = os.environ.get(
    "XDG_DATA_HOME", os.path.expanduser("~/.local/share")
)
ORCA_DIR = os.path.join(XDG_DATA_HOME, "orca")

DEFAULT_ENABLED = True
DEFAULT_ANNOUNCE_SENT = True
DEFAULT_ANNOUNCE_TYPING = True
DEFAULT_ANNOUNCE_TYPING_STOPPED = True
DEFAULT_ANNOUNCE_RECEIVED = True
DEFAULT_DEDUP_SECONDS = 5
DEFAULT_DEBUG = False

DEDUP_SECONDS_MIN = 0
DEDUP_SECONDS_MAX = 60

# attribute name -> (store key, legacy getter name, default)
_KEYS = (
    ("enabled", "enabled", "bool", DEFAULT_ENABLED),
    ("announce_sent", "announce-sent", "bool", DEFAULT_ANNOUNCE_SENT),
    ("announce_typing", "announce-typing", "bool", DEFAULT_ANNOUNCE_TYPING),
    ("announce_typing_stopped", "announce-typing-stopped", "bool",
     DEFAULT_ANNOUNCE_TYPING_STOPPED),
    ("announce_received", "announce-received", "bool", DEFAULT_ANNOUNCE_RECEIVED),
    ("dedup_seconds", "dedup-seconds", "int", DEFAULT_DEDUP_SECONDS),
    ("debug", "debug", "bool", DEFAULT_DEBUG),
)


def _legacy_gsettings() -> Gio.Settings | None:
    """Return the old private-schema Gio.Settings, or None if not installed."""
    user_schema_dir = os.path.join(XDG_DATA_HOME, "glib-2.0", "schemas")
    default_source = Gio.SettingsSchemaSource.get_default()
    try:
        source = Gio.SettingsSchemaSource.new_from_directory(
            user_schema_dir, default_source, False,
        )
    except GLib.Error:
        source = default_source
    if source is None:
        return None
    schema = source.lookup(LEGACY_SCHEMA_ID, True)
    if schema is None:
        return None
    return Gio.Settings.new_full(schema, None, None)


def migrate_legacy_settings(settings) -> bool:
    """Import settings from the pre-extension GSettings schema, once.

    ``settings`` is the extension's ExtensionSettings. Returns True if
    values were actually imported. Writes the version marker either way,
    so a missing or already-migrated legacy schema costs one lookup at
    most once in the life of the install.
    """
    if settings.get(_VERSION_KEY) is not None:
        return False

    legacy = _legacy_gsettings()
    if legacy is None:
        _log.info(
            "SignalFilter: no legacy %s schema to import from; using defaults.",
            LEGACY_SCHEMA_ID,
        )
        settings.set(_VERSION_KEY, SETTINGS_VERSION)
        return False

    try:
        for _attr, key, kind, _default in _KEYS:
            getter = legacy.get_boolean if kind == "bool" else legacy.get_int
            settings.set(key, getter(key))
    except (GLib.Error, TypeError, ValueError) as error:
        # Leave the version marker unset so a later run can try again
        # rather than silently stranding the user on defaults.
        _log.error("SignalFilter: importing legacy settings failed: %s", error)
        return False

    settings.set(_VERSION_KEY, SETTINGS_VERSION)
    _log.info("SignalFilter: imported settings from the legacy %s schema.", LEGACY_SCHEMA_ID)
    return True


class Config:
    """Signal Filter configuration, backed by Orca's per-extension settings."""

    def __init__(self, settings=None):
        self._settings = settings
        self.enabled: bool = DEFAULT_ENABLED
        self.announce_sent: bool = DEFAULT_ANNOUNCE_SENT
        self.announce_typing: bool = DEFAULT_ANNOUNCE_TYPING
        self.announce_typing_stopped: bool = DEFAULT_ANNOUNCE_TYPING_STOPPED
        self.announce_received: bool = DEFAULT_ANNOUNCE_RECEIVED
        self.dedup_seconds: int = DEFAULT_DEDUP_SECONDS
        self.debug: bool = DEFAULT_DEBUG

    @classmethod
    def load(cls, settings=None) -> Config:
        """Read the current settings into a new Config."""
        cfg = cls(settings)
        cfg.reload()
        return cfg

    def reload(self) -> None:
        """Re-read every value from the settings store."""
        settings = self._settings
        if settings is None:
            _log.warning("SignalFilter: no settings store; using defaults.")
            return

        for attr, key, kind, default in _KEYS:
            value = settings.get(key, default)
            try:
                setattr(self, attr, bool(value) if kind == "bool" else int(value))
            except (TypeError, ValueError):
                _log.warning("SignalFilter: ignoring unusable value for %s: %r", key, value)
                setattr(self, attr, default)

        # A negative window would make the dedup check nonsensical rather
        # than merely disabled, so clamp rather than trust the store.
        self.dedup_seconds = min(max(self.dedup_seconds, DEDUP_SECONDS_MIN), DEDUP_SECONDS_MAX)

    def save(self) -> None:
        """Write every value back to the settings store."""
        settings = self._settings
        if settings is None:
            _log.error("SignalFilter: cannot save, no settings store.")
            return
        for attr, key, kind, _default in _KEYS:
            value = getattr(self, attr)
            settings.set(key, bool(value) if kind == "bool" else int(value))
