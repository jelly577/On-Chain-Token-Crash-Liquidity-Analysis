"""Discovery engine — orchestrates all protocol adapters."""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from ..registry.loader import (
    get_chain_id,
    get_enabled_protocols,
    get_quote_assets,
    load_registry,
)
from .log_utils import dedupe_pools
from .base import PoolDiscoveryAdapter
from .uniswap_v2 import UniswapV2Adapter
from .uniswap_v3 import UniswapV3Adapter
from .uniswap_v4 import UniswapV4Adapter


_ADAPTER_MAP: dict[str, type[PoolDiscoveryAdapter]] = {
    "UniswapV2Adapter": UniswapV2Adapter,
    "UniswapV3Adapter": UniswapV3Adapter,
    "UniswapV4Adapter": UniswapV4Adapter,
}


def discover_pools(
    w3: Web3,
    token_address: str,
    from_block: int,
    to_block: int,
    chain_id: int = 1,
) -> dict[str, list]:
    """Discover all pools containing *token_address* across all supported protocols.

    Returns {"pools": [...], "protocols_used": [...], "errors": [...]}.
    """
    registry = load_registry()
    chain_id = get_chain_id(registry)
    deployments = get_enabled_protocols(registry)
    quote_assets = get_quote_assets(registry)

    all_pools: list = []
    protocol_names: set[str] = set()
    errors: list[str] = []

    for dep in deployments:
        adapter_cls = _ADAPTER_MAP.get(dep.adapter)
        if adapter_cls is None:
            errors.append(f"No adapter registered for {dep.adapter}")
            continue

        adapter = adapter_cls(w3, dep)
        try:
            pools = adapter.discover(
                token_address, from_block, to_block, quote_assets
            )
            all_pools.extend(pools)
            protocol_names.add(f"{dep.protocol}_{dep.version}")
        except Exception as e:
            errors.append(f"{dep.protocol}_{dep.version}: {e}")

    all_pools = dedupe_pools(all_pools)

    return {
        "pools": [p.__dict__ for p in all_pools],
        "protocols_used": list(protocol_names),
        "errors": errors,
    }
