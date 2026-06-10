"""Accessible GTK3 settings dialog for Orca Signal Filter.

Single-page layout following the same AT-SPI event suspension pattern
as Clock and Polyglot for Orca.
"""

from __future__ import annotations

import logging
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Atk", "1.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Atk, Gdk, GLib

from .config import Config

_log = logging.getLogger("orca-signal-filter")

_resume_timer_id: int | None = None

_EVENTS_TO_SUSPEND = [
    "object:state-changed:focused",
    "object:state-changed:showing",
    "object:children-changed:",
    "object:property-change:accessible-name",
]


def _suspend_events():
    global _resume_timer_id
    if _resume_timer_id is not None:
        GLib.source_remove(_resume_timer_id)
        _resume_timer_id = None
    try:
        from orca import event_manager
        manager = event_manager.get_manager()
        for event in _EVENTS_TO_SUSPEND:
            manager.deregister_listener(event)
    except Exception:
        pass


def _schedule_resume():
    global _resume_timer_id
    if _resume_timer_id is not None:
        GLib.source_remove(_resume_timer_id)
    _resume_timer_id = GLib.timeout_add(500, _resume_events)


def _resume_events():
    global _resume_timer_id
    _resume_timer_id = None
    try:
        from orca import event_manager
        manager = event_manager.get_manager()
        for event in _EVENTS_TO_SUSPEND:
            manager.register_listener(event)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Row creation helpers
# ---------------------------------------------------------------------------

def _create_switch_row(label_text, state, atk_name=None, atk_desc=None):
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    hbox.set_margin_start(12)
    hbox.set_margin_end(12)
    hbox.set_margin_top(12)
    hbox.set_margin_bottom(12)
    label = Gtk.Label(label=label_text)
    label.set_use_underline(True)
    label.set_xalign(0)
    label.set_hexpand(True)
    switch = Gtk.Switch()
    switch.set_valign(Gtk.Align.CENTER)
    switch.set_active(state)
    label.set_mnemonic_widget(switch)
    atk_obj = switch.get_accessible()
    if atk_obj:
        atk_obj.set_role(Atk.Role.SWITCH)
        if atk_name:
            atk_obj.set_name(atk_name)
        if atk_desc:
            atk_obj.set_description(atk_desc)
    hbox.pack_start(label, True, True, 0)
    hbox.pack_end(switch, False, False, 0)
    row.add(hbox)
    return row, switch


def _create_spin_row(label_text, lower, upper, step, value, atk_name=None):
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    hbox.set_margin_start(12)
    hbox.set_margin_end(12)
    hbox.set_margin_top(12)
    hbox.set_margin_bottom(12)
    label = Gtk.Label(label=label_text)
    label.set_use_underline(True)
    label.set_xalign(0)
    label.set_hexpand(True)
    adjustment = Gtk.Adjustment(
        value=value, lower=lower, upper=upper,
        step_increment=step, page_increment=step * 5,
    )
    spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=0)
    label.set_mnemonic_widget(spin)
    atk_obj = spin.get_accessible()
    if atk_obj and atk_name:
        atk_obj.set_name(atk_name)
    hbox.pack_start(label, True, True, 0)
    hbox.pack_end(spin, False, False, 0)
    row.add(hbox)
    return row, spin


# ---------------------------------------------------------------------------
# FocusManagedListBox (same pattern as Clock/Audio Themes/Polyglot)
# ---------------------------------------------------------------------------

class FocusManagedListBox(Gtk.ListBox):
    """ListBox managing Tab/Shift+Tab focus between interactive widgets."""

    def __init__(self):
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        self.get_style_context().add_class("frame")
        self.set_can_focus(False)
        self.set_header_func(self._separator_header_func, None)
        self._widgets = []
        self._rows = []
        self._exiting_backward = [False]

    @staticmethod
    def _separator_header_func(row, before, _user_data):
        if before is not None:
            row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    def add_row_with_widget(self, row, widget):
        widget.connect("key-press-event", self._on_widget_key_press)
        row.connect("focus-in-event", self._on_row_focus_in, widget)
        self.add(row)
        self._rows.append(row)
        self._widgets.append(widget)

    def _focus_next_sensitive_widget(self, widget):
        try:
            idx = self._widgets.index(widget)
            for i in range(idx + 1, len(self._widgets)):
                if self._widgets[i].get_sensitive():
                    self._widgets[i].grab_focus()
                    return True
        except ValueError:
            pass
        return False

    def _focus_prev_sensitive_widget(self, widget):
        try:
            idx = self._widgets.index(widget)
            for i in range(idx - 1, -1, -1):
                if self._widgets[i].get_sensitive():
                    self._widgets[i].grab_focus()
                    return True
        except ValueError:
            pass
        return False

    def _on_widget_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Tab:
            return self._focus_next_sensitive_widget(widget)
        if event.keyval == Gdk.KEY_ISO_Left_Tab:
            return self._focus_prev_sensitive_widget(widget)
        return False

    def _on_row_focus_in(self, _row, _event, widget):
        if self._exiting_backward[0]:
            self._exiting_backward[0] = False
            return False
        widget.grab_focus()
        return False


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SignalFilterSettingsWindow(Gtk.Window):
    """Accessible settings window for Orca Signal Filter."""

    def __init__(self, config: Config, on_save: Callable[[Config], None] | None = None):
        super().__init__(title="Signal Filter Settings")
        self._config = config
        self._on_save = on_save

        self.set_default_size(450, -1)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)

        atk_obj = self.get_accessible()
        if atk_obj:
            atk_obj.set_name("Signal Filter Settings")

        _suspend_events()
        self._build_ui()
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)

    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(18)
        vbox.set_margin_bottom(18)
        vbox.set_margin_start(18)
        vbox.set_margin_end(18)

        listbox = FocusManagedListBox()

        # Enable
        row, self._enable_switch = _create_switch_row(
            "_Enable Signal Filter", self._config.enabled,
            atk_name="Enable Signal Filter",
            atk_desc="Filter noisy live region announcements from Signal Desktop",
        )
        listbox.add_row_with_widget(row, self._enable_switch)

        # Announce sent
        row, self._sent_switch = _create_switch_row(
            "Announce _sent messages", self._config.announce_sent,
            atk_name="Announce sent messages",
        )
        listbox.add_row_with_widget(row, self._sent_switch)

        # Announce typing
        row, self._typing_switch = _create_switch_row(
            "Announce _typing indicators", self._config.announce_typing,
            atk_name="Announce typing indicators",
        )
        listbox.add_row_with_widget(row, self._typing_switch)

        # Announce typing stopped
        row, self._typing_stopped_switch = _create_switch_row(
            "Announce typing st_opped", self._config.announce_typing_stopped,
            atk_name="Announce when the other person stops typing",
            atk_desc="Say \"stopped typing\" when the typing indicator disappears "
                     "without a message being sent",
        )
        listbox.add_row_with_widget(row, self._typing_stopped_switch)

        # Announce received
        row, self._received_switch = _create_switch_row(
            "Announce _received messages", self._config.announce_received,
            atk_name="Announce received messages",
        )
        listbox.add_row_with_widget(row, self._received_switch)

        # Dedup seconds
        row, self._dedup_spin = _create_spin_row(
            "_Deduplication window (seconds):", 1, 30, 1,
            self._config.dedup_seconds,
            atk_name="Deduplication window in seconds",
        )
        listbox.add_row_with_widget(row, self._dedup_spin)

        # Debug
        row, self._debug_switch = _create_switch_row(
            "De_bug logging", self._config.debug,
            atk_name="Debug logging",
            atk_desc="Log all Signal live region events to debug file for troubleshooting",
        )
        listbox.add_row_with_widget(row, self._debug_switch)

        vbox.pack_start(listbox, True, True, 0)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(12)

        save_btn = Gtk.Button(label="Save")
        save_btn.get_accessible().set_name("Save settings")
        save_btn.connect("clicked", self._on_save_clicked)

        close_btn = Gtk.Button(label="Close")
        close_btn.get_accessible().set_name("Close without saving")
        close_btn.connect("clicked", self._on_close_clicked)

        btn_box.pack_start(close_btn, False, False, 0)
        btn_box.pack_start(save_btn, False, False, 0)

        vbox.pack_start(btn_box, False, False, 0)

        self.add(vbox)

    def _on_save_clicked(self, _btn):
        self._save_config()
        _suspend_events()
        self.destroy()
        _schedule_resume()

    def _on_close_clicked(self, _btn):
        _suspend_events()
        self.destroy()
        _schedule_resume()

    def _on_delete(self, _window, _event):
        _suspend_events()
        _schedule_resume()
        return False

    def _on_key_press(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            _suspend_events()
            self.destroy()
            _schedule_resume()
            return True
        return False

    def _save_config(self):
        self._config.enabled = self._enable_switch.get_active()
        self._config.announce_sent = self._sent_switch.get_active()
        self._config.announce_typing = self._typing_switch.get_active()
        self._config.announce_typing_stopped = self._typing_stopped_switch.get_active()
        self._config.announce_received = self._received_switch.get_active()
        self._config.dedup_seconds = self._dedup_spin.get_value_as_int()
        self._config.debug = self._debug_switch.get_active()
        self._config.save()

        if self._on_save:
            self._on_save(self._config)

    def focus_first(self):
        self._enable_switch.grab_focus()
        _schedule_resume()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def show_settings_dialog(
    config: Config,
    on_save: Callable[[Config], None] | None = None,
) -> SignalFilterSettingsWindow:
    """Show the settings window. Must be called from the GTK main thread."""
    window = SignalFilterSettingsWindow(config, on_save)
    window.show_all()
    window.focus_first()
    return window
