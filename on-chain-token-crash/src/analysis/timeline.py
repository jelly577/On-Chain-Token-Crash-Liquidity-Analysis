"""Crash timeline construction — orders events, detects migration, checks alternative causes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from web3 import Web3

from ..models import NormalizedEvent, VerifiedPool


def build_incident_timeline(
    events_all: list[dict],
    events_swaps: list[dict],
    events_liquidity: list[dict],
    events_transfers: list[dict],
    target_token: str,
    verified_pools: list[VerifiedPool],
    incident_block: int = 0,
) -> dict[str, Any]:
    """Build a chronologically ordered incident timeline.

    Returns:
        {
            "events": [...sorted...],
            "pre_incident": [...],
            "post_incident": [...],
            "incident_window": {...},
            ...
        }
    """
    all_events_sorted = sorted(
        events_all,
        key=lambda e: (e["block_number"], e.get("log_index", 0)),
    )

    # Split into pre/post if we have an incident block
    pre_incident = [
        e for e in all_events_sorted
        if e["block_number"] < incident_block
    ] if incident_block else []

    post_incident = [
        e for e in all_events_sorted
        if e["block_number"] >= incident_block
    ] if incident_block else all_events_sorted

    # Find the first large sell event (crash indicator)
    large_sells = [
        e for e in all_events_sorted
        if e.get("event_type") == "SWAP" and
        abs(int(e.get("token0_amount", "0"))) > 10 ** 18
    ]

    # Find large liquidity removals
    large_removals = [
        e for e in all_events_sorted
        if e.get("event_type") == "LIQUIDITY_REMOVE" and
        abs(int(e.get("token0_amount", "0"))) > 10 ** 18
    ]

    # Detect potential liquidity migration
    migration = check_liquidity_migration(
        events_liquidity, verified_pools, target_token
    )

    # Detect alternative causes
    alt_causes = check_alternative_causes(
        events_transfers, verified_pools, target_token
    )

    # Build summary statistics
    total_swaps = len(events_swaps)
    total_liq_events = len(events_liquidity)
    total_transfers = len(events_transfers)

    first_event = all_events_sorted[0] if all_events_sorted else None
    last_event = all_events_sorted[-1] if all_events_sorted else None

    result = {
        "total_events": len(all_events_sorted),
        "total_swaps": total_swaps,
        "total_liquidity_events": total_liq_events,
        "total_token_transfers": total_transfers,
        "block_range": {
            "first_block": first_event["block_number"] if first_event else 0,
            "first_timestamp": first_event["block_timestamp"] if first_event else 0,
            "last_block": last_event["block_number"] if last_event else 0,
            "last_timestamp": last_event["block_timestamp"] if last_event else 0,
        },
        "incident_block": incident_block,
        "num_pre_incident_events": len(pre_incident),
        "num_post_incident_events": len(post_incident),
        "large_swaps": len(large_sells),
        "large_liquidity_removals": len(large_removals),
        "liquidity_migration": migration,
        "alternative_causes": alt_causes,
        "sorted_events": [e for e in all_events_sorted],
    }

    return result


def check_liquidity_migration(
    events_liquidity: list[dict],
    verified_pools: list[VerifiedPool],
    target_token: str,
) -> dict[str, Any]:
    """Detect if liquidity was migrated to another verified pool."""
    # Look for simultaneous removal from one pool and addition to another
    # by the same actor in the same transaction or adjacent blocks
    target = Web3.to_checksum_address(target_token)

    liquidity_adds = [
        e for e in events_liquidity
        if e.get("event_type") == "LIQUIDITY_ADD"
    ]
    liquidity_removes = [
        e for e in events_liquidity
        if e.get("event_type") == "LIQUIDITY_REMOVE"
    ]

    # Check for remove followed by add by same actor within 5 blocks
    migration_candidates = []
    for rm in liquidity_removes:
        rm_actor = rm.get("actor", "")
        rm_recipient = rm.get("recipient", "")
        rm_block = rm["block_number"]
        for add in liquidity_adds:
            add_actor = add.get("actor", "")
            add_recipient = add.get("recipient", "")
            add_block = add["block_number"]
            if (add_block - rm_block) <= 5 and add_block >= rm_block:
                if (add_actor and add_actor == rm_actor) or \
                   (add_recipient and add_recipient == rm_recipient):
                    migration_candidates.append({
                        "remove_block": rm_block,
                        "add_block": add_block,
                        "actor": rm_actor or add_actor,
                        "remove_pool": rm.get("pool_address", ""),
                        "add_pool": add.get("pool_address", ""),
                        "remove_amount0": rm.get("token0_amount", "0"),
                        "add_amount0": add.get("token0_amount", "0"),
                    })

    return {
        "migration_detected": len(migration_candidates) > 0,
        "migration_candidates": migration_candidates[:5],  # top 5
        "num_pools": len(verified_pools),
    }


def check_alternative_causes(
    events_transfers: list[dict],
    verified_pools: list[VerifiedPool],
    target_token: str,
) -> dict[str, Any]:
    """Check for alternative causes of a crash."""
    alt_causes = {
        "large_token_distributions": False,
        "minting_detected": False,
        "concentrated_dumping": False,
        "notes": [],
    }

    # Check for large token transfers to many addresses (airdrop/dumping)
    transfers_by_sender: dict[str, int] = {}
    target = Web3.to_checksum_address(target_token)

    for evt in events_transfers:
        actor = evt.get("actor", "")
        if actor:
            transfers_by_sender[actor] = transfers_by_sender.get(actor, 0) + 1

    # If any sender distributed tokens many times, flag it
    for sender, count in transfers_by_sender.items():
        if count > 10:
            alt_causes["large_token_distributions"] = True
            alt_causes["notes"].append(
                "Address {} sent tokens in {} separate transfers (potential distribution).".format(
                    sender, count
                )
            )

    return alt_causes


def build_crash_window(
    events_all: list[dict],
    incident_block: int,
    window_before: int = 100,
    window_after: int = 50,
) -> dict[str, Any]:
    """Extract events in a window around the incident block."""
    if incident_block == 0:
        return {"window_events": [], "window_start": 0, "window_end": 0}

    window_start = max(0, incident_block - window_before)
    window_end = incident_block + window_after

    window_events = [
        e for e in events_all
        if window_start <= e["block_number"] <= window_end
    ]

    return {
        "window_start": window_start,
        "window_end": window_end,
        "incident_block": incident_block,
        "total_window_events": len(window_events),
        "window_events": window_events,
    }


def analyze_timeline(
    events_all: list[dict],
    events_swaps: list[dict],
    events_liquidity: list[dict],
    events_transfers: list[dict],
    verified_pools: list[VerifiedPool],
    target_token: str,
    incident_block: int = 0,
    output_dir: str | Path = "output",
) -> dict[str, Any]:
    """Main entry point: build and analyze the crash timeline."""
    out = Path(output_dir)

    timeline = build_incident_timeline(
        events_all, events_swaps, events_liquidity, events_transfers,
        target_token, verified_pools, incident_block,
    )
    _write_json(out / "incident_timeline.json", timeline)

    crash_window = build_crash_window(events_all, incident_block)
    _write_json(out / "crash_window.json", crash_window)

    return timeline


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

