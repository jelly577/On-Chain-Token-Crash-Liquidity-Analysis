"""Pool verification — confirms discovered pools against their Factory."""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from ..client import get_contract, has_bytecode
from ..discovery.log_utils import get_logs_chunked
from ..models import VerifiedPool
from ..registry.loader import (
    get_protocol_by_factory,
    get_v3_fee_tiers,
    is_trusted_factory,
    load_registry,
)

MIN_CONFIDENCE = 0.3


def verify_pool(
    w3: Web3,
    pool: VerifiedPool,
    target_token: Optional[str] = None,
    from_block: int = 0,
    to_block: int = 0,
) -> VerifiedPool:
    """Run verification checks on a candidate pool and update its confidence."""
    pool = VerifiedPool(**{k: v for k, v in pool.__dict__.items()})
    registry = load_registry()

    checks_passed = 0
    checks_total = 0

    # 0. Factory must be whitelisted
    checks_total += 1
    if is_trusted_factory(registry, pool.factory_address):
        checks_passed += 1
    else:
        pool.verified = False
        pool.verification_confidence = 0.0
        return pool

    # Resolve custody and position model from architecture
    pool = _resolve_custody(w3, pool, registry)

    # 1. Contract has bytecode
    checks_total += 1
    if has_bytecode(w3, pool.pool_address):
        checks_passed += 1

    contract = None
    try:
        contract = get_contract(w3, pool.pool_address, _abi_name(pool))
    except Exception:
        pass

    if contract is None:
        pool.verified = False
        pool.verification_confidence = round(checks_passed / max(checks_total, 1), 4)
        return pool

    # 2. Verify factory on-chain
    checks_total += 1
    try:
        onchain_factory = contract.functions.factory().call()
        if Web3.to_checksum_address(onchain_factory) == Web3.to_checksum_address(pool.factory_address):
            checks_passed += 1
    except Exception:
        pass

    # 3. Verify token pair
    checks_total += 1
    try:
        t0 = Web3.to_checksum_address(contract.functions.token0().call())
        t1 = Web3.to_checksum_address(contract.functions.token1().call())
        if pool.token0 and pool.token1:
            if t0 == Web3.to_checksum_address(pool.token0) and t1 == Web3.to_checksum_address(pool.token1):
                checks_passed += 1
        else:
            pool.token0 = t0
            pool.token1 = t1
            checks_passed += 1
    except Exception:
        pass

    # 4. Target token must be in the pair
    if target_token:
        checks_total += 1
        target = Web3.to_checksum_address(target_token)
        if target in (pool.token0, pool.token1):
            checks_passed += 1

    # Version-specific checks
    if pool.version == "v2":
        checks_passed, checks_total = _verify_v2(
            w3, pool, contract, checks_passed, checks_total
        )
    elif pool.version == "v3":
        checks_passed, checks_total = _verify_v3(
            w3, pool, contract, registry, checks_passed, checks_total
        )

    # Event provenance
    checks_total += 1
    if _verify_event_provenance(w3, pool, from_block, to_block):
        checks_passed += 1

    confidence = checks_passed / max(checks_total, 1)
    pool.verified = confidence >= MIN_CONFIDENCE
    pool.verification_confidence = round(confidence, 4)
    return pool


def verify_pools(
    w3: Web3,
    pools: list[VerifiedPool],
    target_token: Optional[str] = None,
    from_block: int = 0,
    to_block: int = 0,
) -> list[VerifiedPool]:
    """Verify a list of candidate pools."""
    return [
        verify_pool(w3, pool, target_token, from_block, to_block)
        for pool in pools
    ]


def _resolve_custody(
    w3: Web3, pool: VerifiedPool, registry: dict
) -> VerifiedPool:
    """Set custody and position-manager fields based on architecture."""
    deployment = get_protocol_by_factory(registry, pool.factory_address)
    if deployment is None:
        return pool

    pool.architecture = deployment.architecture

    if pool.version == "v2":
        pool.custody_address = pool.pool_address
        if deployment.router and not pool.router_addresses:
            pool.router_addresses = [deployment.router]
    elif pool.version == "v3":
        pool.custody_address = pool.pool_address
        if deployment.position_manager:
            pool.position_manager_address = deployment.position_manager
        if deployment.router and not pool.router_addresses:
            pool.router_addresses = [deployment.router]

    return pool


def _verify_v2(
    w3: Web3,
    pool: VerifiedPool,
    contract,
    checks_passed: int,
    checks_total: int,
) -> tuple[int, int]:
    """V2-specific verification: getPair cross-check and reserves."""
    checks_total += 1
    try:
        factory = get_contract(w3, pool.factory_address, "uniswap_v2_factory")
        expected_pair = factory.functions.getPair(
            Web3.to_checksum_address(pool.token0),
            Web3.to_checksum_address(pool.token1),
        ).call()
        if Web3.to_checksum_address(expected_pair) == Web3.to_checksum_address(pool.pool_address):
            checks_passed += 1
    except Exception:
        pass

    checks_total += 1
    try:
        contract.functions.getReserves().call()
        checks_passed += 1
    except Exception:
        pass

    return checks_passed, checks_total


def _verify_v3(
    w3: Web3,
    pool: VerifiedPool,
    contract,
    registry: dict,
    checks_passed: int,
    checks_total: int,
) -> tuple[int, int]:
    """V3-specific verification: fee, tickSpacing, getPool, slot0, liquidity, PM."""
    if pool.fee is not None:
        checks_total += 1
        try:
            onchain_fee = contract.functions.fee().call()
            if onchain_fee == pool.fee:
                checks_passed += 1
        except Exception:
            pass

        expected_spacing = _expected_tick_spacing(registry, pool.fee)
        if expected_spacing is not None:
            checks_total += 1
            try:
                onchain_spacing = contract.functions.tickSpacing().call()
                if onchain_spacing == expected_spacing:
                    checks_passed += 1
            except Exception:
                pass

        checks_total += 1
        try:
            factory = get_contract(w3, pool.factory_address, "uniswap_v3_factory")
            expected_pool = factory.functions.getPool(
                Web3.to_checksum_address(pool.token0),
                Web3.to_checksum_address(pool.token1),
                pool.fee,
            ).call()
            if Web3.to_checksum_address(expected_pool) == Web3.to_checksum_address(pool.pool_address):
                checks_passed += 1
        except Exception:
            pass

    checks_total += 1
    try:
        contract.functions.slot0().call()
        checks_passed += 1
    except Exception:
        pass

    checks_total += 1
    try:
        contract.functions.liquidity().call()
        checks_passed += 1
    except Exception:
        pass

    if pool.position_manager_address:
        checks_total += 1
        if has_bytecode(w3, pool.position_manager_address):
            checks_passed += 1

    return checks_passed, checks_total


def _expected_tick_spacing(registry: dict, fee: int) -> Optional[int]:
    for tier in get_v3_fee_tiers(registry):
        if tier["fee"] == fee:
            return tier.get("tick_spacing")
    return None


def _verify_event_provenance(
    w3: Web3,
    pool: VerifiedPool,
    from_block: int,
    to_block: int,
) -> bool:
    """Confirm the pool was created via an official Factory event."""
    try:
        if pool.version == "v2":
            factory = get_contract(w3, pool.factory_address, "uniswap_v2_factory")
            event = factory.events.PairCreated
            pool_key = "pair"
        else:
            factory = get_contract(w3, pool.factory_address, "uniswap_v3_factory")
            event = factory.events.PoolCreated
            pool_key = "pool"

        if pool.creation_block > 0:
            logs = event.get_logs(
                from_block=pool.creation_block,
                to_block=pool.creation_block,
            )
            for log in logs:
                if Web3.to_checksum_address(log["args"][pool_key]) == Web3.to_checksum_address(pool.pool_address):
                    return True
            return False

        search_from = from_block if from_block > 0 else 0
        search_to = to_block if to_block > 0 else w3.eth.block_number
        logs = get_logs_chunked(event, search_from, search_to)
        for log in logs:
            if Web3.to_checksum_address(log["args"][pool_key]) == Web3.to_checksum_address(pool.pool_address):
                if not pool.creation_block:
                    pool.creation_block = log["blockNumber"]
                    pool.creation_transaction = log["transactionHash"].hex()
                return True
        return False
    except Exception:
        return False


def _abi_name(pool: VerifiedPool) -> str:
    if pool.version == "v2":
        return "uniswap_v2_pair"
    return "uniswap_v3_pool"
