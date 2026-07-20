"""Token holdings analysis & pool account identification.

1. Extract unique account addresses from Transfer events
2. Query token balance for each account
3. Identify and annotate pool addresses
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3

from ..client import get_contract
from ..models import VerifiedPool


def analyze_holdings(
    w3: Web3,
    token_address: str,
    token_decimals: int,
    transfer_events: list[dict],
    verified_pools: list[VerifiedPool],
    from_block: int,
    to_block: int,
    output_dir: str | Path = "output",
) -> dict[str, Any]:
    """Run the full holdings analysis pipeline.

    Steps:
      1. Extract unique addresses from transfer events
      2. Query balanceOf for each address
      3. Identify pool addresses among them
      4. Store results as JSON + CSV tables
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Extract unique addresses ---
    unique_addresses: set[str] = set()
    address_tx_count: dict[str, int] = defaultdict(int)
    address_first_seen: dict[str, int] = {}
    address_last_seen: dict[str, int] = {}

    for evt in transfer_events:
        actor = evt.get("actor", "")
        recipient = evt.get("recipient", "")
        bn = evt.get("block_number", 0)
        if actor and actor != "0x0000000000000000000000000000000000000000":
            unique_addresses.add(actor)
            address_tx_count[actor] += 1
            if actor not in address_first_seen or bn < address_first_seen[actor]:
                address_first_seen[actor] = bn
            if actor not in address_last_seen or bn > address_last_seen[actor]:
                address_last_seen[actor] = bn
        if recipient and recipient != "0x0000000000000000000000000000000000000000":
            unique_addresses.add(recipient)
            address_tx_count[recipient] += 1
            if recipient not in address_first_seen or bn < address_first_seen[recipient]:
                address_first_seen[recipient] = bn
            if recipient not in address_last_seen or bn > address_last_seen[recipient]:
                address_last_seen[recipient] = bn

    # --- Step 2: Query balanceOf for each address ---
    token_contract = get_contract(w3, token_address, "erc20")
    balances: dict[str, str] = {}
    query_timestamp = int(datetime.now(timezone.utc).timestamp())

    for addr in sorted(unique_addresses):
        try:
            checksum_addr = w3.to_checksum_address(addr)
            bal = token_contract.functions.balanceOf(checksum_addr).call()
            balances[addr] = str(bal)
        except Exception:
            balances[addr] = "0"

    # --- Step 3: Identify pool addresses ---
    pool_addresses: set[str] = set()
    for p in verified_pools:
        pool_addresses.add(p.pool_address.lower())
        if p.custody_address:
            pool_addresses.add(p.custody_address.lower())

    pool_by_addr: dict[str, VerifiedPool] = {}
    for p in verified_pools:
        pool_by_addr[p.pool_address.lower()] = p
        if p.custody_address:
            pool_by_addr[p.custody_address.lower()] = p

    # --- Build holdings table ---
    holdings_rows: list[dict[str, Any]] = []
    for addr in sorted(unique_addresses):
        bal_raw = balances.get(addr, "0")
        try:
            bal_decimal = int(bal_raw) / (10 ** token_decimals)
        except (ValueError, TypeError):
            bal_decimal = 0.0
        is_pool = addr.lower() in pool_addresses
        pool_info = pool_by_addr.get(addr.lower())
        pool_label = ""
        if is_pool and pool_info:
            pool_label = "{} {}".format(pool_info.protocol, pool_info.version).upper()

        holdings_rows.append({
            "address": addr,
            "balance_raw": bal_raw,
            "balance_decimal": round(bal_decimal, 6),
            "is_pool": is_pool,
            "pool_label": pool_label,
            "tx_count": address_tx_count.get(addr, 0),
            "first_seen_block": address_first_seen.get(addr, 0),
            "last_seen_block": address_last_seen.get(addr, 0),
            "query_timestamp": query_timestamp,
        })

    holdings_rows.sort(key=lambda r: r["balance_decimal"], reverse=True)

    # --- Build pool identification table ---
    pool_rows: list[dict[str, Any]] = []
    for p in verified_pools:
        pool_addr_lower = p.pool_address.lower()
        holder_info = next(
            (r for r in holdings_rows if r["address"].lower() == pool_addr_lower),
            None,
        )
        pool_rows.append({
            "pool_address": p.pool_address,
            "protocol": p.protocol,
            "version": p.version,
            "token0": p.token0,
            "token1": p.token1,
            "fee": p.fee,
            "balance_raw": holder_info["balance_raw"] if holder_info else "0",
            "balance_decimal": holder_info["balance_decimal"] if holder_info else 0.0,
            "in_holders_list": holder_info is not None,
        })

    # --- Write outputs ---
    result = {
        "total_unique_addresses": len(unique_addresses),
        "query_timestamp": query_timestamp,
        "query_time_human": datetime.fromtimestamp(
            query_timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "holdings_count": len(holdings_rows),
        "pool_count": len(pool_rows),
        "holdings": holdings_rows,
        "pool_identification": pool_rows,
    }

    _write_json(out / "holdings.json", result)

    csv_holdings_path = out / "holdings_table.csv"
    with open(csv_holdings_path, "w", newline="") as f:
        if holdings_rows:
            writer = csv.DictWriter(f, fieldnames=list(holdings_rows[0].keys()))
            writer.writeheader()
            writer.writerows(holdings_rows)

    csv_pool_path = out / "pool_identification_table.csv"
    with open(csv_pool_path, "w", newline="") as f:
        if pool_rows:
            writer = csv.DictWriter(f, fieldnames=list(pool_rows[0].keys()))
            writer.writeheader()
            writer.writerows(pool_rows)

    return result


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
