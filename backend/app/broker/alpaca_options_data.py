"""Bounded GET-only Alpaca contract/snapshot collection; never partial success."""
import asyncio
import re

MAX_PAGES = 20
MAX_CONTRACTS = 5000
COLLECTION_TIMEOUT = 45


class OptionsDataUnavailable(ValueError):
    pass


async def pages(get, base, path, params, field, expected_type):
    token = None
    seen = set()
    for _ in range(MAX_PAGES):
        query = dict(params)
        if token is not None:
            query["page_token"] = token
        result = await get(base, path, params=query)
        if not isinstance(result, dict) or not isinstance(result.get(field), expected_type):
            raise OptionsDataUnavailable("Malformed options page")
        yield result[field]
        token = result.get("next_page_token")
        if token is None or token == "":
            return
        if not isinstance(token, str) or len(token) > 2048 or token in seen:
            raise OptionsDataUnavailable("Invalid options pagination")
        seen.add(token)
    raise OptionsDataUnavailable("Options page limit reached; collection incomplete")


async def collect_options(get, trading_base, data_base, symbol, expiry, feed=None):
    """100 symbols/request, full pagination, fixed paths, one overall deadline.

    Missing individual snapshots stay absent (unknown), not fabricated quotes.
    Any failed/malformed/truncated page aborts the collection. Tokens are opaque
    query parameters and never URLs. Provider-default feed remains unverified.
    """
    if feed not in (None, "opra", "indicative"):
        raise OptionsDataUnavailable("Invalid options feed")
    async with asyncio.timeout(COLLECTION_TIMEOUT):
        contracts = {}
        async for rows in pages(get, trading_base, "/v2/options/contracts",
                {"underlying_symbols":symbol, "expiration_date":expiry, "limit":500},
                "option_contracts", list):
            for c in rows:
                if not isinstance(c, dict):
                    raise OptionsDataUnavailable("Malformed option contract")
                occ = c.get("symbol")
                match = re.fullmatch(r"([A-Z.]{1,10})(\d{6})([CP])(\d{8})", occ or "") if isinstance(occ,str) else None
                if (not match or match[1] != symbol or c.get("expiration_date") != expiry
                        or match[2] != expiry.replace("-", "")[2:]
                        or c.get("type") != ("call" if match[3] == "C" else "put")
                        or c.get("underlying_symbol",symbol) != symbol):
                    raise OptionsDataUnavailable("Option contract scope mismatch")
                if occ in contracts and contracts[occ] != c:
                    raise OptionsDataUnavailable("Conflicting option contract pages")
                contracts[occ] = c
                if len(contracts) > MAX_CONTRACTS:
                    raise OptionsDataUnavailable("Options contract limit reached")
        snapshots = {}
        symbols = list(contracts)
        for start in range(0, len(symbols), 100):
            batch = symbols[start:start+100]
            params = {"symbols":",".join(batch), "limit":100}
            if feed is not None:
                params["feed"] = feed
            async for rows in pages(get, data_base, "/v1beta1/options/snapshots", params, "snapshots", dict):
                if any(key not in batch or not isinstance(value,dict) for key,value in rows.items()):
                    raise OptionsDataUnavailable("Option snapshot scope mismatch")
                if any(key in snapshots and snapshots[key] != value for key,value in rows.items()):
                    raise OptionsDataUnavailable("Conflicting option snapshot pages")
                snapshots.update(rows)
        return list(contracts.values()), snapshots
