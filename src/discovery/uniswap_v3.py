"""Uniswap V3 pool discovery."""
from __future__ import annotations

from typing import Optional

from web3 import Web3
from web3.types import EventData

from ..client import get_contract
from ..models import ProtocolDeployment, VerifiedPool
from ..registry.loader import get_chain_id, get_v3_fees, load_registry
from .base import PoolDiscoveryAdapter
from .log_utils import dedupe_pools, get_logs_chunked


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

        # --- Exhaustive discovery via PoolCreated events (chunked) ---
        event = factory.events.PoolCreated
        search_from = max(from_block, self.deployment.deployment_block)

        for token_role in ("token0", "token1"):
            arg_filter = {"token0": token} if token_role == "token0" else {"token1": token}
            entries = get_logs_chunked(
                event,
                from_block=search_from,
                to_block=to_block,
                argument_filters=arg_filter,
            )

            for entry in entries:
                evt: EventData = entry
                args = evt["args"]
                pool_addr = Web3.to_checksum_address(args["pool"])
                if pool_addr in seen:
                    continue
                seen.add(pool_addr)
                pools.append(VerifiedPool(
                    chain_id=chain_id,
                    protocol="uniswap",
                    version="v3",
                    architecture="concentrated_pool",
                    factory_address=self.deployment.factory,
                    router_addresses=(
                        [self.deployment.router] if self.deployment.router else []
                    ),
                    pool_address=pool_addr,
                    custody_address=pool_addr,
                    position_manager_address=self.deployment.position_manager,
                    token0=Web3.to_checksum_address(args["token0"]),
                    token1=Web3.to_checksum_address(args["token1"]),
                    fee=args["fee"],
                    creation_block=entry["blockNumber"],
                    creation_transaction=entry["transactionHash"].hex(),
                    verified=False,
                    verification_confidence=0.0,
                ))

        return dedupe_pools(pools)

    def _candidate_from_address(
        self, pool_addr: str, fee: int, chain_id: int
    ) -> VerifiedPool:
        try:
            pool = get_contract(self.w3, pool_addr, "uniswap_v3_pool")
            token0 = Web3.to_checksum_address(pool.functions.token0().call())
            token1 = Web3.to_checksum_address(pool.functions.token1().call())
            return VerifiedPool(
                chain_id=chain_id,
                protocol="uniswap",
                version="v3",
                architecture="concentrated_pool",
                factory_address=self.deployment.factory,
                router_addresses=(
                    [self.deployment.router] if self.deployment.router else []
                ),
                pool_address=pool_addr,
                custody_address=pool_addr,
                position_manager_address=self.deployment.position_manager,
                token0=token0,
                token1=token1,
                fee=fee,
                verified=False,
                verification_confidence=0.0,
            )
        except Exception:
            return VerifiedPool(
                chain_id=chain_id,
                protocol="uniswap",
                version="v3",
                architecture="concentrated_pool",
                factory_address=self.deployment.factory,
                router_addresses=(
                    [self.deployment.router] if self.deployment.router else []
                ),
                pool_address=pool_addr,
                custody_address=pool_addr,
                position_manager_address=self.deployment.position_manager,
                fee=fee,
                verified=False,
                verification_confidence=0.0,
            )
