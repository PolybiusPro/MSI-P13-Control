"""Qt-free transition table for the panel lifecycle.

``step`` is a pure function: it takes the current state and an event and
returns the next state plus the side effects the caller should perform. The
window owns the effects (blanking, persisting, timers, dialogs); this module
only decides which ones follow from what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto

MODE_OFF = "off"


class Phase(Enum):
    OFF = auto()
    STARTING = auto()
    RUNNING = auto()
    RETRYING = auto()
    STOPPING = auto()


class Event(Enum):
    REQUEST = auto()
    START_OK = auto()
    START_FAILED = auto()
    ASYNC_ERROR = auto()
    RETRY_TICK = auto()
    EXTERNAL_STOP = auto()
    SHUTDOWN = auto()


class Effect(Enum):
    BLANK = auto()
    PERSIST_OFF = auto()
    PERSIST_MODE = auto()
    SHOW_OFF = auto()
    SHOW_ERROR = auto()
    SCHEDULE_RETRY = auto()
    CANCEL_RETRY = auto()


@dataclass(frozen=True)
class PanelState:
    phase: Phase = Phase.OFF
    #: The mode the user chose. It survives failures so a later launch retries it.
    desired: str = MODE_OFF
    #: True while the mode is applied by login autostart, which retries on failure.
    login: bool = False
    #: True once the current login failure streak has been shown to the user.
    notified: bool = False


Result = tuple[PanelState, tuple[Effect, ...]]


def step(
    state: PanelState,
    event: Event,
    *,
    mode: str = MODE_OFF,
    login: bool = False,
) -> Result:
    if state.phase is Phase.STOPPING:
        return state, ()

    if event is Event.SHUTDOWN:
        return replace(state, phase=Phase.STOPPING), (Effect.CANCEL_RETRY,)

    if event is Event.REQUEST:
        return _request(state, mode, login)

    if event is Event.START_OK:
        if state.phase is not Phase.STARTING:
            return state, ()
        return replace(state, phase=Phase.RUNNING, notified=False), (Effect.PERSIST_MODE,)

    if event is Event.START_FAILED:
        if state.phase is not Phase.STARTING:
            return state, ()
        if state.login:
            return (
                replace(state, phase=Phase.RETRYING, notified=True),
                (Effect.SCHEDULE_RETRY,),
            )
        return replace(state, phase=Phase.OFF), (Effect.BLANK, Effect.SHOW_OFF)

    if event is Event.ASYNC_ERROR:
        if state.phase not in (Phase.STARTING, Phase.RUNNING):
            return state, ()
        if state.login:
            effects = (
                (Effect.SCHEDULE_RETRY,)
                if state.notified
                else (Effect.SHOW_ERROR, Effect.SCHEDULE_RETRY)
            )
            return replace(state, phase=Phase.RETRYING, notified=True), effects
        return replace(state, phase=Phase.OFF), (Effect.SHOW_ERROR, Effect.SHOW_OFF)

    if event is Event.RETRY_TICK:
        if state.phase is not Phase.RETRYING:
            return state, ()
        return replace(state, phase=Phase.STARTING), ()

    if event is Event.EXTERNAL_STOP:
        if state.phase is not Phase.RUNNING:
            return state, ()
        return replace(state, phase=Phase.OFF), (Effect.SHOW_OFF,)

    return state, ()


def _request(state: PanelState, mode: str, login: bool) -> Result:
    if mode == MODE_OFF:
        return (
            PanelState(Phase.OFF, desired=MODE_OFF),
            (Effect.BLANK, Effect.PERSIST_OFF),
        )
    if login:
        starting = PanelState(
            Phase.STARTING, desired=mode, login=True, notified=state.notified
        )
        return starting, ()
    return PanelState(Phase.STARTING, desired=mode), (Effect.CANCEL_RETRY,)
