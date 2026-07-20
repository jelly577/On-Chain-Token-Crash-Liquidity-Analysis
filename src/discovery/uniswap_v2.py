"""Uniswap V2 pool discovery."""
from __future__ import annotations

from typing import Optional

from web3 import Web3
from web3.types import EventData

from ..client import get_contract
from ..models import ProtocolDeployment, VerifiedPool
from ..registry.loader import get_chain_id, load_registry
from .base import PoolDiscoveryAdapter
from .log_utils import dedupe_pools, get_logs_chunked


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

        # --- Exhaustive discovery via PairCreated events (chunked) ---
        event = factory.events.PairCreated
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
                evt: EventData = entry  # type: ignore[assignment]
                args = evt["args"]
                pair_addr = Web3.to_checksum_address(args["pair"])
                if pair_addr in seen:
                    continue
                seen.add(pair_addr)
                pools.append(VerifiedPool(
                    chain_id=chain_id,
                    protocol="uniswap",
                    version="v2",
                    architecture="direct_pair",
                    factory_address=self.deployment.factory,
                    router_addresses=[self.deployment.router] if self.deployment.router else [],
                    pool_address=pair_addr,
                    custody_address=pair_addr,
                    token0=Web3.to_checksum_address(args["token0"]),
                    token1=Web3.to_checksum_address(args["token1"]),
                    creation_block=entry["blockNumber"],
                    creation_transaction=entry["transactionHash"].hex(),
                    verified=False,
                    verification_confidence=0.0,
                ))

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
