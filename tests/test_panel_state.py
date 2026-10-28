"""Pure transition table for the panel lifecycle."""

from __future__ import annotations

from p13ctl.gui.panel_state import Effect, Event, Phase, PanelState, step


def _running(mode: str = "sysmon", *, login: bool = False) -> PanelState:
    return PanelState(Phase.RUNNING, desired=mode, login=login)


def test_initial_state_is_off_with_nothing_desired() -> None:
    state = PanelState()
    assert state.phase is Phase.OFF
    assert state.desired == "off"
    assert state.login is False
    assert state.notified is False


def test_manual_request_goes_to_starting_and_cancels_login_retry() -> None:
    before = PanelState(Phase.RETRYING, desired="clock", login=True, notified=True)
    after, effects = step(before, Event.REQUEST, mode="sysmon")
    assert after == PanelState(Phase.STARTING, desired="sysmon")
    assert Effect.CANCEL_RETRY in effects


def test_login_request_keeps_login_and_notified_flags() -> None:
    before = PanelState(Phase.RETRYING, desired="clock", login=True, notified=True)
    after, effects = step(before, Event.REQUEST, mode="clock", login=True)
    assert after == PanelState(Phase.STARTING, desired="clock", login=True, notified=True)
    assert Effect.CANCEL_RETRY not in effects


def test_requesting_off_blanks_and_persists_off() -> None:
    after, effects = step(_running(), Event.REQUEST, mode="off")
    assert after == PanelState(Phase.OFF, desired="off")
    assert effects == (Effect.BLANK, Effect.PERSIST_OFF)


def test_start_ok_runs_and_persists_the_mode() -> None:
    before = PanelState(Phase.STARTING, desired="clock")
    after, effects = step(before, Event.START_OK)
    assert after == PanelState(Phase.RUNNING, desired="clock")
    assert effects == (Effect.PERSIST_MODE,)


def test_login_start_ok_clears_notified_streak() -> None:
    before = PanelState(Phase.STARTING, desired="clock", login=True, notified=True)
    after, _ = step(before, Event.START_OK)
    assert after.notified is False
    assert after.login is True


def test_manual_start_failure_goes_off_without_persisting() -> None:
    before = PanelState(Phase.STARTING, desired="clock")
    after, effects = step(before, Event.START_FAILED)
    assert after.phase is Phase.OFF
    assert after.desired == "clock"
    assert effects == (Effect.BLANK, Effect.SHOW_OFF)
    assert Effect.PERSIST_OFF not in effects


def test_login_start_failure_retries_and_stays_quiet() -> None:
    before = PanelState(Phase.STARTING, desired="clock", login=True)
    after, effects = step(before, Event.START_FAILED)
    assert after == PanelState(Phase.RETRYING, desired="clock", login=True, notified=True)
    assert effects == (Effect.SCHEDULE_RETRY,)


def test_async_error_on_manual_run_shows_error_and_goes_off() -> None:
    after, effects = step(_running("extended"), Event.ASYNC_ERROR)
    assert after.phase is Phase.OFF
    assert after.desired == "extended"
    assert effects == (Effect.SHOW_ERROR, Effect.SHOW_OFF)


def test_async_error_on_login_notifies_once_per_streak() -> None:
    first, first_effects = step(_running("extended", login=True), Event.ASYNC_ERROR)
    assert first.phase is Phase.RETRYING
    assert first_effects == (Effect.SHOW_ERROR, Effect.SCHEDULE_RETRY)

    again, again_effects = step(
        PanelState(Phase.RUNNING, desired="extended", login=True, notified=True),
        Event.ASYNC_ERROR,
    )
    assert again.phase is Phase.RETRYING
    assert again_effects == (Effect.SCHEDULE_RETRY,)


def test_retry_tick_restarts_only_from_retrying() -> None:
    before = PanelState(Phase.RETRYING, desired="clock", login=True, notified=True)
    after, effects = step(before, Event.RETRY_TICK)
    assert after.phase is Phase.STARTING
    assert effects == ()

    idle, idle_effects = step(_running(), Event.RETRY_TICK)
    assert idle == _running()
    assert idle_effects == ()


def test_external_stop_while_running_goes_off() -> None:
    after, effects = step(_running("extended"), Event.EXTERNAL_STOP)
    assert after.phase is Phase.OFF
    assert after.desired == "extended"
    assert effects == (Effect.SHOW_OFF,)


def test_external_stop_is_ignored_unless_running() -> None:
    for phase in (Phase.OFF, Phase.STARTING, Phase.RETRYING, Phase.STOPPING):
        state = PanelState(phase, desired="extended")
        assert step(state, Event.EXTERNAL_STOP) == (state, ())


def test_shutdown_goes_to_stopping_from_any_live_phase_and_cancels_retry() -> None:
    for phase in Phase:
        if phase is Phase.STOPPING:
            continue
        after, effects = step(PanelState(phase, desired="clock", login=True), Event.SHUTDOWN)
        assert after.phase is Phase.STOPPING
        assert Effect.CANCEL_RETRY in effects


def test_stopping_is_terminal() -> None:
    state = PanelState(Phase.STOPPING, desired="clock")
    for event in (Event.REQUEST, Event.START_OK, Event.START_FAILED, Event.ASYNC_ERROR):
        assert step(state, event, mode="sysmon") == (state, ())
