"""R2-Q provider/authority regression tests.

These tests deliberately require UNKNOWN to hold all entries closed. They are
not proof that broker submission routes have been migrated.
"""
import asyncio
from app.services.entry_authority import (
    EntryAuthority, EntryAuthorization, ProofState, UnavailableHistoryProvider,
)


def test_default_history_source_is_unknown():
    evidence=asyncio.run(UnavailableHistoryProvider().verify(
        intent_digest="hash",account_scope="paper-scope"))
    assert evidence.state is ProofState.UNKNOWN
    assert evidence.reason == "complete_execution_history_unavailable"


def test_no_entry_authorization_without_complete_broker_history():
    authority=EntryAuthority()
    permit=asyncio.run(authority.request(
        intent_digest="order-a",account_scope="paper-scope"))
    assert permit is None


def test_externally_constructed_authorization_not_trusted():
    authority=EntryAuthority()
    invented=EntryAuthorization("forged","order-a","paper-scope")
    assert asyncio.run(authority.consume(invented,
        intent_digest="order-a",account_scope="paper-scope")) is False


def test_parallel_requests_do_not_issue_a_permit():
    async def check():
        authority=EntryAuthority()
        return await asyncio.gather(*(
            authority.request(intent_digest="order-a",account_scope="paper-scope")
            for _ in range(8)))
    assert asyncio.run(check()) == [None]*8
