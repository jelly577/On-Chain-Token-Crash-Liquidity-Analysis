"""CLI entry point for the on-chain token crash analysis system."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from .client import get_web3
from .discovery.engine import discover_pools
from .models import VerifiedPool
from .token.profiler import profile_token
from .registry.loader import load_registry, get_chain_id
from .verification.verifier import verify_pools

app = typer.Typer()


@app.command()
def analyze(
    token_address: str = typer.Argument(..., help="Token contract address"),
    chain_id: int = typer.Option(1, help="Chain ID"),
    from_block: int = typer.Option(19000000, help="Start block"),
    to_block: int = typer.Option(19100000, help="End block"),
    rpc_url: str = typer.Option("", envvar="ETH_RPC_URL", help="RPC URL"),
    output_dir: str = typer.Option("output", help="Output directory"),
):
    """Analyze a token's on-chain liquidity and crash timeline."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    w3 = get_web3(rpc_url or None)
    registry = load_registry()
    chain_id = get_chain_id(registry)

    # Step 1: Token profile
    typer.echo(f"Profiling token {token_address} ...")
    profile = profile_token(w3, token_address, chain_id)
    _write_json(out / "token_profile.json", profile.__dict__)
    typer.echo(f"  Symbol: {profile.symbol}, Decimals: {profile.decimals}")

    # Step 2: Discover pools
    typer.echo("Discovering pools ...")
    result = discover_pools(w3, token_address, from_block, to_block, chain_id)
    _write_json(out / "pool_candidates.json", result)
    typer.echo(f"  Found {len(result['pools'])} candidate(s)")

    # Step 3: Verify pools
    typer.echo("Verifying pools ...")
    candidates = [VerifiedPool(**dict(pdata)) for pdata in result["pools"]]
    verified_pools = verify_pools(
        w3, candidates, target_token=token_address,
        from_block=from_block, to_block=to_block,
    )
    for verified in verified_pools:
        if verified.verified:
            typer.echo(f"  OK  {verified.pool_address}")
        else:
            typer.echo(f"  FAIL {verified.pool_address} (conf={verified.verification_confidence})")

    _write_json(out / "verified_pools.json", [p.__dict__ for p in verified_pools])

    # Summary
    typer.echo(f"\nDone. Output written to {out.resolve()}")


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


if __name__ == "__main__":
    app()
