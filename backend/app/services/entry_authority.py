"""R2-Q: internal entry authority. Unknown history never grants an entry.

An authorization is single-use, bound to the exact order intent, and verified
again at consumption. This module does not enable broker connections.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ProofState(str, Enum):
    PROVEN = "proven"
    UNPROVEN = "unproven"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HistoryEvidence:
    state: ProofState
    reason: str
    evidence_id: str | None = None


class HistoryProvider(Protocol):
    async def verify(self, *, intent_digest: str, account_scope: str) -> HistoryEvidence: ...


class UnavailableHistoryProvider:
    async def verify(self, *, intent_digest: str, account_scope: str) -> HistoryEvidence:
        return HistoryEvidence(ProofState.UNKNOWN, "complete_execution_history_unavailable")


@dataclass(frozen=True)
class EntryAuthorization:
    _nonce: str
    _intent_digest: str
    _account_scope: str


class EntryAuthority:
    """Memory-local one-shot permit service, NOT a production authorization store.

    No permit can currently be issued by the shipped UNKNOWN provider.
    Account ownership, stop state, and order risk checks must be verified by
    trusted server-side integrations before implementing any positive path.
    """

    def __init__(self, provider: HistoryProvider | None = None):
        self._provider = provider or UnavailableHistoryProvider()

    async def request(self, *, intent_digest: str, account_scope: str) -> EntryAuthorization | None:
        if not intent_digest or not account_scope:
            return None
        try:
            evidence = await self._provider.verify(
                intent_digest=intent_digest, account_scope=account_scope,
            )
        except Exception:
            # A failed broker-history read cannot authorize an order.
            return None
        # Malformed, missing, or unrecognized results must not be elevated
        # into an authorization decision.
        if not isinstance(evidence, HistoryEvidence):
            return None
        if evidence.state is not ProofState.PROVEN or not evidence.evidence_id:
            return None
        # Intentionally unavailable: proof evidence alone cannot establish
        # kill-switch state, full risk approval, or an atomic broker submission.
        return None

    async def consume(self, authorization: EntryAuthorization, *,
                      intent_digest: str, account_scope: str) -> bool:
        # This temporary implementation cannot consume externally constructed
        # capabilities; real storage/TOCTOU coordination is not yet present.
        return False


entry_authority = EntryAuthority()
