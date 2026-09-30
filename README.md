# cryptotax

Rebuilds crypto cost basis and realized gains/losses **from the blockchains
themselves**, wallet by wallet, and shows where the numbers are uncertain.
It runs on your own machine; wallet lists, exports and results stay in
`data/` and `out/`, which are git-ignored.

Status: early. Built: EVM chains (Etherscan V2), Hyperliquid, pricing, lot
engine, reports. Not built yet: Solana, Lighter, Coinbase/Kraken/Robinhood
CSV importers (they will be written against your real export files, not
guessed formats).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Get a free Etherscan key at etherscan.io/myapikey; one key covers all chains. The app stores it in `data/settings.json`; the command line reads `ETHERSCAN_API_KEY`.

### The app (recommended)

```bash
python -m cryptotax ui
```

This opens http://127.0.0.1:8765 in your browser. It runs only on your computer.
1. **Wallets**: paste your Etherscan key and your addresses, and mark each one Mine / Not sure / Not mine.
2. **Fetch**: pick chains, then press Start. Progress and any failures show live.
3. **Results**: press Calculate. Totals, the review list, Form 8949 rows, per-account nets and Jan 1 holdings, with CSV downloads.

### Command line (same engine)

Put your addresses in `data/wallets.txt`, one per line (optional label after
a comma). 0x addresses are scanned on every chain in `DEFAULT_CHAINS` and on
Hyperliquid; base58 addresses are treated as Solana.

```bash
python -m cryptotax fetch --wallets data/wallets.txt     # slow the first time; cached after
python -m cryptotax report --year 2025
```

Outputs in `out/`:
- `summary_2025.md` – gains/losses by bucket, review totals, net by account,
  and holdings per account at 2025-01-01.
- `disposals_2025.csv` – one row per lot sold (Form 8949 fields).
- `review_2025.csv` – every guess the engine made, largest dollars first.

## How it computes (and the assumptions to check with a CPA)

| Topic | What the code does | Why / risk |
|---|---|---|
| Wallet-by-wallet | Each chain+address, exchange, and perp venue keeps its own lots, for all years | Required from 1/1/2025 (Rev. Proc. 2024-28). Replaying history per wallet yields the "actual" position at 1/1/2025, which is what applies if no safe-harbor allocation was made. Earlier years may have been filed on a pooled basis; this tool does not re-file those. |
| Lot method | FIFO within each wallet | FIFO is the default. Specific ID needs identification at the time of sale. |
| Own-wallet transfers | Move lots with original date and basis; never taxed | Matched by same tx, or by same asset, amount within 3%, received within 30 min before to 3 days after the send |
| Swaps | Dispose outgoing at the event's USD value, and use that value as basis for incoming | Stablecoin/ETH/BTC/SOL side preferred when both are priced |
| Gas | Disposal of the gas token at market value. On swaps it is added to the acquired asset's basis; otherwise it is recorded as a non-deductible fee | Conservative |
| WETH | Treated as ETH, so wrapping is a non-event | Common practice; IRS has not ruled |
| DeFi deposits that return a receipt token (aTokens, LP tokens) | Taxed as swaps | Aggressive for gains, favorable for losses; many treat these as non-taxable. Review. |
| Sends to unknown addresses | Lots removed, no gain/loss, listed for review | Could be a payment (taxable), a gift, a bridge, or a wallet missing from your list |
| Receipts from unknown addresses | Basis = market value, listed for review | If income, it is taxable as ordinary income at that value |
| Unpriced tokens | $0, listed for review | Usually spam |
| Selling more than a wallet holds | Excess gets $0 basis, flagged `missing_basis` | This is the usual cause of losses disappearing in other tools |
| Hyperliquid perps | Realized PnL minus fees and funding reported as short-term gain/loss | Not regulated futures, so Section 1256 is unlikely to apply. Confirm with a CPA. |
| Dates | UTC | Your local-date tax year could move trades near midnight on Dec 31/Jan 1 |

## Known data gaps

- Hyperliquid's API returns at most the 10,000 most recent fills per address.
  If you hit that, older fills need Hyperliquid's CSV export.
- L2 L1-data fees may be missing from gas.
- NFTs are not handled.
- Chains the Etherscan free tier does not cover are listed as fetch failures.
