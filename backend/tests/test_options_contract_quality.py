from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
import pytest
from app.broker.broker_interface import OptionContract, Greeks
from app.services.options_contract_quality import score_spread_quality

NOW=datetime(2026,11,6,15,tzinfo=timezone.utc)


def leg(strike, bid, ask, delta, kind="put"):
    return OptionContract(symbol=f"SPY-{kind}-{strike}",underlying="SPY",expiration=date(2026,12,18),
        strike=Decimal(str(strike)),option_type=kind,bid=Decimal(str(bid)),ask=Decimal(str(ask)),last=0,
        volume=0,open_interest=0,quote_timestamp=NOW,quote_feed="opra",
        greeks=Greeks(delta=delta,gamma=.01,theta=-.04,vega=.1,implied_vol=.3))


def pair(): return leg(500,2.00,2.04,-.20),leg(490,1.00,1.02,-.10)


def test_credit_target_is_020_not_long_option_055():
    short,long=pair();r=score_spread_quality(short,long,"bull_put_spread",now=NOW)
    assert r["state"]=="scored" and 0<=r["score"]<=100 and r["target_delta"]==.20
    assert r["delta_leg"]=="short" and r["max_loss_per_contract"]==899.0
    assert r["execution_authorized"] is False and r["experimental"] is True
    short.greeks.delta=-.55
    assert score_spread_quality(short,long,"bull_put_spread",now=NOW)["score"]<r["score"]


def test_long_debit_targets_long_leg_and_bear_put_shape_supported():
    short=leg(510,1.00,1.02,.20,"call");long=leg(500,2.00,2.04,.55,"call")
    # Bull call: long below short. This is intentionally distinct from the
    # bearish credit call shape.
    r=score_spread_quality(short,long,"bull_call_debit_spread",now=NOW)
    assert r["state"]=="scored" and r["delta_leg"]=="long" and r["target_delta"]==.55
    assert r["max_loss_per_contract"]==101.0
    short=leg(490,1.00,1.02,-.2);long=leg(500,2.00,2.04,-.55)
    assert score_spread_quality(short,long,"bear_put_spread",now=NOW)["state"]=="scored"


@pytest.mark.parametrize("change,reason",[("feed","indicative_or_unverified_feed"),("time","quote_time_unknown"),("naive","quote_time_unknown"),("stale","stale_or_future_quote"),("future","stale_or_future_quote"),("nan","invalid_quote"),("crossed","invalid_quote"),("zero","invalid_quote"),("wide","spread_too_wide"),("delta","invalid_or_missing_delta"),("greeks","invalid_or_missing_delta"),("identity","incompatible_legs"),("expiry","expired_contract"),("kind","strategy_leg_type_mismatch"),("strike","invalid_strikes"),("order","strategy_strike_order_mismatch"),("economics","invalid_spread_economics")])
def test_invalid_data_never_gets_a_numeric_score(change,reason):
    short,long=pair()
    if change=="feed":long.quote_feed="indicative"
    if change=="time":long.quote_timestamp=None
    if change=="naive":long.quote_timestamp=NOW.replace(tzinfo=None)
    if change=="stale":long.quote_timestamp=NOW-timedelta(seconds=61)
    if change=="future":long.quote_timestamp=NOW+timedelta(seconds=6)
    if change=="nan":long.bid=Decimal("NaN")
    if change=="crossed":long.ask=Decimal(".5")
    if change=="zero":long.bid=Decimal("0")
    if change=="wide":long.ask=Decimal("2")
    if change=="delta":short.greeks.delta=float("nan")
    if change=="greeks":short.greeks=None
    if change=="identity":long.symbol=short.symbol
    if change=="expiry":short.expiration=long.expiration=date(2026,1,1)
    if change=="kind":short.option_type=long.option_type="call"
    if change=="strike":short.strike=Decimal("NaN")
    if change=="order":short.strike=Decimal("480")
    if change=="economics":long.bid=Decimal("3");long.ask=Decimal("3.02")
    r=score_spread_quality(short,long,"bull_put_spread",now=NOW)
    assert r["score"] is None and r["reason"]==reason and not r["execution_authorized"]


@pytest.mark.parametrize("delta",[None,False,"bad",float("inf"),0,2,.2])
def test_invalid_or_wrong_sign_put_delta(delta):
    short,long=pair();short.greeks.delta=delta
    assert score_spread_quality(short,long,"bull_put_spread",now=NOW)["score"] is None


@pytest.mark.parametrize("limits",[{"max_spread_pct":0},{"max_spread_pct":float("nan")},{"max_age_seconds":0},{"max_age_seconds":True}])
def test_invalid_limits_rejected(limits):
    with pytest.raises(ValueError):score_spread_quality(*pair(),"bull_put_spread",now=NOW,**limits)


def test_unknown_strategy_and_naive_clock():
    assert score_spread_quality(*pair(),"iron_condor",now=NOW)["reason"]=="unsupported_strategy"
    with pytest.raises(ValueError):score_spread_quality(*pair(),"bull_put_spread",now=NOW.replace(tzinfo=None))


def test_locked_quotes_valid_and_wider_long_leg_scores_lower():
    short,long=pair();short.ask=short.bid;long.ask=long.bid
    r=score_spread_quality(short,long,"bull_put_spread",now=NOW)
    assert r["score"]==100
    long.ask=Decimal("1.08")
    assert score_spread_quality(short,long,"bull_put_spread",now=NOW)["score"]<100
