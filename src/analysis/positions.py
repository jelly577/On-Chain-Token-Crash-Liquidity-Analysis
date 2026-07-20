"""Position analysis — reconstruct V2 LP-token holders and V3 position NFT owners."""
from __future__ import annotations

import json
from pathlib import Path
from collections import defaultdict
from typing import Any, Optional

from web3 import Web3

from ..client import get_contract
from ..discovery.log_utils import get_logs_chunked
from ..models import NormalizedEvent, Position, VerifiedPool


def reconstruct_v2_holders(
    w3: Web3,
    pools: list[VerifiedPool],
    events_by_pool: dict[str, list[dict]],
    from_block: int,
    to_block: int,
) -> list[Position]:
    """Reconstruct V2 LP-token holders from Transfer events on each Pair."""
    positions: list[Position] = []

    for pool in pools:
        if pool.version != "v2" or not pool.verified:
            continue
        pair_addr = pool.pool_address
        # The Pair contract IS the LP ERC-20 token for V2
        # Read totalSupply at the end of the range
        try:
            pair_contract = get_contract(w3, pair_addr, "uniswap_v2_pair")
            total_supply = pair_contract.functions.totalSupply().call()
        except Exception:
            total_supply = 0

        if total_supply == 0:
            continue

        # Index LP Transfer events
        transfers: dict[str, dict] = {}
        for evt in events_by_pool.get(pair_addr.lower(), []):
            if evt.get("source_event") == "Transfer" and evt.get("event_type") == "TOKEN_TRANSFER":
                from_addr = evt.get("actor", "")
                to_addr = evt.get("recipient", "")
                value = int(evt.get("token0_amount", "0"))
                if from_addr in transfers:
                    transfers[from_addr]["balance"] -= value
                if to_addr in transfers:
                    transfers[to_addr]["balance"] += value
                if from_addr not in transfers:
                    transfers[from_addr] = {"address": from_addr, "balance": -value, "last_block": evt["block_number"]}
                if to_addr not in transfers:
                    transfers[to_addr] = {"address": to_addr, "balance": value, "last_block": evt["block_number"]}

        # Also try fetching LP Transfer events directly from the Pair
        try:
            lp_contract = get_contract(w3, pair_addr, "uniswap_v2_pair")
            transfer_events = get_logs_chunked(
                lp_contract.events.Transfer, from_block, to_block
            )
            for evt in transfer_events:
                args = evt["args"]
                from_addr = Web3.to_checksum_address(args["from"])
                to_addr = Web3.to_checksum_address(args["to"])
                value = args["value"]
                if from_addr not in transfers:
                    transfers[from_addr] = {"address": from_addr, "balance": 0, "last_block": evt["blockNumber"]}
                if to_addr not in transfers:
                    transfers[to_addr] = {"address": to_addr, "balance": 0, "last_block": evt["blockNumber"]}
                transfers[from_addr]["balance"] -= value
                transfers[to_addr]["balance"] += value
        except Exception:
            pass

        # Filter to holders with positive balance, convert to Positions
        for addr, info in transfers.items():
            bal = info.get("balance", 0)
            if bal > 0:
                share = bal / total_supply
                positions.append(Position(
                    pool_address=pair_addr,
                    owner=addr,
                    lp_token_address=pair_addr,
                    liquidity=str(bal),
                    share_pct=round(share * 100, 6),
                    resolution_method="v2_transfer_reconstruction",
                    confidence=0.9,
                ))

        # Fallback: window may miss LP Transfers — snapshot balanceOf for known actors
        if not any(p.pool_address == pair_addr for p in positions):
            candidates: set[str] = set()
            for evt in events_by_pool.get(pair_addr.lower(), []):
                for key in ("actor", "recipient"):
                    a = evt.get(key) or ""
                    if a and a.lower() not in (
                        "0x0000000000000000000000000000000000000000",
                        pair_addr.lower(),
                    ):
                        candidates.add(Web3.to_checksum_address(a))
            try:
                pair_contract = get_contract(w3, pair_addr, "uniswap_v2_pair")
                for addr in candidates:
                    try:
                        bal = int(pair_contract.functions.balanceOf(addr).call())
                    except Exception:
                        continue
                    if bal <= 0:
                        continue
                    share = bal / total_supply
                    positions.append(Position(
                        pool_address=pair_addr,
                        owner=addr,
                        lp_token_address=pair_addr,
                        liquidity=str(bal),
                        share_pct=round(share * 100, 6),
                        resolution_method="v2_balanceof_snapshot",
                        confidence=0.75,
                    ))
            except Exception:
                pass

    return positions


def reconstruct_v3_position_owners(
    w3: Web3,
    pools: list[VerifiedPool],
    position_manager_address: str,
    from_block: int,
    to_block: int,
) -> list[Position]:
    """Map V3 position NFTs to their current owners via the PositionManager."""
    positions: list[Position] = []
    pm_addr = Web3.to_checksum_address(position_manager_address)

    try:
        pm_contract = get_contract(w3, pm_addr, "uniswap_v3_position_manager")
    except Exception:
        return positions

    # Index ERC-721 Transfer events to find all tokenIds and their final owners
    nft_owners: dict[int, str] = {}
    try:
        transfer_events = get_logs_chunked(
            pm_contract.events.Transfer, from_block, to_block
        )
        for evt in transfer_events:
            args = evt["args"]
            token_id = args["tokenId"]
            to_addr = Web3.to_checksum_address(args["to"])
            nft_owners[token_id] = to_addr
    except Exception:
        pass

    # Build (token0, token1, fee) -> pool map
    pool_map: dict[tuple[str, str, int], VerifiedPool] = {}
    for p in pools:
        if p.version == "v3" and p.verified:
            pool_map[(Web3.to_checksum_address(p.token0),
                      Web3.to_checksum_address(p.token1),
                      p.fee)] = p

    # For each NFT we found, get position details and match to pool
    for token_id, owner_addr in nft_owners.items():
        try:
            pos = pm_contract.functions.positions(token_id).call()
            # pos = (nonce, operator, token0, token1, fee, tickLower, tickUpper, liquidity, ...)
            t0 = Web3.to_checksum_address(pos[2])
            t1 = Web3.to_checksum_address(pos[3])
            fee = pos[4]
            liquidity = pos[7]
            matched_pool = pool_map.get((t0, t1, fee))

            if matched_pool and liquidity > 0:
                # Get total liquidity in pool for share calculation
                try:
                    pool_contract = get_contract(w3, matched_pool.pool_address, "uniswap_v3_pool")
                    pool_liquidity = pool_contract.functions.liquidity().call()
                    share = (liquidity / pool_liquidity * 100) if pool_liquidity > 0 else 0.0
                except Exception:
                    share = 0.0

                positions.append(Position(
                    pool_address=matched_pool.pool_address,
                    owner=owner_addr,
                    nft_token_id=token_id,
                    liquidity=str(liquidity),
                    share_pct=round(share, 6),
                    resolution_method="v3_nft_owner_of",
                    confidence=0.95,
                ))
        except Exception:
            pass

    return positions


def analyze_positions(
    w3: Web3,
    verified_pools: list[VerifiedPool],
    events_all: list[dict],
    target_token: str,
    from_block: int,
    to_block: int,
    output_dir: str | Path = "output",
) -> tuple[list[Position], dict[str, Any]]:
    """Main entry point: reconstruct all positions and compute summary stats."""
    out = Path(output_dir)
    positions: list[Position] = []

    # Group events by pool address
    events_by_pool: dict[str, list[dict]] = defaultdict(list)
    for evt in events_all:
        pa = evt.get("pool_address", "").lower()
        if pa:
            events_by_pool[pa].append(evt)

    # V2 holders
    v2_positions = reconstruct_v2_holders(
        w3, verified_pools, events_by_pool, from_block, to_block
    )
    positions.extend(v2_positions)

    # V3 position owners
    pm_addresses = set()
    for p in verified_pools:
        if p.version == "v3" and p.position_manager_address:
            pm_addresses.add(p.position_manager_address)

    for pm_addr in pm_addresses:
        v3_positions = reconstruct_v3_position_owners(
            w3, verified_pools, pm_addr, from_block, to_block
        )
        positions.extend(v3_positions)

    # Write output
    pos_dicts = [p.__dict__ for p in positions]
    _write_json(out / "positions.json", pos_dicts)

    # Summary stats
    total_lp_holders = len(set(p.owner for p in positions))
    top_holders = sorted(positions, key=lambda x: x.share_pct, reverse=True)[:5]
    summary = {
        "total_positions": len(positions),
        "total_unique_holders": total_lp_holders,
        "top_5_holders": [
            {"owner": h.owner, "share_pct": h.share_pct, "pool": h.pool_address}
            for h in top_holders
        ],
    }
    _write_json(out / "position_summary.json", summary)

    return positions, summary


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

