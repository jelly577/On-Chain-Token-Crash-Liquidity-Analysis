"""Base adapter interface for pool discovery."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from web3 import Web3

from ..models import ProtocolDeployment, VerifiedPool


class PoolDiscoveryAdapter(ABC):
    """Discover pool candidates for a given token on a specific protocol."""

    def __init__(self, w3: Web3, deployment: ProtocolDeployment) -> None:
        self.w3 = w3
        self.deployment = deployment

    @abstractmethod
    def discover(
        self,
        token_address: str,
        from_block: int,
        to_block: int,
        quote_assets: Optional[list[dict]] = None,
    ) -> list[VerifiedPool]:
        """Return candidate pools containing *token_address*."""
        ...
