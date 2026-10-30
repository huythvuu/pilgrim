"""Tests for the message panel."""

import threading

from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import MessageWidget
from pilgrim.gui.ripple.components.message import IDLE_TEXT, PAUSING_TEXT, RUNNING_TEXT


def test_starts_idle(qtbot: QtBot) -> None:
    widget = MessageWidget()
    qtbot.addWidget(widget)
    assert not widget.is_prompting
    assert widget.message.text() == IDLE_TEXT
    assert not widget.pause_button.isEnabled()
    assert not widget.resume_button.isEnabled()
    assert not widget.message.font().bold()


def test_running_enables_pause_only(qtbot: QtBot) -> None:
    widget = MessageWidget()
    qtbot.addWidget(widget)
    widget.set_running(True)
    assert not widget.is_prompting
    assert widget.message.text() == RUNNING_TEXT
    assert widget.pause_button.isEnabled()
    assert not widget.resume_button.isEnabled()
    widget.set_running(False)
    assert widget.message.text() == IDLE_TEXT
    assert not widget.pause_button.isEnabled()


def test_prompt_from_another_thread_shows_it_and_enables_resume(qtbot: QtBot) -> None:
    widget = MessageWidget()
    qtbot.addWidget(widget)
    widget.set_running(True)
    with qtbot.waitSignal(widget.prompted, timeout=2000) as blocker:
        thread = threading.Thread(target=widget.prompt, args=("Set the load to 1.5A.",))
        thread.start()
        thread.join()
    assert blocker.args == ["Set the load to 1.5A."]
    assert widget.is_prompting
    assert widget.message.text() == "Set the load to 1.5A."
    assert widget.message.font().bold()
    assert widget.resume_button.isEnabled()
    assert not widget.pause_button.isEnabled()


def test_resume_emits_and_shows_running(qtbot: QtBot) -> None:
    widget = MessageWidget()
    qtbot.addWidget(widget)
    widget.show_prompt("Set the load to 1.5A.")
    with qtbot.waitSignal(widget.resumed, timeout=2000):
        widget.resume_button.click()
    assert not widget.is_prompting
    assert widget.message.text() == RUNNING_TEXT
    assert not widget.message.font().bold()
    assert widget.pause_button.isEnabled()


def test_pause_emits_and_waits_for_the_prompt(qtbot: QtBot) -> None:
    widget = MessageWidget()
    qtbot.addWidget(widget)
    widget.set_running(True)
    with qtbot.waitSignal(widget.pause_requested, timeout=2000):
        widget.pause_button.click()
    assert widget.message.text() == PAUSING_TEXT
    assert not widget.message.font().bold()
    assert not widget.pause_button.isEnabled()
    assert not widget.is_prompting
    widget.show_prompt("Paused as requested.")
    assert widget.is_prompting
    assert widget.message.text() == "Paused as requested."


def test_clear_disables_both_buttons_without_emitting(qtbot: QtBot) -> None:
    widget = MessageWidget()
    qtbot.addWidget(widget)
    widget.show_prompt("Set the load to 0A.")
    with qtbot.assertNotEmitted(widget.resumed), qtbot.assertNotEmitted(widget.pause_requested):
        widget.clear()
    assert not widget.is_prompting
    assert widget.message.text() == IDLE_TEXT
    assert not widget.pause_button.isEnabled()
