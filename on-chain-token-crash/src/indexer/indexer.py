"""Event indexer — indexes V2/V3 swaps, liquidity events, LP-token transfers, and token transfers.

Supports checkpoint/resume via a JSON checkpoint file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from web3 import Web3
from web3.types import EventData

from ..client import get_contract
from ..discovery.log_utils import get_logs_chunked
from ..models import NormalizedEvent, VerifiedPool


def _fetch_block_timestamps(
    w3: Web3, block_numbers: set[int]
) -> dict[int, int]:
    cache = {}
    for bn in sorted(block_numbers):
        if bn in cache:
            continue
        try:
            block = w3.eth.get_block(bn)
            cache[bn] = block.get("timestamp", 0)
        except Exception:
            cache[bn] = 0
    return cache


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    if checkpoint_path.exists():
        with open(checkpoint_path) as f:
            return json.load(f)
    return {}


def _save_checkpoint(checkpoint_path: Path, state: dict[str, Any]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with open(checkpoint_path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _normalize_v2_event(
    evt: EventData,
    pool: VerifiedPool,
    block_timestamps: dict[int, int],
) -> NormalizedEvent:
    args = evt["args"]
    bn = evt["blockNumber"]
    evt_name = evt.get("event", "")
    base = {
        "block_number": bn,
        "block_timestamp": block_timestamps.get(bn, 0),
        "transaction_hash": evt["transactionHash"].hex(),
        "log_index": evt.get("logIndex", 0),
        "protocol": "uniswap",
        "version": "v2",
        "pool_address": pool.pool_address,
        "verified": True,
    }
    if evt_name == "Swap":
        return NormalizedEvent(
            **base,
            event_type="SWAP",
            actor=Web3.to_checksum_address(args["sender"]),
            recipient=Web3.to_checksum_address(args["to"]),
            token0_amount=str(args["amount0In"]) if int(args["amount0In"]) > 0 else "-{}".format(args['amount0Out']),
            token1_amount=str(args["amount1In"]) if int(args["amount1In"]) > 0 else "-{}".format(args['amount1Out']),
            source_event="Swap",
        )
    elif evt_name == "Mint":
        return NormalizedEvent(
            **base,
            event_type="LIQUIDITY_ADD",
            actor=Web3.to_checksum_address(args["sender"]),
            recipient=Web3.to_checksum_address(args["sender"]),
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            source_event="Mint",
        )
    elif evt_name == "Burn":
        return NormalizedEvent(
            **base,
            event_type="LIQUIDITY_REMOVE",
            actor=Web3.to_checksum_address(args["sender"]),
            recipient=Web3.to_checksum_address(args["to"]),
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            source_event="Burn",
        )
    return None


def _normalize_v3_pool_event(
    evt: EventData,
    pool: VerifiedPool,
    block_timestamps: dict[int, int],
) -> NormalizedEvent:
    args = evt["args"]
    bn = evt["blockNumber"]
    evt_name = evt.get("event", "")
    base = {
        "block_number": bn,
        "block_timestamp": block_timestamps.get(bn, 0),
        "transaction_hash": evt["transactionHash"].hex(),
        "log_index": evt.get("logIndex", 0),
        "protocol": "uniswap",
        "version": "v3",
        "pool_address": pool.pool_address,
        "verified": True,
    }
    if evt_name == "Swap":
        amount0 = int(args["amount0"])
        amount1 = int(args["amount1"])
        return NormalizedEvent(
            **base,
            event_type="SWAP",
            actor=Web3.to_checksum_address(args["sender"]),
            recipient=Web3.to_checksum_address(args["recipient"]),
            token0_amount=str(abs(amount0)),
            token1_amount=str(abs(amount1)),
            source_event="Swap",
        )
    elif evt_name == "Mint":
        return NormalizedEvent(
            **base,
            event_type="LIQUIDITY_ADD",
            actor=Web3.to_checksum_address(args["sender"]),
            recipient=Web3.to_checksum_address(args["owner"]),
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            liquidity_delta=str(args["amount"]),
            source_event="Mint",
        )
    elif evt_name == "Burn":
        return NormalizedEvent(
            **base,
            event_type="LIQUIDITY_REMOVE",
            actor=Web3.to_checksum_address(args["sender"]),
            recipient=Web3.to_checksum_address(args["owner"]),
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            liquidity_delta="-{}".format(args['amount']),
            source_event="Burn",
        )
    elif evt_name == "Collect":
        return NormalizedEvent(
            **base,
            event_type="COLLECT_FEES",
            actor=Web3.to_checksum_address(args["owner"]),
            recipient=Web3.to_checksum_address(args["recipient"]),
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            source_event="Collect",
        )
    return None


def _normalize_v3_position_event(
    evt: EventData,
    pool_map: dict,
    block_timestamps: dict[int, int],
) -> Optional[NormalizedEvent]:
    args = evt["args"]
    bn = evt["blockNumber"]
    evt_name = evt.get("event", "")
    token_id = int(args.get("tokenId", 0))
    base = {
        "block_number": bn,
        "block_timestamp": block_timestamps.get(bn, 0),
        "transaction_hash": evt["transactionHash"].hex(),
        "log_index": evt.get("logIndex", 0),
        "protocol": "uniswap",
        "version": "v3",
        "verified": True,
    }
    pm_pool = pool_map.get(token_id)
    pool_addr = pm_pool.pool_address if isinstance(pm_pool, VerifiedPool) else ""
    if evt_name == "Transfer":
        return NormalizedEvent(
            **base,
            event_type="POSITION_TRANSFER",
            actor=Web3.to_checksum_address(args["from"]),
            recipient=Web3.to_checksum_address(args["to"]),
            pool_address=pool_addr,
            source_event="Transfer",
        )
    if evt_name == "IncreaseLiquidity":
        return NormalizedEvent(
            **base,
            event_type="LIQUIDITY_ADD",
            pool_address=pool_addr,
            actor="",
            recipient="",
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            liquidity_delta=str(args["liquidity"]),
            source_event="IncreaseLiquidity",
        )
    if evt_name == "DecreaseLiquidity":
        return NormalizedEvent(
            **base,
            event_type="LIQUIDITY_REMOVE",
            pool_address=pool_addr,
            actor="",
            recipient="",
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            liquidity_delta="-{}".format(args['liquidity']),
            source_event="DecreaseLiquidity",
        )
    if evt_name == "Collect":
        return NormalizedEvent(
            **base,
            event_type="COLLECT_FEES",
            pool_address=pool_addr,
            actor="",
            recipient=Web3.to_checksum_address(args["recipient"]),
            token0_amount=str(args["amount0"]),
            token1_amount=str(args["amount1"]),
            source_event="Collect",
        )
    return None


def index_v2_pool_events(
    w3: Web3,
    pool: VerifiedPool,
    from_block: int,
    to_block: int,
    checkpoint: dict,
) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    pool_key = "v2_{}".format(pool.pool_address.lower())
    last_indexed = checkpoint.get(pool_key, 0)
    start = max(from_block, last_indexed + 1)
    if start > to_block:
        return events
    contract = get_contract(w3, pool.pool_address, "uniswap_v2_pair")
    block_nums: set[int] = set()
    raw_events: list[EventData] = []
    for evt_name in ("Swap", "Mint", "Burn"):
        try:
            event_obj = getattr(contract.events, evt_name)
            entries = get_logs_chunked(event_obj, start, to_block)
            raw_events.extend(entries)
            block_nums.update(e["blockNumber"] for e in entries)
        except Exception:
            pass
    timestamps = _fetch_block_timestamps(w3, block_nums)
    for evt in raw_events:
        ne = _normalize_v2_event(evt, pool, timestamps)
        if ne:
            events.append(ne)
    checkpoint[pool_key] = to_block
    return events


def index_v3_pool_events(
    w3: Web3,
    pool: VerifiedPool,
    from_block: int,
    to_block: int,
    checkpoint: dict,
) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    pool_key = "v3_{}".format(pool.pool_address.lower())
    last_indexed = checkpoint.get(pool_key, 0)
    start = max(from_block, last_indexed + 1)
    if start > to_block:
        return events
    contract = get_contract(w3, pool.pool_address, "uniswap_v3_pool")
    block_nums: set[int] = set()
    raw_events: list[EventData] = []
    for evt_name in ("Swap", "Mint", "Burn", "Collect"):
        try:
            event_obj = getattr(contract.events, evt_name)
            entries = get_logs_chunked(event_obj, start, to_block)
            raw_events.extend(entries)
            block_nums.update(e["blockNumber"] for e in entries)
        except Exception:
            pass
    timestamps = _fetch_block_timestamps(w3, block_nums)
    for evt in raw_events:
        ne = _normalize_v3_pool_event(evt, pool, timestamps)
        if ne:
            events.append(ne)
    checkpoint[pool_key] = to_block
    return events


def index_v3_position_events(
    w3: Web3,
    position_manager_address: str,
    verified_pools: list[VerifiedPool],
    from_block: int,
    to_block: int,
    checkpoint: dict,
) -> tuple[list[NormalizedEvent], dict]:
    events: list[NormalizedEvent] = []
    pm_key = "v3_pm_{}".format(position_manager_address.lower())
    last_indexed = checkpoint.get(pm_key, 0)
    start = max(from_block, last_indexed + 1)
    token_pool_map: dict = {}
    if start > to_block:
        return events, token_pool_map
    pm_contract = get_contract(w3, position_manager_address, "uniswap_v3_position_manager")
    block_nums: set[int] = set()
    raw_events: list[EventData] = []
    for evt_name in ("Transfer", "IncreaseLiquidity", "DecreaseLiquidity", "Collect"):
        try:
            event_obj = getattr(pm_contract.events, evt_name)
            entries = get_logs_chunked(event_obj, start, to_block)
            raw_events.extend(entries)
            block_nums.update(e["blockNumber"] for e in entries)
        except Exception:
            pass
    v3_pools_by_tokens: dict = {}
    for p in verified_pools:
        if p.version == "v3" and p.verified:
            v3_pools_by_tokens[
                (Web3.to_checksum_address(p.token0),
                 Web3.to_checksum_address(p.token1),
                 p.fee)
            ] = p
    for evt in raw_events:
        if evt.get("event") == "IncreaseLiquidity":
            token_id = int(evt["args"]["tokenId"])
            try:
                pos = pm_contract.functions.positions(token_id).call()
                t0 = Web3.to_checksum_address(pos[2])
                t1 = Web3.to_checksum_address(pos[3])
                fee = pos[4]
                match_pool = v3_pools_by_tokens.get((t0, t1, fee))
                if match_pool:
                    token_pool_map[token_id] = match_pool
            except Exception:
                pass
    timestamps = _fetch_block_timestamps(w3, block_nums)
    for evt in raw_events:
        ne = _normalize_v3_position_event(evt, token_pool_map, timestamps)
        if ne:
            events.append(ne)
    checkpoint[pm_key] = to_block
    return events, token_pool_map


def index_token_transfers(
    w3: Web3,
    token_address: str,
    from_block: int,
    to_block: int,
    checkpoint: dict,
) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    token_key = "token_transfers_{}".format(token_address.lower())
    last_indexed = checkpoint.get(token_key, 0)
    start = max(from_block, last_indexed + 1)
    if start > to_block:
        return events
    token = Web3.to_checksum_address(token_address)
    try:
        contract = get_contract(w3, token, "erc20")
        entries = get_logs_chunked(contract.events.Transfer, start, to_block)
        timestamps = _fetch_block_timestamps(w3, {e["blockNumber"] for e in entries})
        for evt in entries:
            bn = evt["blockNumber"]
            args = evt["args"]
            events.append(NormalizedEvent(
                block_number=bn,
                block_timestamp=timestamps.get(bn, 0),
                transaction_hash=evt["transactionHash"].hex(),
                log_index=evt.get("logIndex", 0),
                protocol="",
                version="",
                pool_address="",
                event_type="TOKEN_TRANSFER",
                actor=Web3.to_checksum_address(args["from"]),
                recipient=Web3.to_checksum_address(args["to"]),
                token0_amount=str(args["value"]),
                source_event="Transfer",
                verified=True,
            ))
    except Exception:
        pass
    checkpoint[token_key] = to_block
    return events


def index_events(
    w3: Web3,
    verified_pools: list[VerifiedPool],
    target_token: str,
    from_block: int,
    to_block: int,
    output_dir: str | Path = "output",
    checkpoint_file: str = "event_indexer_checkpoint.json",
    index_token_transfer: bool = True,
) -> dict[str, list]:
    out = Path(output_dir)
    cp_path = out / checkpoint_file
    checkpoint = _load_checkpoint(cp_path)
    swaps: list[dict] = []
    liquidity: list[dict] = []
    transfers: list[dict] = []
    all_events: list[NormalizedEvent] = []
    v3_pools = [p for p in verified_pools if p.version == "v3" and p.verified]
    v2_pools = [p for p in verified_pools if p.version == "v2" and p.verified]
    for pool in v2_pools:
        evts = index_v2_pool_events(w3, pool, from_block, to_block, checkpoint)
        all_events.extend(evts)
        for e in evts:
            if e.event_type == "SWAP":
                swaps.append(e.__dict__)
            else:
                liquidity.append(e.__dict__)
    v3_pm_events: list[NormalizedEvent] = []
    v3_token_pool_map: dict = {}
    for pool in v3_pools:
        evts = index_v3_pool_events(w3, pool, from_block, to_block, checkpoint)
        all_events.extend(evts)
        for e in evts:
            if e.event_type == "SWAP":
                swaps.append(e.__dict__)
            else:
                liquidity.append(e.__dict__)
    pm_addresses = set()
    for pool in v3_pools:
        if pool.position_manager_address:
            pm_addresses.add(pool.position_manager_address)
    for pm_addr in pm_addresses:
        pm_evts, token_map = index_v3_position_events(
            w3, pm_addr, verified_pools, from_block, to_block, checkpoint
        )
        v3_pm_events.extend(pm_evts)
        v3_token_pool_map.update(token_map)
        for e in pm_evts:
            if e.event_type in ("LIQUIDITY_REMOVE", "LIQUIDITY_ADD"):
                liquidity.append(e.__dict__)
    if index_token_transfer:
        token_evts = index_token_transfers(
            w3, target_token, from_block, to_block, checkpoint
        )
        all_events.extend(token_evts)
        transfers = [e.__dict__ for e in token_evts]
    _save_checkpoint(cp_path, checkpoint)
    _write_json(out / "swaps.json", swaps)
    _write_json(out / "liquidity_events.json", liquidity)
    _write_json(out / "events_all.json", [e.__dict__ for e in all_events])
    return {
        "swaps": swaps,
        "liquidity_events": liquidity,
        "transfers": transfers,
    }


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
