"""Protocol registry loader — reads the YAML config and provides typed access."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from web3 import Web3

from ..models import ProtocolDeployment


def _config_path() -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    return here / "config" / "protocols.ethereum.yaml"


def load_registry(path: Optional[str] = None) -> dict:
    """Load the protocol registry from YAML."""
    p = Path(path) if path else _config_path()
    with open(p) as f:
        return yaml.safe_load(f)


def get_chain_id(registry: dict) -> int:
    """Return the chain ID from the registry."""
    return registry.get("chain_id", 1)


def get_enabled_protocols(registry: dict) -> list[ProtocolDeployment]:
    """Return a list of enabled ProtocolDeployment objects."""
    result = []
    for entry in registry.get("protocols", []):
        if not entry.get("enabled", True):
            continue
        result.append(ProtocolDeployment(
            protocol=entry["protocol"],
            version=entry["version"],
            architecture=entry["architecture"],
            factory=entry["factory"],
            router=entry.get("router"),
            position_manager=entry.get("position_manager"),
            adapter=entry.get("adapter", ""),
            deployment_block=entry.get("deployment_block", 0),
            enabled=True,
        ))
    return result


def get_trusted_factories(registry: dict) -> set[str]:
    """Return checksum addresses of all enabled protocol factories."""
    return {
        Web3.to_checksum_address(dep.factory)
        for dep in get_enabled_protocols(registry)
    }


def is_trusted_factory(registry: dict, factory_address: str) -> bool:
    """Return True if *factory_address* is a whitelisted deployment."""
    return Web3.to_checksum_address(factory_address) in get_trusted_factories(registry)


def get_protocol_by_factory(
    registry: dict, factory_address: str
) -> Optional[ProtocolDeployment]:
    """Look up a protocol deployment by its factory address."""
    target = Web3.to_checksum_address(factory_address)
    for dep in get_enabled_protocols(registry):
        if Web3.to_checksum_address(dep.factory) == target:
            return dep
    return None


def get_quote_assets(registry: dict) -> list[dict]:
    """Return the list of quote asset definitions."""
    return registry.get("quote_assets", [])


def get_v3_fee_tiers(registry: dict) -> list[dict]:
    """Return the configured V3 fee tiers."""
    return registry.get("v3_fee_tiers", [])


def get_v3_fees(registry: dict) -> list[int]:
    """Return just the fee values from the V3 fee tier config."""
    return [tier["fee"] for tier in get_v3_fee_tiers(registry)]
