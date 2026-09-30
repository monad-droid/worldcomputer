"""Canonical asset ids.

The same economic asset on different chains (ETH on mainnet and on L2s,
native USDC on each chain) must share one id so bridges are recognized as
transfers. Everything unknown is keyed by chainid:contract.

WETH is mapped to ETH, which makes wrapping/unwrapping a non-event. The IRS
has not ruled on wrapping; this is the common treatment. Set
TREAT_WETH_AS_ETH = False to treat wraps as taxable swaps instead.
"""
TREAT_WETH_AS_ETH = True

# chainid -> native asset
NATIVE = {
    1: "ETH", 10: "ETH", 42161: "ETH", 8453: "ETH", 59144: "ETH", 534352: "ETH",
    81457: "ETH", 324: "ETH", 7777777: "ETH", 130: "ETH", 57073: "ETH", 480: "ETH",
    137: "POL", 56: "BNB", 43114: "AVAX", 100: "XDAI", 250: "FTM", 5000: "MNT",
    80094: "BERA", 146: "S", 999: "HYPE",
}

# (chainid, lowercase contract) -> canonical id. Verified contract addresses only.
KNOWN = {
    # USDC (native Circle)
    (1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): "USDC",
    (42161, "0xaf88d065e77c8cc2239327c5edb3a432268e5831"): "USDC",
    (10, "0x0b2c639c533813f4aa9d7837caf62653d097ff85"): "USDC",
    (8453, "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): "USDC",
    (137, "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"): "USDC",
    # USDT
    (1, "0xdac17f958d2ee523a2206206994597c13d831ec7"): "USDT",
    (42161, "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"): "USDT",
    # DAI
    (1, "0x6b175474e89094c44da98b954eedeac495271d0f"): "DAI",
    # WETH
    (1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"): "WETH",
    (42161, "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"): "WETH",
    (10, "0x4200000000000000000000000000000000000006"): "WETH",
    (8453, "0x4200000000000000000000000000000000000006"): "WETH",
    # WBTC
    (1, "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"): "WBTC",
}

STABLES = {"USD", "USDC", "USDT", "DAI", "PYUSD", "USDE"}


def canonical(chainid: int, contract: str | None) -> str:
    if not contract:
        return NATIVE.get(chainid, f"{chainid}:native")
    cid = KNOWN.get((chainid, contract.lower()), f"{chainid}:{contract.lower()}")
    if cid == "WETH" and TREAT_WETH_AS_ETH:
        return "ETH"
    return cid
