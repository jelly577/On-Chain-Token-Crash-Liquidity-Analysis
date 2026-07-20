"""Resolve a token query (address, symbol, or name) to a mainnet contract address."""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from web3 import Web3

# Common Ethereum mainnet aliases (offline fast path)
_LOCAL_ALIASES: dict[str, str] = {
    "eth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "usdt": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "dai": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    "wbtc": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "uni": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
}

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"


class TokenResolveError(ValueError):
    """Raised when a token query cannot be resolved uniquely."""


def is_address(value: str) -> bool:
    return bool(_ADDRESS_RE.match((value or "").strip()))


def resolve_token(
    query: str,
    chain_id: int = 1,
    pick: int = 0,
) -> dict[str, Any]:
    """Resolve ``query`` to a token contract on ``chain_id``.

    Accepts:
      - ``0x...`` contract address
      - symbol / name (e.g. ``USDC``, ``CREDI``) via local aliases + DexScreener

    Returns dict with keys: address, symbol, name, source, candidates (list).
    """
    q = (query or "").strip()
    if not q:
        raise TokenResolveError("Empty token query")

    if is_address(q):
        addr = Web3.to_checksum_address(q)
        return {
            "address": addr,
            "symbol": "",
            "name": "",
            "source": "address",
            "candidates": [{"address": addr, "symbol": "", "name": "", "liquidity_usd": 0}],
        }

    key = q.lower()
    if chain_id == 1 and key in _LOCAL_ALIASES:
        addr = Web3.to_checksum_address(_LOCAL_ALIASES[key])
        return {
            "address": addr,
            "symbol": q.upper(),
            "name": q.upper(),
            "source": "local_alias",
            "candidates": [{
                "address": addr,
                "symbol": q.upper(),
                "name": q.upper(),
                "liquidity_usd": 0,
            }],
        }

    candidates = _search_dexscreener(q, chain_id=chain_id)
    if not candidates:
        raise TokenResolveError(
            "No Ethereum token found for '{}'. Try the 0x contract address.".format(q)
        )

    if pick < 0 or pick >= len(candidates):
        raise TokenResolveError(
            "Invalid --pick {}; {} candidate(s) available:\n{}".format(
                pick, len(candidates), _format_candidates(candidates)
            )
        )

    # Ambiguous symbol matches: require exact symbol unless only one candidate
    exact = [c for c in candidates if c["symbol"].lower() == key]
    pool = exact if exact else candidates

    if pick == 0 and len(pool) > 1 and not exact:
        # Multiple fuzzy name hits — still pick richest, but expose list
        chosen = pool[0]
    elif pick == 0 and len(exact) > 1:
        chosen = exact[0]  # highest liquidity among exact symbol matches
    else:
        chosen = pool[pick] if pick < len(pool) else candidates[pick]

    return {
        "address": chosen["address"],
        "symbol": chosen.get("symbol", ""),
        "name": chosen.get("name", ""),
        "source": "dexscreener",
        "candidates": candidates,
    }


def _search_dexscreener(query: str, chain_id: int = 1) -> list[dict[str, Any]]:
    """Search DexScreener and return unique tokens on the target chain, richest first."""
    chain_slug = {1: "ethereum"}.get(chain_id)
    if not chain_slug:
        raise TokenResolveError(
            "Name lookup currently supports Ethereum mainnet (chain_id=1) only"
        )

    try:
        resp = httpx.get(
            _DEXSCREENER_SEARCH,
            params={"q": query},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise TokenResolveError(
            "Token name lookup failed (network/API): {}".format(exc)
        ) from exc

    pairs = data.get("pairs") or []
    by_addr: dict[str, dict[str, Any]] = {}
    q_lower = query.lower()

    for pair in pairs:
        if (pair.get("chainId") or "").lower() != chain_slug:
            continue
        base = pair.get("baseToken") or {}
        addr = (base.get("address") or "").strip()
        if not is_address(addr):
            continue
        addr = Web3.to_checksum_address(addr)
        symbol = (base.get("symbol") or "").strip()
        name = (base.get("name") or "").strip()
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)

        # Prefer tokens whose symbol/name relates to the query
        if q_lower not in symbol.lower() and q_lower not in name.lower():
            # Still keep if DexScreener returned it as a hit; soft filter later
            pass

        prev = by_addr.get(addr)
        if prev is None or liq > prev["liquidity_usd"]:
            by_addr[addr] = {
                "address": addr,
                "symbol": symbol,
                "name": name,
                "liquidity_usd": liq,
            }

    ranked = sorted(by_addr.values(), key=lambda c: c["liquidity_usd"], reverse=True)

    # Prefer exact symbol matches at the front
    exact = [c for c in ranked if c["symbol"].lower() == q_lower]
    rest = [c for c in ranked if c["symbol"].lower() != q_lower]
    return exact + rest


def _format_candidates(candidates: list[dict[str, Any]], limit: int = 8) -> str:
    lines = []
    for i, c in enumerate(candidates[:limit]):
        lines.append(
            "  [{}] {} ({})  {}  liq=${:,.0f}".format(
                i,
                c.get("symbol") or "?",
                c.get("name") or "?",
                c.get("address"),
                float(c.get("liquidity_usd") or 0),
            )
        )
    if len(candidates) > limit:
        lines.append("  ... and {} more".format(len(candidates) - limit))
    return "\n".join(lines)


def format_resolve_summary(resolved: dict[str, Any]) -> str:
    cands = resolved.get("candidates") or []
    lines = [
        "Resolved '{}' → {} ({}) via {}".format(
            resolved.get("symbol") or resolved.get("name") or resolved["address"],
            resolved["address"],
            resolved.get("symbol") or "?",
            resolved.get("source"),
        )
    ]
    if len(cands) > 1:
        lines.append("Other candidates (use --pick N):")
        lines.append(_format_candidates(cands))
    return "\n".join(lines)
