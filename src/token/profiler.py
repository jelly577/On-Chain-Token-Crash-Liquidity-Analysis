"""Token profiler — validates and profiles an ERC-20 token."""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from ..client import get_contract, has_bytecode
from ..models import TokenProfile

# EIP-1967 / EIP-1822 proxy storage slots
EIP1967_IMPLEMENTATION_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)
EIP1967_BEACON_SLOT = (
    "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
)
EIP1822_PROXIABLE_SLOT = (
    "0xc5f16f0fcc639fa48a6947836d9850f504798523bf896c0a962285830193199cf"
)

# Common function selectors found in bytecode for behavior heuristics
BEHAVIOR_SELECTORS: dict[str, list[str]] = {
    "minting": ["40c10f19", "1249c58b", "a0712d68"],
    "pausing": ["8456cb59", "3f4ba83a", "5c975abb"],
    "blacklisting": ["fe575cf8", "f9f92beb", "e47d6060", "537df3b6"],
    "fee_on_transfer": ["c0323c68", "cc8842bc", "eb659a8c"],
    "rebasing": ["2f667e56", "0ba794ae", "7a28fb88"],
}


def _safe_call(contract, fn_name: str, default=None):
    """Call a read function and return *default* on failure."""
    try:
        fn = getattr(contract.functions, fn_name, None)
        if fn is None:
            return default
        return fn().call()
    except Exception:
        return default


def _address_from_storage_slot(w3: Web3, address: str, slot: str) -> Optional[str]:
    """Extract an address stored at a proxy storage slot."""
    try:
        raw = w3.eth.get_storage_at(
            Web3.to_checksum_address(address), slot
        )
        if raw == b"\x00" * 32:
            return None
        addr = Web3.to_checksum_address("0x" + raw.hex()[-40:])
        if addr == "0x0000000000000000000000000000000000000000":
            return None
        if not has_bytecode(w3, addr):
            return None
        return addr
    except Exception:
        return None


def _detect_proxy(
    w3: Web3, address: str, contract
) -> tuple[Optional[str], Optional[str]]:
    """Detect proxy and implementation addresses via storage slots and calls."""
    proxy_address: Optional[str] = None
    implementation_address: Optional[str] = None

    impl_slot = _address_from_storage_slot(w3, address, EIP1967_IMPLEMENTATION_SLOT)
    if impl_slot:
        proxy_address = Web3.to_checksum_address(address)
        implementation_address = impl_slot
        return proxy_address, implementation_address

    beacon = _address_from_storage_slot(w3, address, EIP1967_BEACON_SLOT)
    if beacon:
        try:
            beacon_contract = get_contract(w3, beacon, "erc20")
            impl = _safe_call(beacon_contract, "implementation", None)
            if impl and has_bytecode(w3, impl):
                proxy_address = Web3.to_checksum_address(address)
                implementation_address = Web3.to_checksum_address(impl)
                return proxy_address, implementation_address
        except Exception:
            pass

    proxiable = _address_from_storage_slot(w3, address, EIP1822_PROXIABLE_SLOT)
    if proxiable:
        proxy_address = Web3.to_checksum_address(address)
        implementation_address = proxiable
        return proxy_address, implementation_address

    impl_call = _safe_call(contract, "implementation", None)
    if impl_call and has_bytecode(w3, impl_call):
        proxy_address = Web3.to_checksum_address(address)
        implementation_address = Web3.to_checksum_address(impl_call)
        return proxy_address, implementation_address

    return None, None


def _detect_behavior_flags(w3: Web3, address: str, contract) -> list[str]:
    """Detect unusual token behaviors via bytecode selectors and safe calls."""
    flags: list[str] = []

    try:
        bytecode = w3.eth.get_code(Web3.to_checksum_address(address)).hex()
    except Exception:
        bytecode = ""

    if bytecode:
        clean = bytecode[2:] if bytecode.startswith("0x") else bytecode
        for flag, selectors in BEHAVIOR_SELECTORS.items():
            if any(sel in clean for sel in selectors):
                flags.append(flag)

    if _safe_call(contract, "paused", None) is True:
        if "pausing" not in flags:
            flags.append("pausing")

    return sorted(set(flags))


def profile_token(
    w3: Web3,
    address: str,
    chain_id: int = 1,
) -> TokenProfile:
    """Validate and profile a token address."""
    addr = Web3.to_checksum_address(address)
    contract = get_contract(w3, address, "erc20")

    name = _safe_call(contract, "name", "")
    symbol = _safe_call(contract, "symbol", "")
    decimals_raw = _safe_call(contract, "decimals", 18)
    try:
        decimals = int(decimals_raw) if decimals_raw is not None else 18
    except (TypeError, ValueError):
        decimals = 18

    total_supply_raw = _safe_call(contract, "totalSupply", None)
    try:
        total_supply = int(total_supply_raw) if total_supply_raw is not None else None
    except (TypeError, ValueError):
        total_supply = None

    is_contract = has_bytecode(w3, addr)
    proxy_address, implementation_address = _detect_proxy(w3, addr, contract)
    behavior_flags = _detect_behavior_flags(w3, addr, contract) if is_contract else []

    return TokenProfile(
        chain_id=chain_id,
        address=addr,
        symbol=str(symbol) if symbol else "???",
        name=str(name) if name else "???",
        decimals=decimals,
        is_contract=is_contract,
        proxy_address=proxy_address,
        implementation_address=implementation_address,
        behavior_flags=behavior_flags,
        total_supply=total_supply,
    )
