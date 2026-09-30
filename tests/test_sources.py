"""Normalizer tests on hand-built rows shaped like the documented API responses.
These check our parsing logic; they do not prove the live API matches."""
from decimal import Decimal as D

from cryptotax.engine import Engine
from cryptotax.sources import etherscan, hyperliquid
from cryptotax.wallets import load

A = "0x" + "a" * 40
B = "0x" + "b" * 40
X = "0x" + "c" * 40  # not ours
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


def test_etherscan_transfer_between_own_wallets_seen_twice_is_one_transfer():
    own = {A, B}
    normal = [{"hash": "0x1", "from": A, "to": B, "value": str(10**18), "gasUsed": "21000",
               "gasPrice": str(10**9), "isError": "0", "timeStamp": "1735776000"}]
    # Fetching A and B both return the same tx.
    m = etherscan.normalize(8453, own, normal, [], []) + etherscan.normalize(8453, own, normal, [], [])
    for x in m:
        x.usd = abs(x.qty) * 3000
    r = Engine().run(m)
    assert len(r.transfers) == 1
    assert [d.kind for d in r.disposals] == ["fee"]  # only gas


def test_etherscan_token_swap_and_canonical_usdc():
    own = {A}
    tokens = [
        {"hash": "0x2", "from": A, "to": X, "value": str(500 * 10**6), "tokenDecimal": "6",
         "contractAddress": USDC_BASE, "tokenSymbol": "USDC", "logIndex": "1", "timeStamp": "1735776000"},
        {"hash": "0x2", "from": X, "to": A, "value": str(10**18), "tokenDecimal": "18",
         "contractAddress": "0x" + "d" * 40, "tokenSymbol": "FOO", "logIndex": "2", "timeStamp": "1735776000"},
    ]
    m = etherscan.normalize(8453, own, [], [], tokens)
    assert {x.asset for x in m} == {"USDC", "8453:0x" + "d" * 40}


def test_hyperliquid_perp_fill_and_deposit():
    fills = [{"coin": "ETH", "px": "3000", "sz": "1", "side": "A", "time": 1735776000000, "closedPnl": "-250",
              "fee": "1.5", "feeToken": "USDC", "hash": "0xh", "tid": 1}]
    ledger = [{"delta": {"type": "deposit", "usdc": "1000"}, "hash": "0xd", "time": 1735700000000}]
    m = hyperliquid.normalize(A, {A}, fills, [], ledger, {})
    pnl = [x for x in m if x.kind == "perp_pnl"][0]
    assert pnl.qty == D("-251.5")


def test_wallet_list_parsing(tmp_path):
    p = tmp_path / "w.txt"
    p.write_text(f"address,label\n{A.upper().replace('0X', '0x')}, main\n{A}\n"
                 "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU\njunk\n")
    ws = load(p)
    assert [(w.kind, w.label) for w in ws] == [("evm", "main"), ("solana", "")]
