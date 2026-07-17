"""Address labeling — identifies deployer, initial LP, treasury, whale, gauge, locker addresses."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from web3 import Web3

from ..client import get_contract
from ..models import AddressLabel, Position, VerifiedPool
from ..discovery.log_utils import get_logs_chunked

BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x000000000000000000000000000000000000dEaD",
}


def find_deployer(w3: Web3, token_address: str, from_block: int = 0) -> Optional[str]:
    """Find the token contract deployer via creation-block binary search + create tx.

    ``from_block`` is unused for the search (kept for call-site compatibility).
    """
    try:
        addr = Web3.to_checksum_address(token_address)
        code = w3.eth.get_code(addr)
        if not code:
            return None

        # Binary search the earliest block where code exists
        low, high = 0, int(w3.eth.block_number)
        while low < high:
            mid = (low + high) // 2
            try:
                has_code = bool(w3.eth.get_code(addr, mid))
            except Exception:
                has_code = False
            if has_code:
                high = mid
            else:
                low = mid + 1
        creation_block = low

        block = w3.eth.get_block(creation_block, full_transactions=True)
        for tx in block.get("transactions", []) or []:
            if tx.get("to"):
                continue
            try:
                receipt = w3.eth.get_transaction_receipt(tx["hash"])
                created = receipt.get("contractAddress")
                if created and created.lower() == addr.lower():
                    return Web3.to_checksum_address(tx["from"])
            except Exception:
                continue
    except Exception:
        pass
    return None


def label_positions(
    positions: list[Position],
    token_address: str,
    verified_pools: list[VerifiedPool],
    deployer: Optional[str] = None,
) -> list[AddressLabel]:
    """Label addresses from position data: whale, initial LP, deployer-associated."""
    labels: list[AddressLabel] = []
    seen: set[str] = set()
    token_addr = Web3.to_checksum_address(token_address)

    # Label deployer
    if deployer and deployer not in seen:
        labels.append(AddressLabel(
            address=deployer,
            label="Deployer",
            category="token_creator",
            confidence=1.0,
            evidence=["Token contract creator address"],
        ))
        seen.add(deployer)

    # Label large holders (whales): > 5% share
    for pos in positions:
        addr = Web3.to_checksum_address(pos.owner)
        if addr in seen or addr in BURN_ADDRESSES:
            continue

        if pos.share_pct >= 5.0:
            labels.append(AddressLabel(
                address=addr,
                label="Whale LP",
                category="large_liquidity_provider",
                confidence=0.8,
                evidence=[
                    "LP share: {:.2f}%".format(pos.share_pct),
                    "Pool: {}".format(pos.pool_address),
                ],
            ))
            seen.add(addr)

    # Label deployer-associated addresses (same as top LP or interacted with deployer)
    if deployer:
        for pos in positions:
            addr = Web3.to_checksum_address(pos.owner)
            if addr in seen:
                continue
            # Check if deployer is also an LP in the same pool
            for other_pos in positions:
                if (other_pos.owner == deployer and
                    other_pos.pool_address == pos.pool_address and
                    addr != deployer):
                    labels.append(AddressLabel(
                        address=addr,
                        label="Pool Co-LP with Deployer",
                        category="associated_address",
                        confidence=0.6,
                        evidence=[
                            "Shares pool {} with deployer {}".format(
                                pos.pool_address, deployer
                            ),
                        ],
                    ))
                    seen.add(addr)
                    break

    return labels


def label_pool_addresses(
    pools: list[VerifiedPool],
    events: list[dict],
) -> list[AddressLabel]:
    """Label pool-related addresses: factory, router, position manager."""
    labels: list[AddressLabel] = []
    seen: set[str] = set()

    for pool in pools:
        factory = Web3.to_checksum_address(pool.factory_address)
        if factory not in seen:
            labels.append(AddressLabel(
                address=factory,
                label="Factory ({})_{}".format(pool.protocol, pool.version),
                category="protocol_deployment",
                confidence=1.0,
                evidence=["Whitelisted protocol factory"],
            ))
            seen.add(factory)

        pool_addr = Web3.to_checksum_address(pool.pool_address)
        if pool_addr not in seen:
            label = "{} {} Pool".format(pool.protocol.title(), pool.version.upper())
            labels.append(AddressLabel(
                address=pool_addr,
                label=label,
                category="pool",
                confidence=1.0 if pool.verified else pool.verification_confidence,
                evidence=[
                    "Verified pool for token pair {}/{}".format(
                        pool.token0[:10], pool.token1[:10]
                    ),
                ],
            ))
            seen.add(pool_addr)

        custody = Web3.to_checksum_address(pool.custody_address)
        if custody not in seen and custody != pool_addr:
            labels.append(AddressLabel(
                address=custody,
                label="Custody ({})_{}".format(pool.protocol, pool.version),
                category="custody",
                confidence=1.0,
                evidence=["Asset custody address"],
            ))
            seen.add(custody)

        if pool.position_manager_address:
            pm = Web3.to_checksum_address(pool.position_manager_address)
            if pm not in seen:
                labels.append(AddressLabel(
                    address=pm,
                    label="PositionManager ({}_{})".format(pool.protocol, pool.version),
                    category="position_manager",
                    confidence=1.0,
                    evidence=["Manages LP position NFTs"],
                ))
                seen.add(pm)

        for router_addr in pool.router_addresses:
            router = Web3.to_checksum_address(router_addr)
            if router not in seen:
                labels.append(AddressLabel(
                    address=router,
                    label="Router ({}_{})".format(pool.protocol, pool.version),
                    category="router",
                    confidence=1.0,
                    evidence=["Protocol router for swaps"],
                ))
                seen.add(router)

    return labels


def label_transfer_addresses(
    events: list[dict],
    top_n: int = 20,
) -> list[AddressLabel]:
    """Label frequently occurring addresses from token transfer events."""
    labels: list[AddressLabel] = []
    from_counts: dict[str, int] = {}
    to_counts: dict[str, int] = {}
    seen: set[str] = set()

    for evt in events:
        if evt.get("event_type") == "TOKEN_TRANSFER":
            actor = evt.get("actor", "")
            recipient = evt.get("recipient", "")
            if actor:
                from_counts[actor] = from_counts.get(actor, 0) + 1
            if recipient:
                to_counts[recipient] = to_counts.get(recipient, 0) + 1

    # Label frequent recipients as "Frequent Receiver"
    sorted_recipients = sorted(to_counts.items(), key=lambda x: -x[1])
    for addr, count in sorted_recipients[:top_n]:
        if addr in seen or addr in BURN_ADDRESSES:
            continue
        if count >= 5:
            labels.append(AddressLabel(
                address=addr,
                label="Frequent Token Receiver",
                category="frequent_interactor",
                confidence=0.5,
                evidence=["Received token in {} transfer(s)".format(count)],
            ))
            seen.add(addr)

    # Label frequent senders
    sorted_senders = sorted(from_counts.items(), key=lambda x: -x[1])
    for addr, count in sorted_senders[:top_n]:
        if addr in seen or addr in BURN_ADDRESSES:
            continue
        if count >= 5:
            labels.append(AddressLabel(
                address=addr,
                label="Frequent Token Sender",
                category="frequent_interactor",
                confidence=0.5,
                evidence=["Sent token in {} transfer(s)".format(count)],
            ))
            seen.add(addr)

    return labels


def analyze_labels(
    token_address: str,
    verified_pools: list[VerifiedPool],
    positions: list[Position],
    events_swaps: list[dict],
    events_liquidity: list[dict],
    events_transfers: list[dict],
    deployer: Optional[str] = None,
    output_dir: str | Path = "output",
) -> list[AddressLabel]:
    """Main entry point: generate all address labels."""
    out = Path(output_dir)
    labels: list[AddressLabel] = []

    # Pool-related labels
    pool_labels = label_pool_addresses(verified_pools, events_liquidity)
    labels.extend(pool_labels)

    # Position-based labels (whales, deployer relationships)
    pos_labels = label_positions(positions, token_address, verified_pools, deployer)
    labels.extend(pos_labels)

    # Transfer-based labels
    all_events = events_swaps + events_liquidity + events_transfers
    transfer_labels = label_transfer_addresses(all_events)
    labels.extend(transfer_labels)

    # Burn address
    for burn_addr in BURN_ADDRESSES:
        labels.append(AddressLabel(
            address=burn_addr,
            label="Burn Address",
            category="burn",
            confidence=1.0,
            evidence=["Standard burn/destroy address"],
        ))

    # Write output
    label_dicts = [l.__dict__ for l in labels]
    _write_json(out / "address_labels.json", label_dicts)

    return labels


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

