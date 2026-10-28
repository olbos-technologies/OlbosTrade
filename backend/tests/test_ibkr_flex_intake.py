"""Synthetic Flex exports only: no credentials, broker calls, or ledger writes."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from xml.etree import ElementTree as ET

import pytest

from app.services import ibkr_flex_intake as intake
from app.services.ibkr_flex_intake import FlexIntakeError, parse_activity_flex_xml
from scripts.inspect_ibkr_flex import main

ACCOUNT = "DUA000001"
ORG = "00000000-0000-4000-8000-000000000001"


def row(**changes):
    return dict(accountId=ACCOUNT, levelOfDetail="EXECUTION", ibExecID="execution-1",
                ibOrderID="123", assetCategory="STK", conid="12345", symbol="MSFT",
                quantity="2", tradePrice="100.25", multiplier="1", buySell="BUY",
                currency="USD", ibCommission="-0.35", ibCommissionCurrency="USD",
                dateTime="20261027;150000", transactionType="ExchTrade") | changes


def xml(rows=None, *, start="20261027", end="20261027", account=ACCOUNT):
    root = ET.Element("FlexQueryResponse", type="AF", queryName="Synthetic test")
    group = ET.SubElement(root, "FlexStatements", count="1")
    statement = ET.SubElement(group, "FlexStatement", accountId=account, fromDate=start, toDate=end)
    trades = ET.SubElement(statement, "Trades")
    for attrs in [row()] if rows is None else rows:
        ET.SubElement(trades, "Trade", attrs)
    return ET.tostring(root, encoding="utf-8")


def parse(payload=None, **options):
    return parse_activity_flex_xml(xml() if payload is None else payload,
        **(dict(expected_account=ACCOUNT, organization_id=ORG, source_timezone="UTC") | options))


def rejected(payload, code, **options):
    with pytest.raises(FlexIntakeError) as error:
        parse(payload, **options)
    assert error.value.code == code
    assert ACCOUNT not in str(error.value)
    return error.value


def test_stock_and_option_preserve_contract_and_decimal_economics():
    result = parse(xml([row(quantity="1.125"), row(ibExecID="execution-2", assetCategory="OPT",
        symbol="SPY option", underlyingSymbol="SPY", conid="54321", multiplier="100",
        buySell="SELL", quantity="-2", putCall="P", strike="500.50", expiry="20261218")]))
    stock, option = result.executions
    assert stock.signed_quantity == Decimal("1.125") and stock.multiplier == 1
    assert option.signed_quantity == -2 and option.price == Decimal("100.25")
    assert option.commission == Decimal("-0.35")
    assert option.option_type == "put" and option.expiration == "2026-12-18"
    assert option.underlying == "SPY" and option.contract_id == "54321"
    record = option.staging_record()
    assert record["signed_quantity"] == "-2" and record["multiplier"] == "100"
    assert record["strike"] == "500.5" and record["source_authenticated"] is False
    assert record["account"] == ACCOUNT and record["organization"] == ORG
    assert stock.staging_record()["strike"] is None
    summary = result.summary()
    assert summary["verified"] is summary["history_coverage_verified"] is summary["entry_authorized"] is False
    assert summary["source_authenticated"] is False
    assert ACCOUNT not in json.dumps(summary) and ORG not in json.dumps(summary)
    assert summary["asset_classes"] == ["OPT", "STK"]


def test_call_and_positive_sell_quantity():
    record = parse(xml([row(assetCategory="OPT", underlyingSymbol="SPY", multiplier="100",
        buySell="SELL", putCall="C", strike="500", expiry="20261218")])).executions[0]
    assert record.option_type == "call" and record.signed_quantity == -2


def test_identical_duplicate_is_once_but_conflict_rejects_whole_batch():
    report = parse(xml([row(), row()]))
    assert report.rows_received == 2 and len(report.executions) == 1
    assert report.summary()["identical_repeats"] == 1
    assert rejected(xml([row(), row(tradePrice="100.26")]), "conflicting_execution_requires_review").row == 2
    rejected(xml([row(description="first"), row(description="second")]), "conflicting_execution_requires_review")


def test_empty_execution_section_is_not_proof_of_no_activity():
    result = parse(xml([]))
    assert not result.executions and result.summary()["verified"] is False
    assert result.end == datetime(2026, 10, 28, tzinfo=timezone.utc)


def test_half_open_period_and_explicit_timezone():
    report = parse(xml([row(dateTime="20261027;235959")]), source_timezone="America/New_York")
    assert report.start == datetime(2026, 10, 27, 4, tzinfo=timezone.utc)
    assert report.end == datetime(2026, 10, 28, 4, tzinfo=timezone.utc)
    assert report.executions[0].filled_at == datetime(2026, 10, 28, 3, 59, 59, tzinfo=timezone.utc)
    rejected(xml([row(dateTime="20261028;000000")]), "execution_outside_declared_period")
    rejected(xml([row(dateTime="20261026;235959")]), "execution_outside_declared_period")


@pytest.mark.parametrize("day,clock", [("20261101", "013000"), ("20260308", "023000")])
def test_dst_offsets_are_not_guessed(day, clock):
    rejected(xml([row(dateTime=f"{day};{clock}")], start=day, end=day),
             "timestamp_ambiguous_or_nonexistent", source_timezone="America/New_York")


@pytest.mark.parametrize("changes,code", [
    ({"accountId":"OTHER"},"row_account_mismatch"),
    ({"levelOfDetail":"SYMBOL_SUMMARY"},"execution_level_required"),
    ({"levelOfDetail":"CLOSED_LOT"},"execution_level_required"),
    ({"transactionType":"Cancel"},"transaction_type_requires_review"),
    ({"origTradeID":"123"},"correction_requires_review"),
    ({"origOrderID":"123"},"correction_requires_review"),
    ({"origTransactionID":"123"},"correction_requires_review"),
    ({"origTradeDate":"20261026"},"correction_requires_review"),
    ({"origTradePrice":"100"},"correction_requires_review"),
    ({"changeInPrice":"1"},"correction_requires_review"),
    ({"changeInQuantity":"-1"},"correction_requires_review"),
    ({"ibExecID":"aa.bb.cc.02"},"execution_revision_requires_review"),
    ({"assetCategory":"FUT"},"unsupported_asset_class"),
    ({"conid":"0"},"contract_identity_invalid"),
    ({"conid":"abc"},"contract_identity_invalid"),
    ({"quantity":"0"},"execution_side_or_quantity_invalid"),
    ({"quantity":"-1","buySell":"BUY"},"execution_side_or_quantity_invalid"),
    ({"buySell":"HOLD"},"execution_side_or_quantity_invalid"),
    ({"tradePrice":"0"},"price_or_multiplier_invalid"),
    ({"multiplier":"0"},"price_or_multiplier_invalid"),
    ({"multiplier":"100"},"stock_multiplier_invalid"),
    ({"putCall":"C"},"stock_option_fields_conflict"),
    ({"expiry":"20261218"},"stock_option_fields_conflict"),
    ({"strike":"100"},"stock_option_fields_conflict"),
    ({"currency":"usd"},"currency_invalid"),
    ({"ibCommissionCurrency":"US"},"currency_invalid"),
    ({"dateTime":"20261027"},"execution_datetime_required"),
    ({"dateTime":"20261027;250000"},"execution_time_invalid"),
    ({"dateTime":"20260230;150000"},"date_invalid"),
    ({"quantity":"NaN"},"numeric_field_invalid"),
    ({"tradePrice":"Infinity"},"numeric_field_invalid"),
    ({"tradePrice":"1e1000000"},"numeric_field_invalid"),
    ({"ibCommission":"bad"},"numeric_field_invalid"),
    ({"ibExecID":""},"required_field_invalid"),
    ({"symbol":"A"*129},"required_field_invalid"),
    ({"ibOrderID":"x\nx"},"required_field_invalid"),
])
def test_malformed_rows_are_rejected_without_private_values(changes, code):
    rejected(xml([row(**changes)]), code)


@pytest.mark.parametrize("field", ["ibExecID","ibOrderID","dateTime","tradePrice","conid","multiplier","ibCommissionCurrency","transactionType"])
def test_required_columns_cannot_be_silently_omitted(field):
    attrs = row()
    attrs.pop(field)
    rejected(xml([attrs]), "required_field_invalid")


@pytest.mark.parametrize("changes,code", [
    ({"quantity":"1.5"},"option_quantity_invalid"),
    ({"strike":"0"},"option_contract_invalid"),
    ({"putCall":"X"},"option_contract_invalid"),
    ({"expiry":"20200101"},"option_expired_before_execution"),
    ({"expiry":"2026-12-18"},"date_format_invalid"),
    ({"expiry":"20260230"},"date_invalid"),
])
def test_option_contract_is_not_reduced_to_underlying(changes, code):
    attrs = row(assetCategory="OPT", multiplier="100", putCall="C", strike="500", expiry="20261218", underlyingSymbol="SPY")
    attrs.update(changes)
    rejected(xml([attrs]), code)


def test_potential_combo_execution_revision_requires_review():
    rejected(xml([row(ibExecID="a.b.c.01.01"),row(ibExecID="a.b.c.01.02")]), "execution_family_requires_review")
    assert parse(xml([row(ibExecID="a.b.c.01")])).executions[0].execution_id == "a.b.c.01"


@pytest.mark.parametrize("payload", [b"", b"x"*(intake.MAX_BYTES+1), "text", None])
def test_bytes_and_payload_bounds(payload):
    with pytest.raises(FlexIntakeError, match="payload_size_or_type_invalid"):
        parse_activity_flex_xml(payload, expected_account=ACCOUNT, organization_id=ORG, source_timezone="UTC")


@pytest.mark.parametrize("payload,code", [
    (b"<broken", "xml_invalid"), (b"\xff", "xml_invalid"),
    (b'<!DOCTYPE x><x/>', "xml_declarations_forbidden"),
    (b'<!ENTITY x SYSTEM "file:///private"><x/>', "xml_declarations_forbidden"),
    (b'<FlexStatement/>', "activity_flex_report_required"),
    (b'<FlexQueryResponse type="TC"/>', "activity_flex_report_required"),
    (b'<FlexQueryResponse type="AF"/>', "statement_container_invalid"),
    (b'<FlexQueryResponse type="AF"><FlexStatements count="1"><Other/></FlexStatements></FlexQueryResponse>', "statement_container_invalid"),
    (b'<FlexQueryResponse type="AF"><FlexStatements count="0"/></FlexQueryResponse>', "single_statement_required"),
])
def test_wrong_or_unsafe_documents(payload, code):
    rejected(payload, code)


def test_summary_only_duplicate_sections_and_mixed_accounts_are_not_empty_books():
    root = ET.fromstring(xml())
    statement = root.find("FlexStatements/FlexStatement")
    statement.remove(statement.find("Trades"))
    ET.SubElement(statement,"OpenPositions")
    rejected(ET.tostring(root),"trades_section_required")
    ET.SubElement(statement,"Trades")
    ET.SubElement(statement,"Trades")
    rejected(ET.tostring(root),"trades_section_required")
    root = ET.fromstring(xml())
    ET.SubElement(root[0],"FlexStatement",accountId="OTHER")
    rejected(ET.tostring(root),"single_statement_required")
    rejected(xml(account="OTHER"),"statement_account_mismatch")


def test_unsupported_row_and_nested_contents_are_not_ignored():
    root = ET.fromstring(xml())
    trades = root.find("FlexStatements/FlexStatement/Trades")
    trades[0].tag="Order"
    rejected(ET.tostring(root),"unsupported_trade_row")
    trades[0].tag="Trade"
    ET.SubElement(trades[0],"Nested")
    rejected(ET.tostring(root),"unsupported_trade_row")
    trades[0].remove(trades[0][0])
    trades[0].text="unrecognized correction"
    rejected(ET.tostring(root),"unsupported_trade_row")


def test_row_limit_and_report_digest():
    root = ET.fromstring(xml([]))
    trades=root.find("FlexStatements/FlexStatement/Trades")
    for _ in range(intake.MAX_ROWS+1):
        ET.SubElement(trades,"Trade")
    rejected(ET.tostring(root),"row_limit_exceeded")
    assert parse().source_digest != parse(b"\xef\xbb\xbf"+xml()).source_digest


@pytest.mark.parametrize("options,code", [
    ({"expected_account":""},"expected_account_required"),
    ({"expected_account":None},"expected_account_required"),
    ({"expected_account":"private\nvalue"},"expected_account_required"),
    ({"organization_id":"unknown"},"import_context_invalid"),
    ({"organization_id":None},"import_context_invalid"),
    ({"source_timezone":"unknown/zone"},"import_context_invalid"),
    ({"source_timezone":None},"import_context_invalid"),
])
def test_scope_and_timezone_are_explicit(options, code):
    rejected(xml(),code,**options)


@pytest.mark.parametrize("start,end,code", [
    ("20261028","20261027","declared_period_invalid"),
    ("20261027","99991231","declared_period_invalid"),
    ("2026-10-27","20261027","date_format_invalid"),
    ("20260230","20261027","date_invalid"),
])
def test_declared_period_is_validated(start,end,code):
    rejected(xml(start=start,end=end),code)


def args(path):
    return ["--file",str(path),"--expected-account",ACCOUNT,"--organization",ORG,"--timezone","UTC"]


def test_cli_only_prints_safe_summary_and_never_writes_files(tmp_path,capsys):
    path=tmp_path/"private.xml"
    path.write_bytes(xml())
    assert main(args(path))==0
    output=capsys.readouterr()
    assert json.loads(output.out)["state"]=="parsed_unverified"
    assert ACCOUNT not in output.out and "execution-1" not in output.out and ORG not in output.out
    assert list(tmp_path.iterdir())==[path]
    path.write_bytes(b'<private account="sensitive"')
    assert main(args(path))==2
    assert "sensitive" not in capsys.readouterr().out
    assert main(args(tmp_path/"missing"))==2
    assert json.loads(capsys.readouterr().out)["reason"]=="report_unreadable"


def test_cli_invalid_arguments_do_not_echo_secrets(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--unknown-token","do-not-echo-this"])
    assert error.value.code==2
    output=capsys.readouterr()
    assert "do-not-echo-this" not in output.err
    assert json.loads(output.err)["reason"]=="invalid_arguments"


@pytest.mark.parametrize("notes", ["Ca", "Co", "O;Ca;P", "Co, P"])
def test_visible_broker_cancel_and_correction_codes_require_review(notes):
    rejected(xml([row(notes=notes)]), "cancel_or_correction_requires_review")


def test_closing_trade_code_is_not_a_cancel_and_zero_change_columns_are_valid():
    result = parse(xml([row(notes="C;P", changeInPrice="0", changeInQuantity="0", origTradePrice="0")]))
    assert len(result.executions) == 1


def test_utf16_without_bom_cannot_bypass_entity_rejection():
    text = xml().decode().replace('ibExecID="execution-1"', 'ibExecID="&hidden;"')
    declaration = '<?xml version="1.0" encoding="UTF-16"?>'
    doctype = '<!DOCTYPE FlexQueryResponse [<!ENTITY hidden "execution-1">]>'
    rejected((declaration + doctype + text).encode("utf-16-le"), "xml_invalid")


def test_non_utf8_xml_encoding_declaration_is_not_accepted():
    rejected(b'<?xml version="1.0" encoding="ISO-8859-1"?>' + xml(), "xml_encoding_unsupported")


@pytest.mark.parametrize("declaration", [b'<?xml version="1.0"?>', b'<?xml version="1.0" encoding="UTF-8"?>'])
def test_supported_encoding_declarations(declaration):
    assert len(parse(declaration + xml()).executions) == 1


def test_caller_flags_and_a_parsed_upload_cannot_satisfy_entry_authority():
    from app.services.entry_submission_boundary import check_entry_before_submission
    root = ET.fromstring(xml([row(verified="true", clean="true", source_authenticated="true")]))
    root.set("coverageVerified", "true")
    result = parse(ET.tostring(root))
    assert result.summary()["verified"] is False
    assert result.executions[0].staging_record()["source_authenticated"] is False
    decision = check_entry_before_submission(
        intent="open", reconciliation=result,
        now=datetime(2026, 10, 27, 16, tzinfo=timezone.utc),
        kill_switch_engaged=False, account_scope_authenticated=True,
    )
    assert decision.allowed is False
    assert decision.reason == "reconciliation_evidence_invalid"
