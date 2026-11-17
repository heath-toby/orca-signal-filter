# Changelog

## 2.0.0

**An Orca 51 user extension, with Orca's own settings pane.**

1.x was a loose module copied into `~/.local/share/orca/` and loaded by a
block appended to `orca-customizations.py`, with its own GSettings schema and
its own hand-written settings dialog on Orca+Shift+S. All of that is gone.

### Changed

- **Installs as a user extension**, into
  `~/.local/share/orca/extensions/signal_filter/`, approved by content hash
  with `orca --approve-extension`. No loader block, and `install.sh` removes
  the old one -- left behind it would load the add-on twice.
- **Settings are declared to Orca** rather than drawn by this add-on. All
  seven are a toggle or a number, which is exactly what Orca's generated pane
  renders well, so `config_ui.py` and its 356 lines are deleted along with the
  Orca+Shift+S shortcut. Settings now live in Preferences -> User Extensions
  -> Signal Filter.
- **Settings move into Orca's per-extension store**, and the values from the
  old `org.gnome.Orca.SignalFilter` schema are imported once on first run. A
  missing or already-imported schema costs one lookup, once.
- Declares `WEBSITE`, so [Extension Updater][eu] can find releases.

### Removed

- `signal_filter/config_ui.py`, the hand-written settings dialog.
- The Orca+Shift+S keybinding. Nothing registers a command now.

[eu]: https://github.com/heath-toby/orca-extension-updater

## 1.0.0

First version. A module loaded from `orca-customizations.py`, with its own
GSettings schema and settings dialog.
