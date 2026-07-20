"""Liquidity metrics — TVL, pool concentration, LP concentration, withdrawal severity, and price estimation."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from web3 import Web3

from ..client import get_contract
from ..models import NormalizedEvent, Position, VerifiedPool


def estimate_price_v2(
    pool: VerifiedPool,
    token_address: str,
    reserve0: int,
    reserve1: int,
    decimals0: int,
    decimals1: int,
) -> tuple[float, str]:
    """Estimate token price in quote-token terms from V2 reserves.

    Returns (price, quote_symbol).
    """
    target = Web3.to_checksum_address(token_address)
    t0 = Web3.to_checksum_address(pool.token0)
    t1 = Web3.to_checksum_address(pool.token1)

    if reserve0 <= 0 or reserve1 <= 0:
        price = 0.0
    elif target == t0:
        price = (reserve1 / 10 ** decimals1) / (reserve0 / 10 ** decimals0)
    else:
        price = (reserve0 / 10 ** decimals0) / (reserve1 / 10 ** decimals1)

    # Determine quote symbol
    partner_addr = t1 if target == t0 else t0
    quote_symbol = _guess_quote_symbol(partner_addr)
    return price, quote_symbol


def estimate_price_v3(
    sqrt_price_x96: int,
    token0_decimals: int,
    token1_decimals: int,
    token0_is_target: bool,
) -> float:
    """Estimate token price from V3 sqrtPriceX96.

    sqrtPriceX96 = sqrt(amount1 / amount0) * 2^96
    price = (sqrtPriceX96 / 2^96)^2 * 10^(dec0 - dec1)
    """
    if sqrt_price_x96 == 0:
        return 0.0
    price_ratio = (sqrt_price_x96 / 2 ** 96) ** 2
    if token0_is_target:
        # price = 1 / price_ratio, adjusted for decimals
        price = (1 / price_ratio) * (10 ** (token1_decimals - token0_decimals))
    else:
        price = price_ratio * (10 ** (token0_decimals - token1_decimals))
    return price if price > 0 else 0.0


def _guess_quote_symbol(addr: str) -> str:
    addr_lower = addr.lower()
    known = {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    }
    return known.get(addr_lower, "???")


def calculate_tvl_v2(
    pool: VerifiedPool, token_address: str, reserve0: int, reserve1: int
) -> float:
    """Calculate TVL in token terms for a V2 pool.

    Returns the value of the pool's liquidity expressed in the target token.
    """
    target = Web3.to_checksum_address(token_address)
    t0 = Web3.to_checksum_address(pool.token0)
    t1 = Web3.to_checksum_address(pool.token1)

    if target == t0:
        # TVL = token0 * 2 (because in a V2 AMM, value of both sides is equal)
        return reserve0 * 2
    else:
        return reserve1 * 2


def snapshot_onchain_pool_tvl(
    w3: Web3,
    verified_pools: list[VerifiedPool],
    target_token: str,
) -> dict[str, int]:
    """Read live pool balances/reserves and return TVL in target-token raw units."""
    target = Web3.to_checksum_address(target_token)
    token = get_contract(w3, target, "erc20")
    tvl_by_pool: dict[str, int] = {}

    for pool in verified_pools:
        if not pool.verified:
            continue
        pa = pool.pool_address
        try:
            if pool.version == "v2":
                pair = get_contract(w3, pa, "uniswap_v2_pair")
                reserve0, reserve1, _ = pair.functions.getReserves().call()
                tvl = int(calculate_tvl_v2(pool, target, int(reserve0), int(reserve1)))
            else:
                # V3 / others: target-token balance held by the pool contract
                bal = int(token.functions.balanceOf(Web3.to_checksum_address(pa)).call())
                # Approximate full-pool TVL as 2x the target side when target is in the pair
                t0 = Web3.to_checksum_address(pool.token0)
                t1 = Web3.to_checksum_address(pool.token1)
                if target in (t0, t1) and bal > 0:
                    tvl = bal * 2
                else:
                    tvl = bal
            if tvl > 0:
                tvl_by_pool[pa] = tvl
        except Exception:
            continue
    return tvl_by_pool


def build_tvl_timeline(
    verified_pools: list[VerifiedPool],
    events_all: list[dict],
    target_token: str,
    token_decimals: int,
) -> list[dict]:
    """Build a timeline of TVL and price estimates from events."""
    timeline: list[dict] = []
    target = Web3.to_checksum_address(target_token)

    # Group events by pool and type (normalize address case)
    pool_events: dict[str, list[dict]] = defaultdict(list)
    for evt in events_all:
        pa = (evt.get("pool_address") or "").lower()
        if pa and evt.get("event_type") in ("SWAP", "LIQUIDITY_ADD", "LIQUIDITY_REMOVE"):
            pool_events[pa].append(evt)

    for pool in verified_pools:
        if not pool.verified:
            continue
        pa = pool.pool_address.lower()
        events = pool_events.get(pa, [])
        if not events:
            continue
        events.sort(key=lambda e: (e["block_number"], e.get("log_index", 0)))

        t0 = Web3.to_checksum_address(pool.token0)
        t1 = Web3.to_checksum_address(pool.token1)
        target_is_t0 = (target == t0)

        if pool.version == "v2":
            reserve0 = 0
            reserve1 = 0
            for evt in events:
                bn = evt["block_number"]
                ts = evt["block_timestamp"]
                a0 = int(evt.get("token0_amount", "0") or "0")
                a1 = int(evt.get("token1_amount", "0") or "0")
                etype = evt["event_type"]
                source = evt.get("source_event", "")

                if source == "Sync":
                    reserve0 = a0
                    reserve1 = a1
                elif etype == "SWAP":
                    # token*_amount already signed: positive = into pool, negative = out
                    reserve0 = max(0, reserve0 + a0)
                    reserve1 = max(0, reserve1 + a1)
                elif etype == "LIQUIDITY_ADD":
                    reserve0 += abs(a0)
                    reserve1 += abs(a1)
                elif etype == "LIQUIDITY_REMOVE":
                    reserve0 = max(0, reserve0 - abs(a0))
                    reserve1 = max(0, reserve1 - abs(a1))

                tvl_in_token = (reserve0 * 2) if target_is_t0 else (reserve1 * 2)
                price, quote = estimate_price_v2(
                    pool, target_token, reserve0, reserve1, 18, 18
                )
                timeline.append({
                    "block_number": bn,
                    "block_timestamp": ts,
                    "pool_address": pool.pool_address,
                    "protocol": pool.protocol,
                    "version": pool.version,
                    "event_type": etype,
                    "source_event": source,
                    "reserve0": str(reserve0),
                    "reserve1": str(reserve1),
                    "tvl_in_token": str(tvl_in_token),
                    "price": round(price, 18),
                    "quote_symbol": quote,
                })

        elif pool.version == "v3":
            cum_liquidity = 0
            for evt in events:
                bn = evt["block_number"]
                ts = evt["block_timestamp"]
                a0 = abs(int(evt.get("token0_amount", "0") or "0"))
                a1 = abs(int(evt.get("token1_amount", "0") or "0"))
                etype = evt["event_type"]

                if etype in ("LIQUIDITY_ADD", "LIQUIDITY_REMOVE"):
                    delta = int(evt.get("liquidity_delta", "0") or "0")
                    cum_liquidity += delta
                    cum_liquidity = max(0, cum_liquidity)

                # Approximate TVL in target-token units from event amounts
                tvl_approx = (a0 * 2) if target_is_t0 else (a1 * 2)
                if etype == "SWAP":
                    tvl_approx = a0 if target_is_t0 else a1

                timeline.append({
                    "block_number": bn,
                    "block_timestamp": ts,
                    "pool_address": pool.pool_address,
                    "protocol": pool.protocol,
                    "version": pool.version,
                    "event_type": etype,
                    "source_event": evt.get("source_event", ""),
                    "liquidity": str(cum_liquidity),
                    "token0_amount": str(a0),
                    "token1_amount": str(a1),
                    "tvl_in_token": str(tvl_approx),
                    "price": 0.0,
                    "quote_symbol": "N/A",
                })

    return sorted(timeline, key=lambda e: (e["block_number"], e.get("log_index", 0)))


def calculate_pool_concentration(
    verified_pools: list[VerifiedPool],
    timeline: list[dict],
    onchain_tvl: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """Calculate main-pool dominance and concentration metrics.

    Prefers live on-chain TVL snapshot when available; falls back to timeline.
    """
    final_tvl: dict[str, int] = {}
    if onchain_tvl:
        final_tvl = {k: int(v) for k, v in onchain_tvl.items() if int(v) > 0}
    else:
        for entry in timeline:
            pa = entry["pool_address"]
            tvl = int(entry.get("tvl_in_token", "0") or "0")
            if tvl > 0:
                final_tvl[pa] = tvl

    if not final_tvl:
        return {
            "total_tvl": 0,
            "main_pool": "",
            "main_pool_tvl": 0,
            "main_pool_share": 0,
            "num_active_pools": 0,
            "source": "none",
        }

    total_tvl = sum(final_tvl.values())
    main_pool = max(final_tvl, key=final_tvl.get)
    main_pool_tvl = final_tvl[main_pool]
    main_pool_share = main_pool_tvl / total_tvl if total_tvl > 0 else 0

    return {
        "total_tvl": total_tvl,
        "main_pool": main_pool,
        "main_pool_tvl": main_pool_tvl,
        "main_pool_share": round(main_pool_share, 6),
        "num_active_pools": len(final_tvl),
        "per_pool_tvl": {
            k: v for k, v in sorted(final_tvl.items(), key=lambda x: -x[1])
        },
        "source": "onchain" if onchain_tvl else "timeline",
    }


def calculate_lp_concentration(
    positions: list[Position],
    top_n: int = 5,
) -> dict[str, Any]:
    """Calculate LP concentration: top LP and top-N shares."""
    if not positions:
        return {"top_lp_share": 0, "top_n_share": 0, "num_lps": 0}

    sorted_pos = sorted(positions, key=lambda p: p.share_pct, reverse=True)
    top_lp_share = sorted_pos[0].share_pct if sorted_pos else 0
    top_n_share = sum(p.share_pct for p in sorted_pos[:top_n])

    return {
        "top_lp_share": round(top_lp_share, 6),
        "top_n_share": round(top_n_share, 6),
        "top_{}_share".format(top_n): round(top_n_share, 6),
        "total_lp_positions": len(positions),
        "num_lps": len(set(p.owner for p in positions)),
    }


def calculate_withdrawal_severity(
    events_liquidity: list[dict],
    pre_event_tvl: int,
    incident_block: int,
) -> dict[str, Any]:
    """Calculate the severity of liquidity withdrawals before/during the crash window.

    If ``incident_block`` is 0, use all LIQUIDITY_REMOVE events in the indexed window.
    """
    removals = [
        e for e in events_liquidity
        if e.get("event_type") == "LIQUIDITY_REMOVE"
    ]
    if incident_block and incident_block > 0:
        pre_crash_removals = [
            e for e in removals
            if e.get("block_number", 0) <= incident_block
        ]
    else:
        pre_crash_removals = removals

    # Severity ratio only from removals tied to a known pool (PM events often
    # lack pool_address and use unrelated token amounts).
    amount_events = [
        e for e in pre_crash_removals
        if (e.get("pool_address") or "").strip()
    ]
    total_removed_tokens = sum(
        abs(int(e.get("token0_amount", "0") or "0"))
        + abs(int(e.get("token1_amount", "0") or "0"))
        for e in amount_events
    )

    severity = (
        total_removed_tokens / pre_event_tvl
        if pre_event_tvl > 0 and total_removed_tokens > 0
        else 0.0
    )
    # Cap for scoring stability
    severity = min(severity, 1.0)

    return {
        "num_withdrawals": len(pre_crash_removals),
        "total_removed_token0": total_removed_tokens,
        "pre_event_tvl": pre_event_tvl,
        "withdrawal_severity": round(severity, 6),
        "withdrawal_events": [
            {
                "block_number": e["block_number"],
                "block": e["block_number"],
                "ts": e.get("block_timestamp", 0),
                "pool": e.get("pool_address", ""),
                "pool_address": e.get("pool_address", ""),
                "amount0": e.get("token0_amount", "0"),
                "amount1": e.get("token1_amount", "0"),
                "token0_amount": e.get("token0_amount", "0"),
                "token1_amount": e.get("token1_amount", "0"),
                "actor": e.get("actor", ""),
            }
            for e in pre_crash_removals
        ],
    }


def calculate_all_metrics(
    verified_pools: list[VerifiedPool],
    events_all: list[dict],
    events_liquidity: list[dict],
    positions: list[Position],
    target_token: str,
    token_decimals: int,
    incident_block: int = 0,
    output_dir: str | Path = "output",
    w3: Optional[Web3] = None,
) -> dict[str, Any]:
    """Main entry point: compute all liquidity and risk metrics."""
    out = Path(output_dir)

    timeline = build_tvl_timeline(
        verified_pools, events_all, target_token, token_decimals
    )
    _write_json(out / "tvl_timeline.json", timeline)

    onchain_tvl = None
    if w3 is not None:
        try:
            onchain_tvl = snapshot_onchain_pool_tvl(w3, verified_pools, target_token)
        except Exception:
            onchain_tvl = None

    pool_conc = calculate_pool_concentration(
        verified_pools, timeline, onchain_tvl=onchain_tvl
    )
    lp_conc = calculate_lp_concentration(positions)

    pre_event_tvl = int(pool_conc.get("total_tvl", 0) or 0)
    withdrawal_sev = calculate_withdrawal_severity(
        events_liquidity, pre_event_tvl, incident_block
    )

    metrics = {
        "pool_concentration": pool_conc,
        "lp_concentration": lp_conc,
        "withdrawal_severity": withdrawal_sev,
        "tvl_timeline_length": len(timeline),
        "tvl_timeline": timeline,
    }
    _write_json(out / "metrics.json", metrics)

    return metrics


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

