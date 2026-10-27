"""Tests for the load prompt panel."""

import threading

from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import LoadPromptWidget
from pilgrim.gui.ripple.components.load_prompt import IDLE_TEXT


def test_starts_idle(qtbot: QtBot) -> None:
    widget = LoadPromptWidget()
    qtbot.addWidget(widget)
    assert not widget.is_prompting
    assert widget.message.text() == IDLE_TEXT
    assert not widget.continue_button.isEnabled()
    assert not widget.message.font().bold()


def test_prompt_from_another_thread_shows_it_and_enables_continue(qtbot: QtBot) -> None:
    widget = LoadPromptWidget()
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.prompted, timeout=2000) as blocker:
        thread = threading.Thread(target=widget.prompt, args=("Set the load to 50.",))
        thread.start()
        thread.join()
    assert blocker.args == ["Set the load to 50."]
    assert widget.is_prompting
    assert widget.message.text() == "Set the load to 50."
    assert widget.message.font().bold()
    assert widget.continue_button.isEnabled()


def test_continue_emits_and_clears(qtbot: QtBot) -> None:
    widget = LoadPromptWidget()
    qtbot.addWidget(widget)
    widget.show_prompt("Set the load to 50.")
    with qtbot.waitSignal(widget.continued, timeout=2000):
        widget.continue_button.click()
    assert not widget.is_prompting
    assert widget.message.text() == IDLE_TEXT
    assert not widget.message.font().bold()


def test_clear_disables_continue_without_emitting(qtbot: QtBot) -> None:
    widget = LoadPromptWidget()
    qtbot.addWidget(widget)
    widget.show_prompt("Set the load to 0.")
    with qtbot.assertNotEmitted(widget.continued):
        widget.clear()
    assert not widget.is_prompting
    assert widget.message.text() == IDLE_TEXT
