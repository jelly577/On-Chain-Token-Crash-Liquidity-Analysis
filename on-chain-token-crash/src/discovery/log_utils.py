"""Chunked event-log fetching with adaptive chunk sizing for Free tier limits."""
from __future__ import annotations

import os
from typing import Any, Optional

import requests as _requests
from web3 import Web3

DEFAULT_CHUNK_SIZE = 2_000
TOPIC_CHUNK_SIZE = 10  # Alchemy Free tier: 10 blocks with topics

PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"


def address_topic(address: str) -> str:
    addr = Web3.to_checksum_address(address).lower()
    return "0x" + "000000000000000000000000" + addr[2:]


def get_logs_chunked(event, from_block, to_block, argument_filters=None,
                      chunk_size=DEFAULT_CHUNK_SIZE):
    """Fetch logs with adaptive chunk sizing."""
    if from_block > to_block:
        return []

    logs = []
    start = from_block
    adaptive_size = chunk_size

    while start <= to_block:
        end = min(start + adaptive_size - 1, to_block)
        try:
            entries = event.get_logs(from_block=start, to_block=end)
            logs.extend(entries)
            start = end + 1
            adaptive_size = min(chunk_size, adaptive_size * 2)
        except Exception:
            if adaptive_size > 1:
                adaptive_size = max(1, adaptive_size // 2)
                # Don't advance start — retry with smaller chunk
            else:
                start = end + 1
                adaptive_size = chunk_size

    if argument_filters:
        filtered = []
        for entry in logs:
            match = True
            args = entry.get("args", {})
            for key, value in argument_filters.items():
                if key in args:
                    evt_val = args[key]
                    if isinstance(value, str) and value.startswith("0x"):
                        if Web3.to_checksum_address(str(evt_val)) != Web3.to_checksum_address(value):
                            match = False
                            break
                    elif evt_val != value:
                        match = False
                        break
            if match:
                filtered.append(entry)
        return filtered
    return logs


def get_logs_with_topics(w3, contract_address, topics, from_block, to_block,
                          chunk_size=TOPIC_CHUNK_SIZE):
    """Fetch logs using raw eth_getLogs with topic filters. Adaptive chunk sizing."""
    if from_block > to_block:
        return []

    provider = w3.provider
    rpc_url = provider.endpoint_uri if hasattr(provider, 'endpoint_uri') else os.environ.get("ETH_RPC_URL", "")
    if not rpc_url:
        return []

    addr = Web3.to_checksum_address(contract_address)
    logs = []
    start = from_block
    adaptive_size = chunk_size
    request_id = 0

    while start <= to_block:
        end = min(start + adaptive_size - 1, to_block)
        request_id += 1

        params_dict = {"address": addr, "fromBlock": hex(start), "toBlock": hex(end)}

        # Build topics: keep None for correct position matching
        has_non_none = any(t is not None for t in topics)
        if has_non_none:
            params_dict["topics"] = [t if t is not None else None for t in topics]

        try:
            payload = {"jsonrpc": "2.0", "method": "eth_getLogs", "params": [params_dict], "id": request_id}
            resp = _requests.post(rpc_url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data:
                    logs.extend(data["result"])
                    start = end + 1
                    adaptive_size = min(chunk_size * 4, adaptive_size * 2)
                else:
                    if adaptive_size > 1:
                        adaptive_size = max(1, adaptive_size // 2)
                    else:
                        start = end + 1
                        adaptive_size = chunk_size
            else:
                if adaptive_size > 1:
                    adaptive_size = max(1, adaptive_size // 2)
                else:
                    start = end + 1
                    adaptive_size = chunk_size
        except Exception:
            if adaptive_size > 1:
                adaptive_size = max(1, adaptive_size // 2)
            else:
                start = end + 1
                adaptive_size = chunk_size

    return logs


def dedupe_pools(pools):
    seen = set()
    result = []
    for pool in pools:
        addr = Web3.to_checksum_address(pool.pool_address)
        if addr in seen:
            continue
        seen.add(addr)
        result.append(pool)
    return result
