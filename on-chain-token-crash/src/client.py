"""Web3 client wrapper — manages RPC connections and chain interaction."""
from __future__ import annotations

import json
import os
import time as _time
from pathlib import Path
from typing import Optional

import requests as _requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from web3 import Web3
from web3.contract.contract import Contract
from web3.providers.rpc import HTTPProvider


_ABI_CACHE: dict[str, list] = {}


class _RateLimitedSession(_requests.Session):
    """Requests session with built-in rate limiting and retry on 429."""

    def __init__(self, delay: float = 0.2):
        super().__init__()
        self._delay = delay
        self._last_req: float = 0.0

        # Auto-retry on 429 / 5xx with exponential backoff
        retry_strategy = Retry(
            total=5,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def post(self, url: str, **kwargs) -> _requests.Response:
        # Rate limit: ensure minimum gap between requests
        elapsed = _time.time() - self._last_req
        if elapsed < self._delay:
            _time.sleep(self._delay - elapsed)
        self._last_req = _time.time()
        return super().post(url, **kwargs)


def _abi_path(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent
    return here / "abis" / f"{name}.json"


def _load_abi(name: str) -> list:
    if name not in _ABI_CACHE:
        with open(_abi_path(name)) as f:
            _ABI_CACHE[name] = json.load(f)
    return _ABI_CACHE[name]


def get_web3(rpc_url: Optional[str] = None) -> Web3:
    """Return a Web3 instance connected to the given or env-configured RPC.

    Includes built-in rate limiting (0.2s between requests) and
    automatic retry with backoff on 429 / 5xx responses.
    """
    url = rpc_url or os.environ.get("ETH_RPC_URL")
    if not url:
        raise ValueError(
            "No RPC URL provided. Set ETH_RPC_URL or pass rpc_url."
        )
    session = _RateLimitedSession(delay=0.2)
    provider = HTTPProvider(url, session=session)
    w3 = Web3(provider)
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
