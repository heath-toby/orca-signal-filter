# Orca Signal Filter

An [Orca screen reader](https://orca.gnome.org/) add-on that makes
[Signal Desktop](https://signal.org/download/) pleasant to use without sight.

Out of the box, Signal Desktop under Orca is noisy and uninformative: its
`aria-live` regions fire constantly, every new message is double-spoken with the
timestamp glued onto the end (`"…warm butter  Now"` then `"…warm butter"`), and
there is no spoken cue when a message is sent, received, or when the other person
is typing. This add-on fixes all of that.

## What it does

1. **Silences the live-region spam.** All of Signal's noisy automatic Orca
   announcements are suppressed, so you no longer hear duplicated or
   timestamp-garbled speech.
2. **Announces messages cleanly**, exactly once each:
   - `Message sent.` when you send.
   - `Daniel: <text>` (or `<sender>: <text>` in groups) when one arrives.
3. **Announces typing**, group-aware:
   - 1:1: `Daniel is typing.`
   - group: names who is typing from the typing bubble's avatars —
     `Alex and Bob are typing.`, `Alex, Bob and Carol are typing.`,
     `Alex and others are typing.`
   - and, when the indicator disappears without a message being sent,
     `Daniel stopped typing.` (toggleable separately).

It does this by reading Signal's **actual rendered DOM markers** through
AT-SPI (message direction from `module-message--outgoing/incoming`, clean text
from `module-message__text`, the per-message id for de-duplication, the typing
bubble from `module-typing-animation`), rather than parsing Orca's messy
live-region text. Conversation switches and history scroll-back are primed
silently so they are never re-announced. Verified against Signal Desktop 8.13.0.

### Bonus: read receipts (dormant)

The add-on also contains a reader for delivery status (`Read by Daniel.`), but on
stock Signal it stays silent: Signal renders the status tick as an unlabeled,
CSS-only `<div>` that Chromium prunes from the accessibility tree, so there is
nothing for any screen reader to read. The reader lights up automatically the
moment that element gains an accessible label — for example via the upstream
[Signal Desktop accessibility change](https://github.com/signalapp/Signal-Desktop)
that adds `aria-label` to the status indicator.

## Requirements

- Orca (GNOME's screen reader)
- Signal Desktop
- Python 3, PyGObject (`gi`), GLib/GSettings — already present on a typical
  Orca install.

## Install

```bash
./install.sh
orca --replace &        # restart Orca to load the add-on
```

The installer copies the module into `~/.local/share/orca/`, registers a
GSettings schema, and adds a loader to your `orca-customizations.py`.

## Uninstall

```bash
./uninstall.sh
orca --replace &
```

## Settings

Press **Orca + Shift + S** at any time to toggle: enabled, announce sent,
announce typing, announce received, the de-duplication window, and a debug log
(written to `~/.local/share/orca/signal_filter/debug.log`).

## How it works

The add-on monkey-patches Orca's live-region presenter to drop Signal's
automatic announcements, and registers its own debounced `object:children-changed`
AT-SPI listener that produces clean, de-duplicated speech from the DOM markers
above. Typing is detected directly from the mutation event (the added node *is*
the typing bubble), so the transient indicator is never missed.

## Credits

Built collaboratively with AI assistance, designed and tested by a daily Orca +
Signal user. Contributions and bug reports welcome.

## License

MIT — see [LICENSE](LICENSE).
