"""Uniswap V3 pool discovery."""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from ..client import get_contract
from ..models import ProtocolDeployment, VerifiedPool
from ..registry.loader import get_chain_id, get_v3_fees, load_registry
from .base import PoolDiscoveryAdapter
from .log_utils import (
    POOL_CREATED_TOPIC,
    address_topic,
    dedupe_pools,
    get_logs_with_topics,
)
import time as _time


class UniswapV3Adapter(PoolDiscoveryAdapter):
    """Discover V3 pools via getPool (fast) and PoolCreated events (exhaustive)."""

    def discover(
        self,
        token_address: str,
        from_block: int,
        to_block: int,
        quote_assets: Optional[list[dict]] = None,
    ) -> list[VerifiedPool]:
        token = Web3.to_checksum_address(token_address)
        registry = load_registry()
        chain_id = get_chain_id(registry)
        fee_tiers = get_v3_fees(registry) or [500, 3000, 10000]
        seen: set[str] = set()
        pools: list[VerifiedPool] = []

        factory = get_contract(self.w3, self.deployment.factory, "uniswap_v3_factory")

        # --- Fast discovery via getPool ---
        if quote_assets:
            for qa in quote_assets:
                q_addr = Web3.to_checksum_address(qa["address"])
                for fee in fee_tiers:
                    for t0, t1 in ((token, q_addr), (q_addr, token)):
                        pool_addr = factory.functions.getPool(t0, t1, fee).call()
                        if not pool_addr or pool_addr == "0x0000000000000000000000000000000000000000":
                            continue
                        pool_addr_checksum = Web3.to_checksum_address(pool_addr)
                        if pool_addr_checksum in seen:
                            continue
                        seen.add(pool_addr_checksum)
                        pools.append(self._candidate_from_address(
                            pool_addr_checksum, fee, chain_id
                        ))

        # --- Exhaustive discovery via PoolCreated events (skip for large ranges) ---
        factory_addr = self.deployment.factory
        search_from = max(from_block, self.deployment.deployment_block)
        total_blocks = to_block - search_from + 1

        if total_blocks <= 1000:
            _try_exhaustive_v3(
                self.w3, factory_addr, token, chain_id,
                search_from, to_block, seen, pools,
                self.deployment,
            )

        return dedupe_pools(pools)

    def _candidate_from_address(
        self, pool_addr: str, fee: int, chain_id: int
    ) -> VerifiedPool:
        try:
            pool = get_contract(self.w3, pool_addr, "uniswap_v3_pool")
            token0 = Web3.to_checksum_address(pool.functions.token0().call())
            token1 = Web3.to_checksum_address(pool.functions.token1().call())
            return VerifiedPool(
                chain_id=chain_id, protocol="uniswap", version="v3",
                architecture="concentrated_pool",
                factory_address=self.deployment.factory,
                router_addresses=(
                    [self.deployment.router] if self.deployment.router else []
                ),
                pool_address=pool_addr, custody_address=pool_addr,
                position_manager_address=self.deployment.position_manager,
                token0=token0, token1=token1, fee=fee,
                verified=False, verification_confidence=0.0,
            )
        except Exception:
            return VerifiedPool(
                chain_id=chain_id, protocol="uniswap", version="v3",
                architecture="concentrated_pool",
                factory_address=self.deployment.factory,
                router_addresses=(
                    [self.deployment.router] if self.deployment.router else []
                ),
                pool_address=pool_addr, custody_address=pool_addr,
                position_manager_address=self.deployment.position_manager,
                fee=fee, verified=False, verification_confidence=0.0,
            )


def _try_exhaustive_v3(
    w3: Web3, factory_addr: str, token: str, chain_id: int,
    search_from: int, to_block: int, seen: set, pools: list,
    deployment: ProtocolDeployment,
) -> None:
    """Try exhaustive V3 discovery via PoolCreated events (graceful failure)."""
    try:
        factory_addr_checksum = Web3.to_checksum_address(factory_addr)

        # token0 == target
        raw_logs = get_logs_with_topics(
            w3, factory_addr_checksum,
            [POOL_CREATED_TOPIC, address_topic(token), None, None],
            search_from, to_block,
        )
        for raw in raw_logs:
            pool_addr = Web3.to_checksum_address("0x" + raw["data"][2+64:2+128].zfill(64)[-40:])
            if pool_addr in seen:
                continue
            seen.add(pool_addr)
            token0 = Web3.to_checksum_address("0x" + raw["topics"][1].hex()[-40:])
            token1 = Web3.to_checksum_address("0x" + raw["topics"][2].hex()[-40:])
            fee = int(raw["topics"][3].hex(), 16) if len(raw["topics"]) > 3 and raw["topics"][3] else 0
            pools.append(VerifiedPool(
                chain_id=chain_id, protocol="uniswap", version="v3",
                architecture="concentrated_pool",
                factory_address=deployment.factory,
                router_addresses=[deployment.router] if deployment.router else [],
                pool_address=pool_addr, custody_address=pool_addr,
                position_manager_address=deployment.position_manager,
                token0=token0, token1=token1, fee=fee,
                creation_block=int(raw["blockNumber"], 16) if isinstance(raw["blockNumber"], str) else raw["blockNumber"],
                creation_transaction=raw["transactionHash"].hex(),
                verified=False, verification_confidence=0.0,
            ))

        # token1 == target
        raw_logs = get_logs_with_topics(
            w3, factory_addr_checksum,
            [POOL_CREATED_TOPIC, None, address_topic(token), None],
            search_from, to_block,
        )
        for raw in raw_logs:
            pool_addr = Web3.to_checksum_address("0x" + raw["data"][2+64:2+128].zfill(64)[-40:])
            if pool_addr in seen:
                continue
            seen.add(pool_addr)
            token0 = Web3.to_checksum_address("0x" + raw["topics"][1].hex()[-40:])
            token1 = Web3.to_checksum_address("0x" + raw["topics"][2].hex()[-40:])
            fee = int(raw["topics"][3].hex(), 16) if len(raw["topics"]) > 3 and raw["topics"][3] else 0
            pools.append(VerifiedPool(
                chain_id=chain_id, protocol="uniswap", version="v3",
                architecture="concentrated_pool",
                factory_address=deployment.factory,
                router_addresses=[deployment.router] if deployment.router else [],
                pool_address=pool_addr, custody_address=pool_addr,
                position_manager_address=deployment.position_manager,
                token0=token0, token1=token1, fee=fee,
                creation_block=int(raw["blockNumber"], 16) if isinstance(raw["blockNumber"], str) else raw["blockNumber"],
                creation_transaction=raw["transactionHash"].hex(),
                verified=False, verification_confidence=0.0,
            ))

    except Exception:
        pass
