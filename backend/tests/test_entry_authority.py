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


class _ClaimedProvenProvider:
    async def verify(self, *, intent_digest, account_scope):
        from app.services.entry_authority import HistoryEvidence
        return HistoryEvidence(ProofState.PROVEN, "claimed_complete", "synthetic-evidence")


def test_proven_history_claim_alone_cannot_issue_order_capability():
    # No broker-account ownership, durable reservation or kill-switch proof
    # exists here; even a claimed PROVEN history is insufficient.
    authority = EntryAuthority(_ClaimedProvenProvider())
    permit = asyncio.run(authority.request(
        intent_digest="order-a", account_scope="paper-scope"
    ))
    assert permit is None


def test_unknown_history_blocks_equity_and_options_for_all_execution_modes():
    """Mode and asset are untrusted intent labels, not authority to trade."""
    async def verify_all():
        authority = EntryAuthority()
        results = []
        for mode in ("manual", "copilot", "autopilot"):
            for asset in ("equity", "options"):
                result = await authority.request(
                    intent_digest=f"{mode}:{asset}:SPY",
                    account_scope="paper-organization",
                )
                results.append(result)
        return results
    assert asyncio.run(verify_all()) == [None] * 6


def test_unavailable_history_never_grants_retry_capability():
    async def retry():
        authority = EntryAuthority()
        return [
            await authority.request(
                intent_digest="same-order", account_scope="paper-organization",
            ) for _ in range(3)
        ]
    assert asyncio.run(retry()) == [None, None, None]
