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
from .token.resolver import TokenResolveError, format_resolve_summary, resolve_token
from .registry.loader import load_registry, get_chain_id
from .verification.verifier import verify_pools
from .indexer.indexer import index_events
from .analysis.positions import analyze_positions
from .analysis.labels import analyze_labels
from .analysis.metrics import calculate_all_metrics
from .analysis.timeline import analyze_timeline
from .analysis.risk import compute_risk
from .report.generator import generate_report
from .analysis.holdings import analyze_holdings
from .analysis.dashboard import generate_dashboard

app = typer.Typer()


def _resolve_or_exit(token_query: str, chain_id: int, pick: int) -> str:
    """Resolve address/symbol/name → checksum address, or exit with a clear error."""
    try:
        resolved = resolve_token(token_query, chain_id=chain_id, pick=pick)
    except TokenResolveError as exc:
        typer.echo("Token resolve failed: {}".format(exc), err=True)
        raise typer.Exit(1)
    typer.echo(format_resolve_summary(resolved))
    return resolved["address"]


@app.command()
def analyze(
    token: str = typer.Argument(
        ..., help="Token contract address, symbol, or name (e.g. 0x… / USDC / CREDI)"
    ),
    chain_id: int = typer.Option(1, help="Chain ID"),
    from_block: int = typer.Option(19000000, help="Start block"),
    to_block: int = typer.Option(19100000, help="End block"),
    incident_block: int = typer.Option(0, help="Block number of the crash incident (optional)"),
    rpc_url: str = typer.Option("", envvar="ETH_RPC_URL", help="RPC URL"),
    output_dir: str = typer.Option("output", help="Output directory"),
    fast_mode: bool = typer.Option(False, help="Skip exhaustive event indexing (faster, less data)"),
    pick: int = typer.Option(0, help="When name matches multiple tokens, pick candidate index"),
):
    """End-to-end analysis: token → liquidity report + dashboard.

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
      11. Holdings analysis
      12. Dashboard generation
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    token_address = _resolve_or_exit(token, chain_id, pick)

    w3 = get_web3(rpc_url or None)
    registry = load_registry()
    chain_id_val = get_chain_id(registry)

    # Step 1: Token profile
    typer.echo("[1/12] Profiling token ...")
    profile = profile_token(w3, token_address, chain_id_val)
    _write_json(out / "token_profile.json", profile.__dict__)
    typer.echo("  Symbol: {}, Decimals: {}".format(profile.symbol, profile.decimals))
    target_token = profile.address

    # Step 2: Discover pools
    typer.echo("[2/12] Discovering pools ...")
    result = discover_pools(w3, token_address, from_block, to_block, chain_id_val)
    _write_json(out / "pool_candidates.json", result)
    typer.echo("  Found {} candidate(s)".format(len(result["pools"])))

    # Step 3: Verify pools
    typer.echo("[3/12] Verifying pools ...")
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
    typer.echo("[4/12] Indexing events (chunk-level resume enabled; Ctrl+C is safe) ...")
    typer.echo("  Progress: {}/indexer_cache + event_indexer_checkpoint.json".format(output_dir))
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
    typer.echo("[5/12] Analyzing positions ...")
    positions, pos_summary = analyze_positions(
        w3, verified_pools, events_all, target_token,
        from_block, to_block, output_dir=output_dir,
    )
    typer.echo("  {} position(s), {} unique holder(s)".format(
        len(positions), pos_summary.get("total_unique_holders", 0)
    ))

    # Step 6: Address labeling
    typer.echo("[6/12] Labeling addresses ...")
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
    typer.echo("[7/12] Calculating metrics ...")
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
    typer.echo("[8/12] Building timeline ...")
    timeline = analyze_timeline(
        events_all, swaps, liquidity_events, transfers,
        verified_pools, target_token,
        incident_block=incident_block, output_dir=output_dir,
    )
    typer.echo("  {} total events in timeline".format(timeline.get("total_events", 0)))

    # Step 9: Risk assessment
    typer.echo("[9/12] Computing risk score ...")
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
    typer.echo("[10/12] Generating report ...")
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

    # Step 11: Holdings
    typer.echo("[11/12] Analyzing holdings ...")
    if fast_mode and not transfers:
        typer.echo("  Skipping balance queries (--fast-mode with no transfers)")
        holdings_result = {
            "holdings": [],
            "pool_identification": [
                {
                    "pool_address": p.pool_address,
                    "protocol": p.protocol,
                    "version": p.version,
                    "token0": p.token0,
                    "token1": p.token1,
                    "in_holders_list": False,
                }
                for p in verified_pools if p.verified
            ],
            "holdings_count": 0,
            "total_unique_addresses": 0,
            "query_time_human": "",
        }
        _write_json(out / "holdings.json", holdings_result)
    else:
        holdings_result = analyze_holdings(
            w3, target_token, token_decimals, transfers,
            verified_pools, from_block, to_block,
            output_dir=output_dir,
        )
        typer.echo("  {} unique addresses, {} holders with balance".format(
            holdings_result.get("total_unique_addresses", 0),
            holdings_result.get("holdings_count", 0),
        ))

    # Step 12: Dashboard
    typer.echo("[12/12] Generating dashboard ...")
    dashboard_path = generate_dashboard(output_dir=output_dir)
    typer.echo("  {}".format(dashboard_path))

    # Summary
    typer.echo("\n=== Analysis Complete ===")
    typer.echo("Chain ID: {}  Token: {}".format(chain_id_val, target_token))
    typer.echo("Risk Score: {:.4f} / 1.00 ({})".format(
        risk.get("final_score", 0), risk.get("risk_level", "N/A")
    ))
    typer.echo("Dashboard: {}".format(dashboard_path))
    typer.echo("Output directory: {}".format(out.resolve()))


@app.command()
def discover_only(
    token: str = typer.Argument(
        ..., help="Token contract address, symbol, or name (e.g. 0x… / USDC / CREDI)"
    ),
    from_block: int = typer.Option(19000000, help="Start block"),
    to_block: int = typer.Option(19100000, help="End block"),
    rpc_url: str = typer.Option("", envvar="ETH_RPC_URL", help="RPC URL"),
    output_dir: str = typer.Option("output", help="Output directory"),
    pick: int = typer.Option(0, help="When name matches multiple tokens, pick candidate index"),
    chain_id: int = typer.Option(1, help="Chain ID"),
):
    """Discover and verify pools for a token."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    token_address = _resolve_or_exit(token, chain_id, pick)

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



@app.command()
def holdings(
    token: str = typer.Argument(
        ..., help="Token contract address, symbol, or name (e.g. 0x… / USDC / CREDI)"
    ),
    from_block: int = typer.Option(19000000, help="Start block"),
    to_block: int = typer.Option(19100000, help="End block"),
    rpc_url: str = typer.Option("", envvar="ETH_RPC_URL", help="RPC URL"),
    output_dir: str = typer.Option("output", help="Output directory"),
    pick: int = typer.Option(0, help="When name matches multiple tokens, pick candidate index"),
    chain_id: int = typer.Option(1, help="Chain ID"),
):
    """Step 1-2: Analyze token holdings & identify pool accounts.

    Runs:
      1. Basic Token Holdings Analysis - extracts unique addresses from
         Transfer events and queries their token balances
      2. Pool Account Identification - matches pool addresses among holders
    """
    from .token.profiler import profile_token as _profile
    from .discovery.engine import discover_pools as _discover
    from .verification.verifier import verify_pools as _verify
    from .registry.loader import load_registry as _registry, get_chain_id as _chain_id
    from .indexer.indexer import index_events as _index
    from .models import VerifiedPool as _VP, to_dict as _to_dict

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    token_address = _resolve_or_exit(token, chain_id, pick)

    w3 = get_web3(rpc_url or None)
    registry = _registry()
    chain_id_val = _chain_id(registry)

    typer.echo("[1] Profiling token ...")
    profile = _profile(w3, token_address, chain_id_val)
    _write_json(out / "token_profile.json", profile.__dict__)
    typer.echo("  Symbol: {}, Decimals: {}".format(profile.symbol, profile.decimals))
    target_token = profile.address
    token_decimals = profile.decimals or 18

    typer.echo("[2] Discovering and verifying pools ...")
    result = _discover(w3, token_address, from_block, to_block, chain_id_val)
    candidates = [_VP(**dict(pdata)) for pdata in result["pools"]]
    verified_pools = _verify(
        w3, candidates, target_token=token_address,
        from_block=from_block, to_block=to_block,
    )
    _write_json(out / "verified_pools.json", [_to_dict(p) for p in verified_pools])
    verified_count = sum(1 for p in verified_pools if p.verified)
    typer.echo("  {} verified pools".format(verified_count))
    if verified_count == 0:
        typer.echo("No verified pools found. Cannot proceed.")
        raise typer.Exit(1)

    typer.echo("[3] Indexing token transfer events ...")
    indexed = _index(
        w3, verified_pools, target_token, from_block, to_block,
        output_dir=output_dir, index_token_transfer=True,
    )
    transfers = indexed["transfers"]
    typer.echo("  {} transfer events indexed".format(len(transfers)))

    typer.echo("[4] Running holdings analysis ...")
    holdings_result = analyze_holdings(
        w3, target_token, token_decimals, transfers,
        verified_pools, from_block, to_block,
        output_dir=output_dir,
    )
    typer.echo("  {} unique addresses found, {} holders with balance".format(
        holdings_result["total_unique_addresses"],
        holdings_result["holdings_count"],
    ))
    pool_identified = [p for p in holdings_result["pool_identification"]
                       if p.get("in_holders_list")]
    typer.echo("  {} pool addresses identified in holder list".format(len(pool_identified)))

    typer.echo("\n=== Holdings Analysis Complete ===")
    typer.echo("Output files:")
    typer.echo("  holdings.json        - Full holdings data (JSON)")
    typer.echo("  holdings_table.csv   - Holdings table (CSV)")
    typer.echo("  pool_identification_table.csv - Pool identification table (CSV)")


@app.command()
def dashboard(
    output_dir: str = typer.Option("output", help="Output directory"),
):
    """Step 3: Generate a visual HTML dashboard from analysis results.

    Requires holdings.json, verified_pools.json, and other analysis
    output files to already exist in the output directory.
    """
    dashboard_path = generate_dashboard(output_dir=output_dir)
    typer.echo("Dashboard generated: {}".format(dashboard_path))


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


if __name__ == "__main__":
    app()

