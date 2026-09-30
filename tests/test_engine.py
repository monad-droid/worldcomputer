from datetime import datetime, timezone
from decimal import Decimal as D

from cryptotax.engine import Engine
from cryptotax.models import FEE, PERP_PNL, Movement


def t(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def mv(ts, acct, asset, qty, tx, usd=None, kind="trade"):
    return Movement(ts=ts, account=acct, asset=asset, qty=D(str(qty)), tx_id=tx,
                    kind=kind, usd=None if usd is None else D(str(usd)), symbol=asset)


def test_buy_then_sell_fifo_and_term():
    r = Engine().run([
        mv(t(2023, 1, 1), "cb", "USD", -1000, "b1", 1000), mv(t(2023, 1, 1), "cb", "ETH", 1, "b1", 1000),
        mv(t(2025, 3, 1), "cb", "USD", -3000, "b2", 3000), mv(t(2025, 3, 1), "cb", "ETH", 1, "b2", 3000),
        mv(t(2025, 6, 1), "cb", "ETH", -1.5, "s1", 3000), mv(t(2025, 6, 1), "cb", "USD", 3000, "s1", 3000),
    ])
    eth = [d for d in r.disposals if d.asset == "ETH"]
    assert [d.qty for d in eth] == [D(1), D("0.5")]
    assert eth[0].gain == D(1000) and eth[0].long_term
    assert eth[1].gain == D(-500) and not eth[1].long_term


def test_transfer_between_own_wallets_keeps_basis_and_date():
    r = Engine().run([
        mv(t(2024, 1, 1), "cb", "USD", -2000, "b", 2000), mv(t(2024, 1, 1), "cb", "ETH", 1, "b", 2000),
        # withdrawal: exchange side and chain side are different records, 1% fee
        mv(t(2024, 2, 1, 10), "cb", "ETH", -1, "w-cb", 3000),
        mv(t(2024, 2, 1, 11), "1:0xa", "ETH", "0.99", "w-chain", 2970),
        mv(t(2025, 5, 1), "1:0xa", "ETH", "-0.99", "s", 1500), mv(t(2025, 5, 1), "1:0xa", "USDC", 1500, "s", 1500),
    ])
    assert len(r.transfers) == 1
    d = [d for d in r.disposals if d.asset == "ETH"][0]
    assert d.basis == D(1980) and d.acquired == t(2024, 1, 1) and d.long_term
    assert d.gain == D(-480)
    assert not [x for x in r.reviews if x.reason.startswith("unmatched")]


def test_missing_basis_is_flagged_not_silent():
    r = Engine().run([
        mv(t(2025, 1, 5), "1:0xa", "PEPE", -100, "s", 50), mv(t(2025, 1, 5), "1:0xa", "USDC", 50, "s", 50),
    ])
    d = r.disposals[0]
    assert "missing_basis" in d.flags and d.basis == 0
    assert any(x.reason.startswith("missing_basis") for x in r.reviews)


def test_wrap_nets_to_zero_and_gas_is_fee():
    r = Engine().run([
        mv(t(2024, 1, 1), "1:0xa", "ETH", 2, "in", 4000),
        # wrap 1 ETH (WETH canonicalized to ETH) + gas
        mv(t(2024, 2, 1), "1:0xa", "ETH", -1, "wrap", 2500), mv(t(2024, 2, 1), "1:0xa", "ETH", 1, "wrap", 2500),
        mv(t(2024, 2, 1), "1:0xa", "ETH", "-0.001", "wrap", "2.5", kind=FEE),
    ])
    assert [d.kind for d in r.disposals] == ["fee"]
    lots = r.lots[("1:0xa", "ETH")]
    assert sum(l.qty for l in lots) == D("1.999")


def test_perp_pnl_recorded():
    r = Engine().run([
        mv(t(2025, 2, 1), "hl:0xa", "USDC", -1200, "f1", -1200, kind=PERP_PNL),
    ])
    assert r.disposals[0].gain == D(-1200)


def test_snapshot_at_2025():
    snap = t(2025, 1, 1)
    r = Engine([snap]).run([
        mv(t(2024, 1, 1), "1:0xa", "ETH", 1, "in", 2000),
        mv(t(2025, 2, 1), "1:0xa", "ETH", 1, "in2", 3000),
    ])
    assert r.snapshots[snap][("1:0xa", "ETH")] == (D(1), D(2000))


def test_fiat_is_not_a_lot():
    r = Engine().run([
        mv(t(2025, 1, 1), "cb", "USD", 5000, "dep", 5000),
        mv(t(2025, 1, 2), "cb", "USD", -1000, "b", 1000), mv(t(2025, 1, 2), "cb", "ETH", 1, "b", 1000),
        mv(t(2025, 2, 1), "cb", "ETH", -1, "s", 800), mv(t(2025, 2, 1), "cb", "USD", 800, "s", 800),
    ])
    assert [(d.asset, d.gain) for d in r.disposals] == [("ETH", D(-200))]
    assert not r.reviews
