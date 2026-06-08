"""Clean, DOM-marker-driven announcements for Signal Desktop under Orca.

This add-on does two things:

  1. SILENCES Orca's own noisy auto-announcements for Signal.  Orca presents
     Signal activity through its live-region presenter, which fires on
     ``object:text-changed:insert``.  Signal emits those events twice per
     message and glues the metadata timestamp ("Now") onto the body, which is
     why stock Orca double-speaks "<text>  Now" then "<text>".  We suppress all
     Signal live-region events so Orca stays quiet here.

  2. Produces clean, deduplicated announcements of its own by watching
     ``object:children-changed`` and reading Signal's real rendered markers.

Ground truth verified against Signal Desktop 8.13.0 (live AT-SPI tree + the
shipped app.asar bundle):

  module-timeline__messages          -> the message list (role: list)
    module-message__wrapper          -> one message (role: article, "Message")
      module-message module-message--incoming|--outgoing      -> direction
      module-message__container--... id="message-accessibility-contents:<uuid>"
                                       -> stable per-message id (dedup key)
        module-message__text          -> clean body text (no timestamp)
        module-message__author         -> sender name in group chats
      module-message__metadata__date  -> "Now" / "17:25"  (the noise we drop)
      module-message__metadata__status-icon--{sending,sent,delivered,read,
                                              viewed,paused}
                                       -> read-receipt state.  NOTE: this is an
          unlabeled CSS-only <div>; Chromium prunes it from the AT-SPI tree, so
          today it is NOT readable (see _scan_status_for).  The reader below is
          written so that the instant the node is exposed -- e.g. by an
          aria-label patch to Signal, or a future Chromium change -- "Read by
          <contact>" starts working with no code change.

  Typing bubble:  a new timeline child carrying class module-typing-animation
                  with title/name "Typing animation for this chat".  There is
                  no per-typer name in 1:1, so we use the conversation header
                  contact (module-ConversationHeader__header__info__title).

All patches and listeners are reversible via uninstall().
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

from orca import (
    command_manager,
    focus_manager,
    keybindings,
    live_region_presenter,
    presentation_manager,
)
from orca.ax_object import AXObject

from .config import Config, ORCA_DIR

_log = logging.getLogger("orca-signal-filter")

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_config: Config | None = None
_installed: bool = False

_orig_handle_event = None          # saved LiveRegionPresenter.handle_event
_listener: Atspi.EventListener | None = None

_recent_source: Atspi.Accessible | None = None   # last Signal event source
_message_list: Atspi.Accessible | None = None     # cached module-timeline__messages
_cur_conv: str | None = None                      # current conversation id
_contact_name: str | None = None                  # cached header contact for _cur_conv

_seen: "OrderedDict[str, float]" = OrderedDict()  # message uuid -> first-seen ts
_status_seen: dict[str, str] = {}                 # message uuid -> last status state
_primed: bool = False
_prime_next: bool = False
_typing_active: bool = False
_typing_clear_source: int | None = None           # auto-clear typing timeout

_scan_source: int | None = None                   # pending GLib timeout id
_dedup_cache: dict[str, float] = {}               # announcement backstop

_settings_window = None

# ---------------------------------------------------------------------------
# Constants -- the real Signal markers
# ---------------------------------------------------------------------------

_SIGNAL_APP_NAMES = ("signal", "signal-desktop")

CLS_MESSAGE_LIST = "module-timeline__messages"
CLS_MSG_CONTAINER = "module-message__container"
CLS_MSG_TEXT = "module-message__text"
CLS_AUTHOR = "module-message__author"
CLS_TYPING = "module-typing-animation"
CLS_TYPING_BUBBLE = "module-message--typing-bubble"
CLS_TIMELINE = "module-timeline"  # base class of the outer timeline flex
CLS_HEADER_TITLE = "module-ConversationHeader__header__info__title"
CLS_HEADER_BTN = "module-ConversationHeader__header--clickable"
CLS_STATUS_ICON = "module-message__metadata__status-icon"
CLS_METADATA = "module-message__metadata"
ID_MSG_PREFIX = "message-accessibility-contents:"
CONV_ID_PREFIX = "conversation-"
TYPING_NAME = "typing animation"  # substring of "Typing animation for this chat"

SCAN_DELAY_MS = 180        # debounce: scan this long after the last mutation
SCAN_TAIL = 8              # how many trailing list children to inspect
ANNOUNCE_MAX = 4           # > this many new at once => treat as bulk/history load
SEEN_CAP = 1000            # bound the dedup set

# Bidi isolate / embedding controls Signal wraps names in (FSI/PDI/etc).
_ISOLATES = dict.fromkeys(
    map(ord, "⁦⁧⁨⁩‪‫‬‭‮‎‏"),
    None,
)

# Map the status-icon modifier class to a spoken state.
_STATUS_FROM_CLASS = {
    "--read": "read",
    "--viewed": "read",      # viewed (media) is also a "they saw it" state
    "--delivered": "delivered",
    "--sent": "sent",
    "--sending": None,       # in-flight, not worth announcing
    "--paused": None,
}

# Debug log
_DEBUG_LOG_DIR = os.path.join(ORCA_DIR, "signal_filter")
_DEBUG_LOG_PATH = os.path.join(_DEBUG_LOG_DIR, "debug.log")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _debug(msg: str) -> None:
    if not _config or not _config.debug:
        return
    try:
        os.makedirs(_DEBUG_LOG_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(f"{stamp} {msg}\n")
    except OSError:
        pass


def _strip(s: str | None) -> str:
    if not s:
        return ""
    return s.translate(_ISOLATES).strip()


def _cls(obj) -> str:
    try:
        return AXObject.get_attribute(obj, "class", False) or ""
    except Exception:
        return ""


def _id(obj) -> str:
    try:
        return AXObject.get_attribute(obj, "id", False) or ""
    except Exception:
        return ""


def _app_name(obj) -> str:
    try:
        app = Atspi.Accessible.get_application(obj)
        return (Atspi.Accessible.get_name(app) or "").lower() if app else ""
    except Exception:
        return ""


def _is_signal_obj(obj) -> bool:
    return _app_name(obj) in _SIGNAL_APP_NAMES


def _find_descendant(root, pred, max_depth=30, max_nodes=9000):
    """Depth-first search for the first descendant matching pred (bounded)."""
    if root is None:
        return None
    stack = [(root, 0)]
    budget = max_nodes
    while stack and budget > 0:
        node, depth = stack.pop()
        budget -= 1
        try:
            if pred(node):
                return node
        except Exception:
            pass
        if depth >= max_depth:
            continue
        try:
            cnt = AXObject.get_child_count(node)
        except Exception:
            cnt = 0
        for i in range(cnt - 1, -1, -1):
            ch = AXObject.get_child(node, i)
            if ch is not None:
                stack.append((ch, depth + 1))
    return None


def _gather_text(node) -> str:
    """Concatenate the text of all leaf descendants, in document order."""
    if node is None:
        return ""
    parts: list[str] = []
    stack = [(node, 0)]
    while stack:
        n, depth = stack.pop()
        try:
            cnt = AXObject.get_child_count(n)
        except Exception:
            cnt = 0
        if cnt == 0:
            nm = AXObject.get_name(n)
            if nm:
                parts.append(nm)
        elif depth < 10:
            for i in range(cnt - 1, -1, -1):
                ch = AXObject.get_child(n, i)
                if ch is not None:
                    stack.append((ch, depth + 1))
    return _strip(" ".join(parts))


# ---------------------------------------------------------------------------
# Locating the conversation / contact
# ---------------------------------------------------------------------------

def _root():
    """Return a Signal frame/document to search from, or None."""
    obj = _recent_source
    if obj is None or not AXObject.is_valid(obj):
        try:
            obj = focus_manager.get_manager().get_locus_of_focus()
        except Exception:
            obj = None
    if obj is None:
        return None
    frame = None
    cur = obj
    for _ in range(40):
        if cur is None:
            break
        try:
            if AXObject.get_role(cur) == Atspi.Role.FRAME:
                frame = cur
        except Exception:
            pass
        cur = AXObject.get_parent(cur)
    return frame or obj


def _conv_id_of(node) -> str | None:
    """Walk up from a node to the enclosing conversation container id."""
    cur = node
    for _ in range(25):
        if cur is None:
            break
        if _id(cur).startswith(CONV_ID_PREFIX):
            return _id(cur)
        cur = AXObject.get_parent(cur)
    return None


def _get_message_list(root):
    global _message_list
    if (_message_list is not None and AXObject.is_valid(_message_list)
            and CLS_MESSAGE_LIST in _cls(_message_list)):
        return _message_list
    # Match the list itself (role LIST), not its enclosing __container landmark,
    # which shares the "module-timeline__messages" class prefix.
    _message_list = _find_descendant(
        root,
        lambda o: AXObject.get_role(o) == Atspi.Role.LIST
        and CLS_MESSAGE_LIST in _cls(o),
    )
    return _message_list


def _timeline_root(mlist, root):
    """The outer timeline element (class first-token 'module-timeline') that
    contains BOTH the message list and the typing bubble, which Signal renders
    as a sibling of the list rather than a list child."""
    cur = mlist
    for _ in range(10):
        cur = AXObject.get_parent(cur)
        if cur is None:
            break
        toks = _cls(cur).split()
        if toks and toks[0] == CLS_TIMELINE:
            return cur
    return AXObject.get_parent(mlist) or root


def _typing_node(o) -> bool:
    c = _cls(o)
    return CLS_TYPING in c or CLS_TYPING_BUBBLE in c


def _get_contact(root) -> str | None:
    """The conversation header contact/group name (cached per conversation)."""
    global _contact_name
    if _contact_name:
        return _contact_name
    # The clickable header button's name is the contact/group name followed by
    # status verbiage, e.g. "Daniel This person is in your contacts. Verified".
    node = _find_descendant(root, lambda o: CLS_HEADER_BTN in _cls(o))
    if node is not None:
        raw = _strip(AXObject.get_name(node))
        raw = re.split(
            r"\s+(?:This (?:person|group|is)\b|Verified\b|·)", raw
        )[0].strip()
        if raw:
            _contact_name = raw
            return raw
    # Fallback: the header title container's first text leaf.
    node = _find_descendant(root, lambda o: CLS_HEADER_TITLE in _cls(o))
    if node is not None:
        name = _strip(AXObject.get_name(node)) or _gather_text(node)
        if name:
            _contact_name = name
            return name
    return None


# ---------------------------------------------------------------------------
# Reading a single message
# ---------------------------------------------------------------------------

def _message_info(container) -> dict | None:
    """Extract uuid, direction, author and clean text from a message container."""
    uuid = _id(container)
    if not uuid.startswith(ID_MSG_PREFIX):
        return None
    cls = _cls(container)
    outgoing = "--outgoing" in cls

    text_node = _find_descendant(
        container, lambda o: CLS_MSG_TEXT in _cls(o), max_depth=10, max_nodes=600
    )
    text = _gather_text(text_node) if text_node is not None else ""
    if not text:
        text = _fallback_text(container)

    author = None
    if not outgoing:
        anode = _find_descendant(
            container, lambda o: CLS_AUTHOR in _cls(o), max_depth=10, max_nodes=600
        )
        if anode is not None:
            author = _strip(AXObject.get_name(anode)) or _gather_text(anode) or None

    return {"uuid": uuid, "outgoing": outgoing, "author": author, "text": text}


def _fallback_text(container) -> str:
    """For media/sticker/attachment messages with no text node: use the
    article wrapper's composed label, minus the trailing timestamp."""
    cur = container
    for _ in range(5):
        cur = AXObject.get_parent(cur)
        if cur is None:
            break
        if "module-message__wrapper" in _cls(cur):
            nm = _strip(AXObject.get_name(cur))
            nm = re.sub(
                r"\s*(?:Now|\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)\s*$", "", nm
            ).strip()
            return nm
    return ""


def _scan_status_for(container) -> str | None:
    """Return the read-receipt state for an outgoing message, or None.

    Looks for a node whose class contains module-message__metadata__status-icon
    and maps the modifier (--read/--viewed/--delivered/--sent) to a state.

    Today this returns None because Chromium prunes the unlabeled status-icon
    <div> from the AT-SPI tree (verified on 8.13.0).  It will start returning
    real states the moment that node is exposed."""
    node = _find_descendant(
        container, lambda o: CLS_STATUS_ICON in _cls(o), max_depth=10, max_nodes=600
    )
    if node is None:
        return None
    cls = _cls(node)
    for mod, state in _STATUS_FROM_CLASS.items():
        if (CLS_STATUS_ICON + mod) in cls:
            return state
    return None


# ---------------------------------------------------------------------------
# Announcing
# ---------------------------------------------------------------------------

def _is_duplicate(text: str) -> bool:
    now = time.time()
    window = _config.dedup_seconds if _config else 5
    for k in [k for k, t in _dedup_cache.items() if now - t > window]:
        del _dedup_cache[k]
    if text in _dedup_cache:
        return True
    _dedup_cache[text] = now
    return False


def _announce(text: str) -> None:
    if not text or _is_duplicate(text):
        return
    _debug(f"PRESENT: {text!r}")
    try:
        presentation_manager.get_manager().present_message(text)
    except Exception as e:
        _debug(f"present error: {e}")


def _remember(uuid: str) -> None:
    _seen[uuid] = time.time()
    while len(_seen) > SEEN_CAP:
        _seen.popitem(last=False)


# ---------------------------------------------------------------------------
# The scan (debounced)
# ---------------------------------------------------------------------------

def _do_scan() -> None:
    global _cur_conv, _prime_next, _primed, _typing_active, _contact_name

    if not _config or not _config.enabled:
        return
    root = _root()
    if root is None or not _is_signal_obj(root):
        return
    mlist = _get_message_list(root)
    if mlist is None:
        return

    # Conversation switch? -> prime silently and reset per-conversation caches.
    conv = _conv_id_of(mlist)
    if conv != _cur_conv:
        _cur_conv = conv
        _contact_name = None
        _prime_next = True

    try:
        n = AXObject.get_child_count(mlist)
    except Exception:
        return

    new_msgs: list[dict] = []
    read_events: list[str] = []  # uuids newly read (dormant until exposed)

    # Typing bubble lives as a sibling of the message list, inside the timeline
    # container -- search the whole timeline, not just the list's children.
    tl = _timeline_root(mlist, root)
    typing_present = _find_descendant(
        tl, _typing_node, max_depth=22, max_nodes=4000
    ) is not None

    for i in range(max(0, n - SCAN_TAIL), n):
        child = AXObject.get_child(mlist, i)
        if child is None:
            continue

        # A message?
        container = _find_descendant(
            child, lambda o: _id(o).startswith(ID_MSG_PREFIX),
            max_depth=8, max_nodes=400,
        )
        if container is None:
            continue
        uuid = _id(container)

        if uuid not in _seen:
            info = _message_info(container)
            if info:
                new_msgs.append(info)

        # Read-receipt tracking for already-known outgoing messages.
        if "--outgoing" in _cls(container):
            state = _scan_status_for(container)
            if state == "read" and _status_seen.get(uuid) != "read":
                _status_seen[uuid] = "read"
                if uuid in _seen:        # don't fire on the initial prime
                    read_events.append(uuid)
            elif state and uuid not in _status_seen:
                _status_seen[uuid] = state

    _debug(f"scan: children={n} typing={typing_present} new={len(new_msgs)}")
    _handle_typing(typing_present, root)
    _handle_new_messages(new_msgs, root)
    _handle_read(read_events, root)


def _clear_typing() -> bool:
    global _typing_active, _typing_clear_source
    _typing_active = False
    _typing_clear_source = None
    return False


def _handle_typing(present: bool, root) -> None:
    global _typing_active, _typing_clear_source
    if present:
        if not _typing_active:
            _typing_active = True
            if _config.announce_typing:
                who = _get_contact(root) or "Someone"
                _announce(f"{who} is typing.")
        # (Re)arm a safety auto-clear: the "stopped typing" remove event can
        # arrive with a defunct node, so never let the flag stick.
        if _typing_clear_source is not None:
            try:
                GLib.source_remove(_typing_clear_source)
            except Exception:
                pass
        _typing_clear_source = GLib.timeout_add_seconds(12, _clear_typing)
    else:
        if _typing_clear_source is not None:
            try:
                GLib.source_remove(_typing_clear_source)
            except Exception:
                pass
            _typing_clear_source = None
        _typing_active = False


def _handle_new_messages(msgs: list[dict], root) -> None:
    global _prime_next, _primed

    if not msgs:
        return

    if _prime_next or not _primed:
        for m in msgs:
            _remember(m["uuid"])
        _prime_next = False
        _primed = True
        _debug(f"PRIMED {len(msgs)} message(s) silently (conversation load)")
        return

    if len(msgs) > ANNOUNCE_MAX:
        # A burst this large is a history page-in, not live traffic.
        for m in msgs:
            _remember(m["uuid"])
        _debug(f"BULK {len(msgs)} message(s) -> silent")
        return

    for m in msgs:
        _remember(m["uuid"])
        if m["outgoing"]:
            if _config.announce_sent:
                _announce("Message sent.")
        else:
            if _config.announce_received:
                if m["author"]:
                    _announce(f"{m['author']}: {m['text']}")
                else:
                    _announce(f"Message received: {m['text']}")


def _handle_read(uuids: list[str], root) -> None:
    """Announce "Read by <contact>" for outgoing messages just marked read.

    Dormant on stock Signal (the status node is not exposed); ready the moment
    it is.  We announce at most once per scan to avoid a flood when several
    messages are read together."""
    if not uuids:
        return
    who = _get_contact(root)
    _announce(f"Read by {who}." if who else "Read.")


# ---------------------------------------------------------------------------
# AT-SPI listener + debounce
# ---------------------------------------------------------------------------

_CONV_TOKENS = (
    "module-timeline", "module-message", "module-typing",
    "ConversationView", "Inbox__conversation",
)


def _relevant_source(source) -> bool:
    """Is this mutation anywhere in the conversation pane (incl. the typing
    bubble, which mounts as a sibling of the message list)?"""
    cur = source
    for _ in range(12):
        if cur is None:
            return False
        c = _cls(cur)
        if any(tok in c for tok in _CONV_TOKENS):
            return True
        i = _id(cur)
        if i.startswith(ID_MSG_PREFIX) or i.startswith(CONV_ID_PREFIX):
            return True
        cur = AXObject.get_parent(cur)
    return False


def _schedule_scan() -> None:
    global _scan_source
    if _scan_source is not None:
        try:
            GLib.source_remove(_scan_source)
        except Exception:
            pass
    _scan_source = GLib.timeout_add(SCAN_DELAY_MS, _scan_fire)


def _scan_fire() -> bool:
    global _scan_source
    _scan_source = None
    try:
        _do_scan()
    except Exception as e:
        _debug(f"scan error: {e}")
    return False


def _on_children_changed(event) -> None:
    global _recent_source
    try:
        if not _config or not _config.enabled:
            return
        source = event.source
        if not _is_signal_obj(source):
            return
        _recent_source = source

        # Event-driven typing detection.  The typing bubble is a transient
        # element; rather than race it with a delayed scan, read it straight
        # off the mutation -- the added/removed node IS the typing container,
        # whose class contains "typing".
        data = event.any_data
        data_cls = _cls(data) if data is not None else ""
        if "typing" in data_cls or "typing" in _cls(source):
            etype = event.type
            if _config.debug:
                _debug(f"TYPING-EVT {etype} data={data_cls[:48]!r}")
            if etype.endswith("add"):
                _handle_typing(True, _root())
                return
            if etype.endswith("remove"):
                _handle_typing(False, _root())
                return

        if not _relevant_source(source):
            return
        _schedule_scan()
    except Exception as e:
        _debug(f"listener error: {e}")


# ---------------------------------------------------------------------------
# Suppress Orca's own (noisy) Signal live-region announcements
# ---------------------------------------------------------------------------

def _patched_handle_event(self, script, event) -> None:
    if _config and _config.enabled:
        try:
            if _is_signal_obj(event.source):
                _debug(f"SUPPRESS live-region: {event.type}")
                return
        except Exception:
            pass
    return _orig_handle_event(self, script, event)


# ---------------------------------------------------------------------------
# Settings dialog + keybinding (unchanged behaviour)
# ---------------------------------------------------------------------------

def _on_settings_saved(new_config: Config) -> None:
    global _config
    _config = new_config
    _log.info("SignalFilter: settings updated")


def _open_settings(_script, _event=None) -> bool:
    global _settings_window
    if _settings_window is not None:
        try:
            _settings_window.present()
            return True
        except Exception:
            _settings_window = None

    from .config_ui import show_settings_dialog
    cfg = Config.load()

    def _on_save(new_cfg):
        global _settings_window
        _settings_window = None
        _on_settings_saved(new_cfg)

    def _on_destroy(*_args):
        global _settings_window
        _settings_window = None

    _settings_window = show_settings_dialog(cfg, on_save=_on_save)
    if _settings_window is not None:
        _settings_window.connect("destroy", _on_destroy)
    return True


def _register_keybinding() -> bool:
    manager = command_manager.get_manager()
    kb = keybindings.KeyBinding("s", keybindings.ORCA_SHIFT_MODIFIER_MASK)
    manager.add_command(
        command_manager.KeyboardCommand(
            name="signalFilterSettings",
            function=_open_settings,
            group_label="Signal Filter",
            description="Open Signal Filter settings",
            desktop_keybinding=kb,
            laptop_keybinding=kb,
        )
    )
    return False


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def install() -> None:
    global _config, _installed, _orig_handle_event, _listener

    if _installed:
        return

    _config = Config.load()

    # 1. Silence Orca's own live-region announcements for Signal.
    _orig_handle_event = live_region_presenter.LiveRegionPresenter.handle_event
    live_region_presenter.LiveRegionPresenter.handle_event = _patched_handle_event

    # 2. Drive our own clean announcements from DOM mutations.
    _listener = Atspi.EventListener.new(_on_children_changed)
    _listener.register("object:children-changed")

    GLib.idle_add(_register_keybinding)
    # Prime the currently-open conversation silently once Orca has settled.
    GLib.timeout_add(1500, lambda: (_scan_fire(), False)[1])

    _installed = True
    _log.info(
        "SignalFilter: installed (enabled=%s, sent=%s, typing=%s, received=%s)",
        _config.enabled, _config.announce_sent,
        _config.announce_typing, _config.announce_received,
    )


def uninstall() -> None:
    global _installed, _listener, _scan_source

    if not _installed:
        return

    if _orig_handle_event is not None:
        live_region_presenter.LiveRegionPresenter.handle_event = _orig_handle_event

    if _listener is not None:
        try:
            _listener.deregister("object:children-changed")
        except Exception:
            pass
        _listener = None

    for src_name in ("_scan_source", "_typing_clear_source"):
        src = globals().get(src_name)
        if src is not None:
            try:
                GLib.source_remove(src)
            except Exception:
                pass
            globals()[src_name] = None

    _installed = False
    _log.info("SignalFilter: uninstalled")
