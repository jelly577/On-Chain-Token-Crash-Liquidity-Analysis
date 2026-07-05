"""Shared data models for the on-chain token crash analysis system."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TokenProfile:
    chain_id: int
    address: str
    symbol: str = ""
    name: str = ""
    decimals: int = 18
    is_contract: bool = False
    proxy_address: Optional[str] = None
    implementation_address: Optional[str] = None
    behavior_flags: list[str] = field(default_factory=list)
    total_supply: Optional[int] = None


@dataclass
class ProtocolDeployment:
    protocol: str
    version: str
    architecture: str
    factory: str
    router: Optional[str] = None
    position_manager: Optional[str] = None
    adapter: str = ""
    deployment_block: int = 0
    enabled: bool = True


@dataclass
class VerifiedPool:
    chain_id: int
    protocol: str
    version: str
    architecture: str
    factory_address: str
    router_addresses: list[str] = field(default_factory=list)
    pool_address: str = ""
    pool_id: Optional[str] = None
    custody_address: str = ""
    position_manager_address: Optional[str] = None
    gauge_addresses: list[str] = field(default_factory=list)
    hooks_address: Optional[str] = None
    token0: str = ""
    token1: str = ""
    fee: Optional[int] = None
    creation_block: int = 0
    creation_transaction: str = ""
    verified: bool = False
    verification_confidence: float = 0.0


@dataclass
class NormalizedEvent:
    block_number: int
    block_timestamp: int
    transaction_hash: str
    log_index: int
    protocol: str
    version: str
    pool_address: str
    event_type: str
    actor: str
    recipient: str = ""
    token0_amount: str = "0"
    token1_amount: str = "0"
    liquidity_delta: str = "0"
    source_event: str = ""
    verified: bool = False


@dataclass
class Position:
    pool_address: str
    owner: str
    lp_token_address: Optional[str] = None
    nft_token_id: Optional[int] = None
    liquidity: str = "0"
    share_pct: float = 0.0
    beneficial_owner: Optional[str] = None
    resolution_method: str = ""
    confidence: float = 0.0


@dataclass
class AddressLabel:
    address: str
    label: str
    category: str = ""
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)


def to_dict(instance):
    """Convert a dataclass instance to a JSON-serializable dict."""
    return {k: v for k, v in asdict(instance).items() if v is not None}
