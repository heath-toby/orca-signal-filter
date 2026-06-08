"""GSettings-backed configuration for Orca Signal Filter."""

from __future__ import annotations

import logging
import os

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

_log = logging.getLogger("orca-signal-filter")

SCHEMA_ID = "org.gnome.Orca.SignalFilter"

XDG_DATA_HOME = os.environ.get(
    "XDG_DATA_HOME", os.path.expanduser("~/.local/share")
)
ORCA_DIR = os.path.join(XDG_DATA_HOME, "orca")


def _get_schema_source():
    """Get a GSettings schema source that includes the user schema dir."""
    user_schema_dir = os.path.join(XDG_DATA_HOME, "glib-2.0", "schemas")
    default_source = Gio.SettingsSchemaSource.get_default()
    try:
        return Gio.SettingsSchemaSource.new_from_directory(
            user_schema_dir, default_source, False,
        )
    except GLib.Error:
        return default_source


class Config:
    """Signal Filter configuration backed by GSettings."""

    def __init__(self):
        self.enabled: bool = True
        self.announce_sent: bool = True
        self.announce_typing: bool = True
        self.announce_received: bool = True
        self.dedup_seconds: int = 5
        self.debug: bool = False
        self._settings: Gio.Settings | None = None

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        cfg._init_gsettings()
        return cfg

    def _init_gsettings(self):
        source = _get_schema_source()
        schema = source.lookup(SCHEMA_ID, True)
        if schema is None:
            _log.warning(
                "SignalFilter: GSettings schema %s not found, using defaults",
                SCHEMA_ID,
            )
            return
        self._settings = Gio.Settings.new_full(schema, None, None)
        self.enabled = self._settings.get_boolean("enabled")
        self.announce_sent = self._settings.get_boolean("announce-sent")
        self.announce_typing = self._settings.get_boolean("announce-typing")
        self.announce_received = self._settings.get_boolean("announce-received")
        self.dedup_seconds = self._settings.get_int("dedup-seconds")
        self.debug = self._settings.get_boolean("debug")

    def save(self):
        if self._settings is None:
            _log.error("SignalFilter: cannot save, GSettings not available")
            return
        self._settings.set_boolean("enabled", self.enabled)
        self._settings.set_boolean("announce-sent", self.announce_sent)
        self._settings.set_boolean("announce-typing", self.announce_typing)
        self._settings.set_boolean("announce-received", self.announce_received)
        self._settings.set_int("dedup-seconds", self.dedup_seconds)
        self._settings.set_boolean("debug", self.debug)
