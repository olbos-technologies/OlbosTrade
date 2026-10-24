"""R2-Q: denial at the final broker submission choke point.

The default history provider is UNKNOWN. Even when a route reaches the
submission adapter, no equity or option order can be placed, including when
the caller fabricates an EntryAuthorization value.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.services.entry_authority import EntryAuthorization
from app.services.order_submission import (
    EntrySubmissionDenied, submit_equity_entry, submit_options_entry,
)


@pytest.mark.parametrize("authorization", [
    None, EntryAuthorization("invented", "fake-intent", "fake-account"),
])
def test_equity_entry_refused_without_broker_call(authorization):
    broker = MagicMock(place_equity_order=AsyncMock())
    with pytest.raises(EntrySubmissionDenied, match="entry_history_proof_unavailable"):
        asyncio.run(submit_equity_entry(
            authorization=authorization, broker=broker,
            ticker="SPY", qty=1, side="BUY",
        ))
    broker.place_equity_order.assert_not_awaited()


@pytest.mark.parametrize("authorization", [
    None, EntryAuthorization("invented", "fake-intent", "fake-account"),
])
def test_options_entry_refused_without_broker_call(authorization):
    broker = MagicMock(place_order=AsyncMock())
    with pytest.raises(EntrySubmissionDenied, match="entry_history_proof_unavailable"):
        asyncio.run(submit_options_entry(
            authorization=authorization, broker=broker, order=object(),
        ))
    broker.place_order.assert_not_awaited()
