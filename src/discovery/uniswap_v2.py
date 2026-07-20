"""Uniswap V2 pool discovery."""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from ..client import get_contract
from ..models import ProtocolDeployment, VerifiedPool
from ..registry.loader import get_chain_id, load_registry
from .base import PoolDiscoveryAdapter
from .log_utils import (
    PAIR_CREATED_TOPIC,
    address_topic,
    dedupe_pools,
    get_logs_with_topics,
)
import time as _time


class UniswapV2Adapter(PoolDiscoveryAdapter):
    """Discover V2 pairs via getPair (fast) and PairCreated events (exhaustive)."""

    def discover(
        self,
        token_address: str,
        from_block: int,
        to_block: int,
        quote_assets: Optional[list[dict]] = None,
    ) -> list[VerifiedPool]:
        token = Web3.to_checksum_address(token_address)
        chain_id = get_chain_id(load_registry())
        seen: set[str] = set()
        pools: list[VerifiedPool] = []

        factory = get_contract(self.w3, self.deployment.factory, "uniswap_v2_factory")

        # --- Fast discovery via getPair with quote assets ---
        if quote_assets:
            for qa in quote_assets:
                q_addr = Web3.to_checksum_address(qa["address"])
                for t0, t1 in ((token, q_addr), (q_addr, token)):
                    pair_addr = factory.functions.getPair(t0, t1).call()
                    if not pair_addr or pair_addr == "0x0000000000000000000000000000000000000000":
                        continue
                    pair_addr_checksum = Web3.to_checksum_address(pair_addr)
                    if pair_addr_checksum in seen:
                        continue
                    seen.add(pair_addr_checksum)
                    pool = self._candidate_from_address(pair_addr_checksum, chain_id)
                    if pool:
                        pools.append(pool)

        # --- Exhaustive discovery via PairCreated events (skip if range too large for Free tier) ---
        factory_addr = self.deployment.factory
        search_from = max(from_block, self.deployment.deployment_block)
        total_blocks = to_block - search_from + 1

        # Skip exhaustive if range > 1000 blocks (would be too slow with Free tier limits)
        if total_blocks <= 1000:
            _try_exhaustive_v2(self.w3, factory_addr, token, chain_id,
                                search_from, to_block, seen, pools,
                                self.deployment.factory, self.deployment.router)

        return dedupe_pools(pools)

    def _candidate_from_address(
        self, pair_addr: str, chain_id: int
    ) -> Optional[VerifiedPool]:
        try:
            pair = get_contract(self.w3, pair_addr, "uniswap_v2_pair")
            token0 = Web3.to_checksum_address(pair.functions.token0().call())
            token1 = Web3.to_checksum_address(pair.functions.token1().call())
            return VerifiedPool(
                chain_id=chain_id,
                protocol="uniswap",
                version="v2",
                architecture="direct_pair",
                factory_address=self.deployment.factory,
                router_addresses=[self.deployment.router] if self.deployment.router else [],
                pool_address=pair_addr,
                custody_address=pair_addr,
                token0=token0,
                token1=token1,
                verified=False,
                verification_confidence=0.0,
            )
        except Exception:
            return None


def _try_exhaustive_v2(
    w3: Web3, factory_addr: str, token: str, chain_id: int,
    search_from: int, to_block: int, seen: set, pools: list,
    deployment_factory: str, deployment_router: Optional[str],
) -> None:
    """Try exhaustive V2 discovery via PairCreated events (graceful failure)."""
    try:
        factory_addr_checksum = Web3.to_checksum_address(factory_addr)
        t0 = _time.time()

        # token0 == target
        raw_logs = get_logs_with_topics(
            w3, factory_addr_checksum,
            [PAIR_CREATED_TOPIC, address_topic(token), None],
            search_from, to_block,
        )
        for raw in raw_logs:
            pair_addr = Web3.to_checksum_address("0x" + raw["data"][2:66].zfill(64)[-40:])
            if pair_addr in seen:
                continue
            seen.add(pair_addr)
            token0 = Web3.to_checksum_address("0x" + raw["topics"][1].hex()[-40:])
            token1 = Web3.to_checksum_address("0x" + raw["topics"][2].hex()[-40:])
            pools.append(VerifiedPool(
                chain_id=chain_id, protocol="uniswap", version="v2",
                architecture="direct_pair", factory_address=deployment_factory,
                router_addresses=[deployment_router] if deployment_router else [],
                pool_address=pair_addr, custody_address=pair_addr,
                token0=token0, token1=token1,
                creation_block=int(raw["blockNumber"], 16) if isinstance(raw["blockNumber"], str) else raw["blockNumber"],
                creation_transaction=raw["transactionHash"].hex(),
                verified=False, verification_confidence=0.0,
            ))

        # token1 == target
        raw_logs = get_logs_with_topics(
            w3, factory_addr_checksum,
            [PAIR_CREATED_TOPIC, None, address_topic(token)],
            search_from, to_block,
        )
        for raw in raw_logs:
            pair_addr = Web3.to_checksum_address("0x" + raw["data"][2:66].zfill(64)[-40:])
            if pair_addr in seen:
                continue
            seen.add(pair_addr)
            token0 = Web3.to_checksum_address("0x" + raw["topics"][1].hex()[-40:])
            token1 = Web3.to_checksum_address("0x" + raw["topics"][2].hex()[-40:])
            pools.append(VerifiedPool(
                chain_id=chain_id, protocol="uniswap", version="v2",
                architecture="direct_pair", factory_address=deployment_factory,
                router_addresses=[deployment_router] if deployment_router else [],
                pool_address=pair_addr, custody_address=pair_addr,
                token0=token0, token1=token1,
                creation_block=int(raw["blockNumber"], 16) if isinstance(raw["blockNumber"], str) else raw["blockNumber"],
                creation_transaction=raw["transactionHash"].hex(),
                verified=False, verification_confidence=0.0,
            ))

        t1 = _time.time()
        # Only log if we found something (silent skip otherwise)
        if pools:
            pass  # pools were added

    except Exception:
        pass  # Silent fallback: fast discovery results are still returned
