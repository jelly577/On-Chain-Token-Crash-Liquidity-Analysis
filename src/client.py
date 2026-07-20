"""Web3 client wrapper — manages RPC connections and chain interaction."""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Optional

from web3 import Web3
from web3.contract.contract import Contract


_ABI_CACHE: dict[str, list] = {}


def _abi_path(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent
    return here / "abis" / f"{name}.json"


def _load_abi(name: str) -> list:
    if name not in _ABI_CACHE:
        with open(_abi_path(name)) as f:
            _ABI_CACHE[name] = json.load(f)
    return _ABI_CACHE[name]


def get_web3(rpc_url: Optional[str] = None) -> Web3:
    """Return a Web3 instance connected to the given or env-configured RPC."""
    url = rpc_url or os.environ.get("ETH_RPC_URL")
    if not url:
        raise ValueError(
            "No RPC URL provided. Set ETH_RPC_URL or pass rpc_url."
        )
    w3 = Web3(Web3.HTTPProvider(url))
    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to {url}")
    return w3


def get_contract(w3: Web3, address: str, abi_name: str) -> Contract:
    """Return a Web3 contract instance for *address* using a named ABI."""
    abi = _load_abi(abi_name)
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def has_bytecode(w3: Web3, address: str) -> bool:
    """Return True if the address has non-empty bytecode."""
    code = w3.eth.get_code(Web3.to_checksum_address(address))
    return code.hex() not in ("0x", "")
