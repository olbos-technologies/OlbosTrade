"""Read-only, versioned IBKR Activity Flex execution intake.

A parsed upload is an UNAUTHENTICATED candidate, never broker history coverage,
ledger promotion, reconciliation proof, or permission to submit an order.
Version 1 accepts one account/statement, Trades/EXECUTION rows, STK and OPT.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 25_000
SCHEMA_VERSION = "ibkr-activity-flex-executions-v1"
_NUMBER = re.compile(r"[+-]?\d{1,24}(?:\.\d{1,12})?", re.ASCII)


class FlexIntakeError(ValueError):
    """Only constant reason codes and row numbers; never echo private XML."""
    def __init__(self, code: str, row: int | None = None):
        self.code, self.row = code, row
        super().__init__(code if row is None else f"{code}:row_{row}")


def _text(values: dict, key: str) -> str:
    value = values.get(key, "").strip()
    if not value or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise FlexIntakeError("required_field_invalid")
    return value


def _decimal(values: dict, key: str) -> Decimal:
    text = _text(values, key)
    if not _NUMBER.fullmatch(text):
        raise FlexIntakeError("numeric_field_invalid")
    return Decimal(text)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _date(text: str) -> date:
    if not re.fullmatch(r"\d{8}", text, flags=re.ASCII):
        raise FlexIntakeError("date_format_invalid")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        raise FlexIntakeError("date_invalid") from None


def _utc(local: datetime, zone: ZoneInfo) -> datetime:
    # Never guess the offset in a repeated/nonexistent DST hour, including at
    # report boundaries. A future adapter can supply independently known UTC.
    candidates = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise FlexIntakeError("timestamp_ambiguous_or_nonexistent")
    return candidates.pop()


@dataclass(frozen=True)
class FlexExecutionCandidate:
    execution_id: str
    account: str
    organization: str
    filled_at: datetime
    asset_class: str
    contract_id: str
    symbol: str
    underlying: str
    signed_quantity: Decimal
    price: Decimal
    multiplier: Decimal
    currency: str
    order_id: str
    commission: Decimal
    commission_currency: str
    expiration: str | None
    strike: Decimal | None
    option_type: str | None
    source_row_digest: str

    def staging_record(self) -> dict:
        """Compatible with the separate draft staging protocol, not a ledger."""
        return {
            "execution_id": self.execution_id, "account": self.account,
            "organization": self.organization, "filled_at": self.filled_at.isoformat(),
            "asset_class": self.asset_class, "contract_id": self.contract_id,
            "symbol": self.symbol, "underlying": self.underlying,
            "signed_quantity": _decimal_text(self.signed_quantity),
            "price": _decimal_text(self.price), "multiplier": _decimal_text(self.multiplier),
            "currency": self.currency, "order_id": self.order_id,
            "commission": _decimal_text(self.commission),
            "commission_currency": self.commission_currency,
            "expiration": self.expiration,
            "strike": _decimal_text(self.strike) if self.strike is not None else None,
            "option_type": self.option_type, "source_row_digest": self.source_row_digest,
            "source": SCHEMA_VERSION, "source_authenticated": False,
        }


@dataclass(frozen=True)
class FlexIntakeResult:
    source_digest: str
    start: datetime
    end: datetime
    rows_received: int
    executions: tuple[FlexExecutionCandidate, ...]

    def summary(self) -> dict:
        """Safe console output: no account, organization, symbol or execution IDs."""
        return {
            "schema": SCHEMA_VERSION, "state": "parsed_unverified", "verified": False,
            "source_authenticated": False, "history_coverage_verified": False,
            "entry_authorized": False, "source_sha256": self.source_digest,
            "declared_start_utc": self.start.isoformat(),
            "declared_end_exclusive_utc": self.end.isoformat(),
            "rows_received": self.rows_received, "executions": len(self.executions),
            "identical_repeats": self.rows_received - len(self.executions),
            "asset_classes": sorted({row.asset_class for row in self.executions}),
        }


def _execution(attrs: dict, *, account: str, organization: str,
               zone: ZoneInfo, start: datetime, end: datetime) -> FlexExecutionCandidate:
    if _text(attrs, "accountId") != account:
        raise FlexIntakeError("row_account_mismatch")
    if _text(attrs, "levelOfDetail") != "EXECUTION":
        raise FlexIntakeError("execution_level_required")
    if _text(attrs, "transactionType") not in ("ExchTrade", "Trade"):
        raise FlexIntakeError("transaction_type_requires_review")
    for field in ("origTradeID", "origOrderID", "origTransactionID", "origTradeDate"):
        if attrs.get(field, "").strip():
            raise FlexIntakeError("correction_requires_review")
    for field in ("changeInPrice", "changeInQuantity", "origTradePrice"):
        if attrs.get(field, "").strip() and _decimal(attrs, field) != 0:
            raise FlexIntakeError("correction_requires_review")
    if attrs.get("notes", "").strip():
        codes = {code.casefold() for code in re.split(r"[;,\s]+", _text(attrs, "notes"))}
        if codes & {"ca", "co"}:
            raise FlexIntakeError("cancel_or_correction_requires_review")
    execution_id = _text(attrs, "ibExecID")
    # Corrections can change the trailing execution-ID component. Do not
    # silently count a known revised four-component ID as an additional fill.
    parts = execution_id.split(".")
    if len(parts) == 4 and parts[-1].isdigit() and int(parts[-1]) != 1:
        raise FlexIntakeError("execution_revision_requires_review")
    asset = _text(attrs, "assetCategory")
    if asset not in ("STK", "OPT"):
        raise FlexIntakeError("unsupported_asset_class")
    contract = _text(attrs, "conid")
    if not re.fullmatch(r"[0-9]{1,20}", contract) or int(contract) <= 0:
        raise FlexIntakeError("contract_identity_invalid")
    quantity, price = _decimal(attrs, "quantity"), _decimal(attrs, "tradePrice")
    multiplier = _decimal(attrs, "multiplier")
    side = _text(attrs, "buySell")
    if side not in ("BUY", "SELL") or quantity == 0 or (side == "BUY" and quantity < 0):
        raise FlexIntakeError("execution_side_or_quantity_invalid")
    if price <= 0 or multiplier <= 0:
        raise FlexIntakeError("price_or_multiplier_invalid")
    if asset == "STK":
        if multiplier != 1:
            raise FlexIntakeError("stock_multiplier_invalid")
        if attrs.get("expiry", "").strip() or attrs.get("putCall", "").strip() or (attrs.get("strike", "").strip() and _decimal(attrs, "strike") != 0):
            raise FlexIntakeError("stock_option_fields_conflict")
    currency, commission_currency = _text(attrs, "currency"), _text(attrs, "ibCommissionCurrency")
    if not all(re.fullmatch(r"[A-Z]{3}", item) for item in (currency, commission_currency)):
        raise FlexIntakeError("currency_invalid")
    timestamp = _text(attrs, "dateTime")
    if not re.fullmatch(r"[0-9]{8};[0-9]{6}", timestamp):
        raise FlexIntakeError("execution_datetime_required")
    day = _date(timestamp[:8])
    try:
        local = datetime.combine(day, time(int(timestamp[9:11]), int(timestamp[11:13]), int(timestamp[13:15])))
    except ValueError:
        raise FlexIntakeError("execution_time_invalid") from None
    filled_at = _utc(local, zone)
    if not start <= filled_at < end:
        raise FlexIntakeError("execution_outside_declared_period")
    expiry = strike = option_type = None
    symbol = _text(attrs, "symbol")
    underlying = symbol
    if asset == "OPT":
        if quantity != quantity.to_integral_value():
            raise FlexIntakeError("option_quantity_invalid")
        expiry_date = _date(_text(attrs, "expiry"))
        if expiry_date < day:
            raise FlexIntakeError("option_expired_before_execution")
        expiry = expiry_date.isoformat()
        strike = _decimal(attrs, "strike")
        if strike <= 0 or _text(attrs, "putCall") not in ("P", "C"):
            raise FlexIntakeError("option_contract_invalid")
        option_type = "put" if attrs["putCall"] == "P" else "call"
        underlying = _text(attrs, "underlyingSymbol")
    return FlexExecutionCandidate(
        execution_id, account, organization, filled_at, asset, str(int(contract)),
        symbol, underlying, quantity.copy_abs() if side == "BUY" else quantity.copy_abs().copy_negate(),
        price, multiplier, currency, _text(attrs, "ibOrderID"),
        _decimal(attrs, "ibCommission"), commission_currency, expiry, strike, option_type,
        hashlib.sha256(json.dumps(attrs, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    )


def parse_activity_flex_xml(payload: bytes, *, expected_account: str,
                            organization_id: str, source_timezone: str) -> FlexIntakeResult:
    """Validate ALL rows before returning anything. No I/O and no promotion.

    Account/tenant/timezone arguments are declared import context, NOT proof of
    ownership or broker authenticity. Dates are inclusive in the source report;
    staging uses UTC half-open intervals. Unsupported content rejects the file.
    """
    if type(payload) is not bytes or not payload or len(payload) > MAX_BYTES:
        raise FlexIntakeError("payload_size_or_type_invalid")
    if (not isinstance(expected_account, str) or not expected_account.strip()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", expected_account.strip())):
        raise FlexIntakeError("expected_account_required")
    account = expected_account.strip()
    try:
        organization = str(UUID(organization_id))
        zone = ZoneInfo(source_timezone)
    except (ValueError, TypeError, AttributeError, ZoneInfoNotFoundError):
        raise FlexIntakeError("import_context_invalid") from None
    try:
        text = payload.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise FlexIntakeError("xml_declarations_forbidden")
        root = ElementTree.fromstring(text)
    except (UnicodeDecodeError, ElementTree.ParseError):
        raise FlexIntakeError("xml_invalid") from None
    if root.tag != "FlexQueryResponse" or root.get("type") != "AF":
        raise FlexIntakeError("activity_flex_report_required")
    containers = root.findall("FlexStatements")
    if len(root) != 1 or len(containers) != 1 or any(child.tag != "FlexStatement" for child in containers[0]):
        raise FlexIntakeError("statement_container_invalid")
    statements = list(containers[0])
    if len(statements) != 1 or containers[0].get("count") != "1":
        raise FlexIntakeError("single_statement_required")
    statement = statements[0]
    if statement.get("accountId") != account:
        raise FlexIntakeError("statement_account_mismatch")
    start_day, end_day = _date(statement.get("fromDate", "")), _date(statement.get("toDate", ""))
    if start_day > end_day or end_day == date.max:
        raise FlexIntakeError("declared_period_invalid")
    start = _utc(datetime.combine(start_day, time()), zone)
    end = _utc(datetime.combine(end_day + timedelta(days=1), time()), zone)
    sections = statement.findall("Trades")
    if len(sections) != 1:
        raise FlexIntakeError("trades_section_required")
    rows = list(sections[0])
    if len(rows) > MAX_ROWS:
        raise FlexIntakeError("row_limit_exceeded")
    parsed: dict[str, FlexExecutionCandidate] = {}
    families: dict[str, str] = {}
    for number, row in enumerate(rows, 1):
        try:
            if row.tag != "Trade" or len(row) or (row.text or "").strip():
                raise FlexIntakeError("unsupported_trade_row")
            candidate = _execution(row.attrib, account=account, organization=organization,
                                   zone=zone, start=start, end=end)
            prior = parsed.get(candidate.execution_id)
            if prior is not None and prior != candidate:
                raise FlexIntakeError("conflicting_execution_requires_review")
            if "." in candidate.execution_id:
                family = candidate.execution_id.rsplit(".", 1)[0]
                if family in families and families[family] != candidate.execution_id:
                    raise FlexIntakeError("execution_family_requires_review")
                families[family] = candidate.execution_id
            parsed[candidate.execution_id] = candidate
        except FlexIntakeError as exc:
            raise FlexIntakeError(exc.code, number) from None
    return FlexIntakeResult(hashlib.sha256(payload).hexdigest(), start, end, len(rows), tuple(parsed.values()))
