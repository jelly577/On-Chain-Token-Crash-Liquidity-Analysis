"""CLI entry point for the on-chain token crash analysis system."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .client import get_web3
from .discovery.engine import discover_pools
from .models import VerifiedPool, to_dict
from .token.profiler import profile_token
from .registry.loader import load_registry, get_chain_id
from .verification.verifier import verify_pools
from .indexer.indexer import index_events
from .analysis.positions import analyze_positions
from .analysis.labels import analyze_labels
from .analysis.metrics import calculate_all_metrics
from .analysis.timeline import analyze_timeline
from .analysis.risk import compute_risk
from .report.generator import generate_report

app = typer.Typer()


@app.command()
def analyze(
    token_address: str = typer.Argument(..., help="Token contract address"),
    chain_id: int = typer.Option(1, help="Chain ID"),
    from_block: int = typer.Option(19000000, help="Start block"),
    to_block: int = typer.Option(19100000, help="End block"),
    incident_block: int = typer.Option(0, help="Block number of the crash incident (optional)"),
    rpc_url: str = typer.Option("", envvar="ETH_RPC_URL", help="RPC URL"),
    output_dir: str = typer.Option("output", help="Output directory"),
    fast_mode: bool = typer.Option(False, help="Skip exhaustive event indexing (faster, less data)"),
):
    """End-to-end analysis of a token's on-chain liquidity and crash timeline.

    Runs the full pipeline:
      1. Token profiling
      2. Pool discovery
      3. Pool verification
      4. Event indexing
      5. Position analysis
      6. Address labeling
      7. Metrics calculation
      8. Timeline analysis
      9. Risk assessment
      10. Report generation
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    w3 = get_web3(rpc_url or None)
    registry = load_registry()
    chain_id_val = get_chain_id(registry)

    # Step 1: Token profile
    typer.echo("[1/10] Profiling token ...")
    profile = profile_token(w3, token_address, chain_id_val)
    _write_json(out / "token_profile.json", profile.__dict__)
    typer.echo("  Symbol: {}, Decimals: {}".format(profile.symbol, profile.decimals))
    target_token = profile.address

    # Step 2: Discover pools
    typer.echo("[2/10] Discovering pools ...")
    result = discover_pools(w3, token_address, from_block, to_block, chain_id_val)
    _write_json(out / "pool_candidates.json", result)
    typer.echo("  Found {} candidate(s)".format(len(result["pools"])))

    # Step 3: Verify pools
    typer.echo("[3/10] Verifying pools ...")
    candidates = [VerifiedPool(**dict(pdata)) for pdata in result["pools"]]
    verified_pools = verify_pools(
        w3, candidates, target_token=token_address,
        from_block=from_block, to_block=to_block,
    )
    verified_count = sum(1 for p in verified_pools if p.verified)
    for p in verified_pools:
        status = "OK" if p.verified else "FAIL"
        typer.echo("  {} {}".format(status, p.pool_address))
    _write_json(out / "verified_pools.json", [to_dict(p) for p in verified_pools])
    typer.echo("  {} verified / {} total".format(verified_count, len(verified_pools)))

    if verified_count == 0:
        typer.echo("No verified pools found. Cannot proceed with analysis.")
        raise typer.Exit(1)

    # Step 4: Event indexing
    typer.echo("[4/10] Indexing events (this may take a while) ...")
    indexed = index_events(
        w3,
        verified_pools,
        target_token,
        from_block,
        to_block,
        output_dir=output_dir,
        index_token_transfer=not fast_mode,
    )
    swaps = indexed["swaps"]
    liquidity_events = indexed["liquidity_events"]
    transfers = indexed["transfers"]

    typer.echo("  {} swaps, {} liquidity events, {} transfers".format(
        len(swaps), len(liquidity_events), len(transfers)
    ))

    # Load events_all for analysis
    events_all_path = out / "events_all.json"
    if events_all_path.exists():
        with open(events_all_path) as f:
            events_all = json.load(f)
    else:
        events_all = swaps + liquidity_events + transfers

    # Step 5: Position analysis
    typer.echo("[5/10] Analyzing positions ...")
    positions, pos_summary = analyze_positions(
        w3, verified_pools, events_all, target_token,
        from_block, to_block, output_dir=output_dir,
    )
    typer.echo("  {} position(s), {} unique holder(s)".format(
        len(positions), pos_summary.get("total_unique_holders", 0)
    ))

    # Step 6: Address labeling
    typer.echo("[6/10] Labeling addresses ...")
    # Try to find deployer
    deployer = None
    try:
        deployer_addr = w3.eth.get_transaction_count(
            w3.to_checksum_address(target_token)
        )
        # A simpler approach: check if we can get the creation tx from the first transfer
    except Exception:
        pass

    labels = analyze_labels(
        target_token, verified_pools, positions,
        swaps, liquidity_events, transfers,
        deployer=deployer, output_dir=output_dir,
    )
    typer.echo("  {} label(s) assigned".format(len(labels)))

    # Step 7: Metrics calculation
    typer.echo("[7/10] Calculating metrics ...")
    token_decimals = profile.decimals or 18
    metrics = calculate_all_metrics(
        verified_pools, events_all, liquidity_events,
        positions, target_token, token_decimals,
        incident_block=incident_block, output_dir=output_dir,
    )
    typer.echo("  TVL timeline: {} points".format(metrics.get("tvl_timeline_length", 0)))
    pool_conc = metrics.get("pool_concentration", {})
    typer.echo("  Main pool share: {:.2%}".format(pool_conc.get("main_pool_share", 0)))

    # Step 8: Timeline analysis
    typer.echo("[8/10] Building timeline ...")
    timeline = analyze_timeline(
        events_all, swaps, liquidity_events, transfers,
        verified_pools, target_token,
        incident_block=incident_block, output_dir=output_dir,
    )
    typer.echo("  {} total events in timeline".format(timeline.get("total_events", 0)))

    # Step 9: Risk assessment
    typer.echo("[9/10] Computing risk score ...")
    risk = compute_risk(
        pool_concentration=metrics.get("pool_concentration", {}),
        lp_concentration=metrics.get("lp_concentration", {}),
        withdrawal_severity=metrics.get("withdrawal_severity", {}),
        timeline=metrics.get("tvl_timeline", []),
        labels=[to_dict(l) for l in labels],
        deployer=deployer,
        incident_block=incident_block,
        migration=timeline.get("liquidity_migration"),
        output_dir=output_dir,
    )
    typer.echo("  Risk score: {:.4f} ({})".format(
        risk.get("final_score", 0), risk.get("risk_level", "N/A")
    ))

    # Step 10: Report generation
    typer.echo("[10/10] Generating report ...")
    report = generate_report(
        token_profile=profile.__dict__,
        verified_pools=[to_dict(p) for p in verified_pools],
        events_swaps=swaps,
        events_liquidity=liquidity_events,
        events_transfers=transfers,
        positions=[to_dict(p) for p in positions],
        address_labels=[to_dict(l) for l in labels],
        metrics=metrics,
        timeline=timeline,
        risk_assessment=risk,
        incident_block=incident_block,
        output_dir=output_dir,
    )
    typer.echo("  report.md written")

    # Summary
    typer.echo("\n=== Analysis Complete ===")
    typer.echo("Risk Score: {:.4f} / 1.00 ({})".format(
        risk.get("final_score", 0), risk.get("risk_level", "N/A")
    ))
    typer.echo("Output directory: {}".format(out.resolve()))


@app.command()
def discover_only(
    token_address: str = typer.Argument(..., help="Token contract address"),
    from_block: int = typer.Option(19000000, help="Start block"),
    to_block: int = typer.Option(19100000, help="End block"),
    rpc_url: str = typer.Option("", envvar="ETH_RPC_URL", help="RPC URL"),
    output_dir: str = typer.Option("output", help="Output directory"),
):
    """Discover and verify pools for a token."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    w3 = get_web3(rpc_url or None)
    registry = load_registry()
    chain_id_val = get_chain_id(registry)

    profile = profile_token(w3, token_address, chain_id_val)
    _write_json(out / "token_profile.json", profile.__dict__)

    result = discover_pools(w3, token_address, from_block, to_block, chain_id_val)
    _write_json(out / "pool_candidates.json", result)
    typer.echo("Found {} candidate(s)".format(len(result["pools"])))

    candidates = [VerifiedPool(**dict(pdata)) for pdata in result["pools"]]
    verified_pools = verify_pools(
        w3, candidates, target_token=token_address,
        from_block=from_block, to_block=to_block,
    )
    _write_json(out / "verified_pools.json", [to_dict(p) for p in verified_pools])

    for p in verified_pools:
        status = "OK" if p.verified else "FAIL"
        typer.echo("{} {} (conf={})".format(status, p.pool_address, p.verification_confidence))


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


if __name__ == "__main__":
    app()

