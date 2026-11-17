"""Orca Signal Filter: clean announcements for Signal Desktop.

Orca 51 user extension. Install to $XDG_DATA_HOME/orca/extensions/ and
approve with ``orca --approve-extension signal_filter``; Orca re-checks
the hash of every file in the package on each start, so re-approve after
editing.

Unlike Orca's other add-ons this one has no settings dialog of its own.
Every setting is a plain toggle or a number, so it declares them to Orca
and uses the pane Orca generates in Preferences -> User Extensions.
"""

__version__ = "2.0.1"

import logging

from orca import gsettings_registry
from orca.extension import Extension, ExtensionPreference

# Import submodules as `from .module import name`, never as
# `from . import module`. The loader imports us as
# orca_user_extension.<name> but never creates `orca_user_extension`
# itself, and `from . import module` goes through __import__ with the
# full dotted path, which insists on importing that absent grandparent.
from .config import (
    DEDUP_SECONDS_MAX,
    DEDUP_SECONDS_MIN,
    DEFAULT_ANNOUNCE_RECEIVED,
    DEFAULT_ANNOUNCE_SENT,
    DEFAULT_ANNOUNCE_TYPING,
    DEFAULT_ANNOUNCE_TYPING_STOPPED,
    DEFAULT_DEBUG,
    DEFAULT_DEDUP_SECONDS,
    DEFAULT_ENABLED,
    Config,
    migrate_legacy_settings,
)
from .interceptor import install as _install
from .interceptor import uninstall as _uninstall

_log = logging.getLogger("orca-signal-filter")

# Where Orca keeps per-extension settings: schema "extensions", one
# relocatable path per extension namespace, all under the single key
# "settings". The namespace is the extension's source id -- the name of
# this package -- which is the last component of __name__ once the
# loader has imported us as orca_user_extension.<namespace>.
_SETTINGS_SCHEMA = "extensions"
_SETTINGS_KEY = "settings"
_NAMESPACE = gsettings_registry.GSettingsRegistry.sanitize_gsettings_path(
    __name__.rsplit(".", 1)[-1]
)


class SignalFilter(Extension):
    """Replaces Signal Desktop's live-region noise with clean announcements."""

    GROUP_LABEL = "Signal Filter"
    DESCRIPTION = "Silences Signal Desktop's own live-region announcements and replaces them with clean typing, sent and received messages driven from the page itself."
    VERSION = "2.0.1"  # keep in sync with __version__; read by AST, must be a literal
    AUTHOR = "Toby"
    # Read by Orca without importing this module, and by Extension Updater
    # to find this add-on's releases. Orca shows it as a link in
    # Preferences -> User Extensions -> Info.
    WEBSITE = "https://github.com/heath-toby/orca-signal-filter"

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        # Kept alive deliberately: the change signal stops firing once
        # the Gio.Settings object is garbage-collected.
        self._watch = None

    def get_preferences(self) -> list[ExtensionPreference]:
        """Returns the preferences Orca's generated dialog will show."""

        return [
            ExtensionPreference.boolean(
                "enabled", "Enable Signal Desktop live region filtering", DEFAULT_ENABLED,
            ),
            ExtensionPreference.boolean(
                "announce-sent", "Announce when a message is sent", DEFAULT_ANNOUNCE_SENT,
            ),
            ExtensionPreference.boolean(
                "announce-received", "Announce received messages", DEFAULT_ANNOUNCE_RECEIVED,
            ),
            ExtensionPreference.boolean(
                "announce-typing", "Announce typing indicators", DEFAULT_ANNOUNCE_TYPING,
            ),
            ExtensionPreference.boolean(
                "announce-typing-stopped",
                "Announce when the other person stops typing",
                DEFAULT_ANNOUNCE_TYPING_STOPPED,
            ),
            ExtensionPreference.integer(
                "dedup-seconds",
                "Deduplication window in seconds",
                DEFAULT_DEDUP_SECONDS,
                DEDUP_SECONDS_MIN,
                DEDUP_SECONDS_MAX,
            ),
            ExtensionPreference.boolean(
                "debug", "Log all Signal live region events to debug file", DEFAULT_DEBUG,
            ),
        ]

    # --- Lifecycle ------------------------------------------------------

    def on_ready(self) -> None:
        """Imports legacy settings if needed, then starts filtering."""

        self._start()

    def on_enabled(self) -> None:
        """Starts filtering after a reload."""

        self._start()

    def on_disabled(self) -> None:
        """Stops filtering and restores Orca's own live region handling."""

        self._stop()

    def on_shutdown(self) -> None:
        """Deliberately does nothing.

        Orca runs shutdown hooks in a daemon thread with a timeout, but
        tearing this extension down means GLib source removal, GStreamer
        state changes and un-monkey-patching, all of which are main-thread
        work. Doing it off-thread risks a warning, an assertion or a hung
        exit -- and buys nothing, because the process is exiting anyway.
        on_disabled, which Orca does call on the main thread, still does
        the real teardown when the extension is disabled or reloaded.
        """

    def _start(self) -> None:
        if self._config is not None:
            return
        migrate_legacy_settings(self.settings)
        self._config = Config.load(self.settings)
        self._watch_settings()
        _install(self._config)

    def _stop(self) -> None:
        _uninstall()
        self._watch = None
        self._config = None

    # --- Reacting to settings changes -----------------------------------

    def _watch_settings(self) -> None:
        """Re-read settings whenever the store changes.

        This is load bearing here, more than in the other add-ons: Orca's
        generated preferences dialog is the only way Signal Filter is
        configured, and it writes settings directly without telling the
        extension anything. Without this watch every change would need an
        Orca restart.
        """
        if self._watch is not None:
            return
        try:
            registry = gsettings_registry.get_registry()
            gs = registry.get_settings(
                _SETTINGS_SCHEMA,
                registry.get_active_profile(),
                f"extensions/{_NAMESPACE}",
            )
            if gs is None:
                _log.warning("SignalFilter: no settings object to watch; changes need a reload.")
                return
            gs.connect(f"changed::{_SETTINGS_KEY}", self._on_settings_key_changed)
            self._watch = gs
        except Exception as error:  # pylint: disable=broad-exception-caught
            _log.warning(
                "SignalFilter: could not watch settings (%s); changes need a reload.", error
            )

    def _on_settings_key_changed(self, _settings, _key) -> None:
        if self._config is not None:
            self._config.reload()
            _log.info(
                "SignalFilter: settings changed (enabled=%s, sent=%s, typing=%s, received=%s)",
                self._config.enabled, self._config.announce_sent,
                self._config.announce_typing, self._config.announce_received,
            )
