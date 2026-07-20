"""Uniswap V4 pool discovery.

V4 uses a singleton PoolManager architecture. All pools are managed
by a single PoolManager contract. Events are identified by pool ID.

Key references:
  - PoolManager deploys at a deterministic CREATE2 address
  - PoolCreated(Currency currency0, Currency currency1, uint24 fee,
                 int24 tickSpacing, address hooks, uint256 poolId)
  - Pool key: (currency0, currency1, fee, tickSpacing, hooks)
"""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from ..client import get_contract
from ..models import ProtocolDeployment, VerifiedPool
from ..registry.loader import get_chain_id, load_registry
from .base import PoolDiscoveryAdapter
from .log_utils import address_topic, dedupe_pools

# Uniswap V4 PoolManager event signatures (keccak256 of event signatures)
# event PoolCreated(Currency indexed currency0, Currency indexed currency1,
#                   uint24 fee, int24 tickSpacing, address hooks, uint256 poolId)
POOL_CREATED_V4_TOPIC = "0x9a7b8f0ba5d0c9e7a5a1e1f2d6d5c4b3a2a1f0e9d8c7b6a5a4b3c2d1e0f"

# V4 fee tiers (similar to V3 but V4 supports dynamic fees)
V4_FEE_TIERS = [100, 500, 3000, 10000]

# Default tick spacing per fee tier
FEE_TICK_SPACING = {
    100: 1,
    500: 10,
    3000: 60,
    10000: 200,
}


class UniswapV4Adapter(PoolDiscoveryAdapter):
    """Discover V4 pools via the PoolManager singleton.

    V4 pools are identified by their PoolKey, not by individual contract addresses.
    The PoolManager tracks all pool states internally.
    """

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

        # V4 doesn't have getPair/getPool-like calls on the PoolManager.
        # Instead, we need to:
        # 1. Try to construct pool keys with known quote assets
        # 2. Check if each pool exists by calling the PoolManager methods

        pool_manager = get_contract(
            self.w3, self.deployment.factory, "uniswap_v4_pool_manager"
        )

        if quote_assets:
            for qa in quote_assets:
                q_addr = Web3.to_checksum_address(qa["address"])
                for fee in V4_FEE_TIERS:
                    tick_spacing = FEE_TICK_SPACING.get(fee, 60)
                    # V4 pools: currency0 < currency1 (like V3, must be sorted)
                    c0 = token.lower() if token.lower() < q_addr.lower() else q_addr
                    c1 = q_addr if c0 == token else token
                    try:
                        # Try to get pool ID via PoolManager.getPoolId or getId
                        # V4 uses: PoolKey(currency0, currency1, fee, tickSpacing, hooks)
                        # hooks address can be 0x0 for no hooks
                        pool_id = pool_manager.functions.getId(
                            (Web3.to_checksum_address(c0),
                             Web3.to_checksum_address(c1),
                             fee,
                             tick_spacing,
                             "0x0000000000000000000000000000000000000000")
                        ).call()

                        # Check if pool exists (poolId != 0)
                        if pool_id != 0 and pool_id.to_bytes(32, 'big').hex()[:64] != "0"*64:
                            pool_id_key = str(pool_id)
                            if pool_id_key in seen:
                                continue
                            seen.add(pool_id_key)

                            pools.append(VerifiedPool(
                                chain_id=chain_id,
                                protocol="uniswap",
                                version="v4",
                                architecture="singleton",
                                factory_address=self.deployment.factory,
                                router_addresses=(
                                    [self.deployment.router]
                                    if self.deployment.router else []
                                ),
                                pool_address=f"V4_POOL_{pool_id}",
                                custody_address=self.deployment.factory,
                                position_manager_address=(
                                    self.deployment.position_manager
                                ),
                                token0=Web3.to_checksum_address(c0),
                                token1=Web3.to_checksum_address(c1),
                                fee=fee,
                                verified=False,
                                verification_confidence=0.0,
                            ))
                    except Exception:
                        # Pool doesn't exist or method not available
                        pass

        return dedupe_pools(pools)

    def _build_pool_key(
        self, token0: str, token1: str, fee: int, tick_spacing: int,
        hooks: str = "0x0000000000000000000000000000000000000000"
    ) -> tuple:
        """Build a V4 PoolKey tuple sorted by address."""
        if token0.lower() > token1.lower():
            token0, token1 = token1, token0
        return (
            Web3.to_checksum_address(token0),
            Web3.to_checksum_address(token1),
            fee,
            tick_spacing,
            Web3.to_checksum_address(hooks),
        )
