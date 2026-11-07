"""Process-local entry permits for the single-worker broker dispatcher.

A halt invalidates existing permits even if an operator resets before a queued
job runs. Check synchronously immediately before broker submission, after any
await. Explicit risk reductions carry no entry permit and remain available.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class EntryHalted(RuntimeError):
    """No new order was sent: entry authorization was halted or invalidated."""


@dataclass(frozen=True)
class EntryPermit:
    epoch: int


_UNBOUND = object()
_permit = ContextVar("entry_submission_permit", default=_UNBOUND)


def capture_entry_permit() -> EntryPermit:
    from app.services.kill_switch import kill_switch_service
    if kill_switch_service.is_engaged:
        raise EntryHalted("kill_switch")
    return EntryPermit(kill_switch_service.entry_epoch)


def current_entry_permit():
    value = _permit.get()
    # Direct adapter callers default to entry protection, never to exemption.
    return capture_entry_permit() if value is _UNBOUND else value


def check_entry_permit(permit) -> None:
    if permit is None:
        return
    from app.services.kill_switch import kill_switch_service
    if kill_switch_service.is_engaged or permit.epoch != kill_switch_service.entry_epoch:
        raise EntryHalted("kill_switch")


@contextmanager
def submission_scope(permit):
    token = _permit.set(permit)
    try:
        check_entry_permit(permit)
        yield
    finally:
        _permit.reset(token)
