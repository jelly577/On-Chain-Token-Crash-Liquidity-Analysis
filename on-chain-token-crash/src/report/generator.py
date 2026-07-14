"""Report generator — produces an explainable Markdown risk report from all analysis outputs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _ts_to_str(timestamp: int) -> str:
    if timestamp == 0:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(timestamp)


def _addr_link(addr: str) -> str:
    short = addr[:10] + "..." + addr[-6:]
    return "[{}](https://etherscan.io/address/{})".format(short, addr)


def _format_amount(amount_str: str) -> str:
    try:
        val = int(amount_str)
        if val >= 10 ** 18:
            return "{:.4f}".format(val / 10 ** 18)
        elif val >= 10 ** 6:
            return "{:.2f}".format(val / 10 ** 6)
        return str(val)
    except (ValueError, TypeError):
        return str(amount_str)


def generate_report(
    token_profile: dict,
    verified_pools: list[dict],
    events_swaps: list[dict],
    events_liquidity: list[dict],
    events_transfers: list[dict],
    positions: list[dict],
    address_labels: list[dict],
    metrics: dict,
    timeline: dict,
    risk_assessment: dict,
    incident_block: int = 0,
    output_dir: str | Path = "output",
) -> str:
    """Generate the complete Markdown report."""
    out = Path(output_dir)
    lines: list[str] = []

    # Header
    token_symbol = token_profile.get("symbol", "???")
    token_addr = token_profile.get("address", "???")
    lines.append("# On-Chain Token Crash & Liquidity Risk Report")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append("- **Token:** {} ({})".format(token_symbol, _addr_link(token_addr)))
    lines.append("- **Chain:** Ethereum (Chain ID: {})".format(token_profile.get("chain_id", 1)))
    lines.append("- **Analysis Window:** Block {} to {}".format(
        timeline.get("block_range", {}).get("first_block", "N/A"),
        timeline.get("block_range", {}).get("last_block", "N/A"),
    ))
    lines.append("- **Incident Block:** {}".format(incident_block if incident_block else "Not specified"))
    lines.append("- **Report Generated:** {}".format(
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ))
    lines.append("")

    # Risk score section
    _add_risk_section(lines, risk_assessment)
    lines.append("")

    # Token profile
    _add_token_profile(lines, token_profile)
    lines.append("")

    # Pool summary
    _add_pool_summary(lines, verified_pools)
    lines.append("")

    # Related addresses
    _add_address_table(lines, address_labels, verified_pools)
    lines.append("")

    # TVL and price timeline
    _add_tvl_timeline(lines, metrics.get("pool_concentration", {}), verified_pools)
    lines.append("")

    # Liquidity events
    _add_liquidity_events(lines, events_liquidity)
    lines.append("")

    # LP concentration
    _add_lp_concentration(lines, metrics.get("lp_concentration", {}), positions)
    lines.append("")

    # Withdrawal severity
    _add_withdrawal_severity(lines, metrics.get("withdrawal_severity", {}))
    lines.append("")

    # Incident timeline
    _add_incident_timeline(lines, timeline, incident_block)
    lines.append("")

    # Risk features
    _add_risk_features(lines, risk_assessment)
    lines.append("")

    # Limitations
    _add_limitations(lines)
    lines.append("")

    # Data sources
    _add_data_sources(lines)
    lines.append("")

    report_text = "\n".join(lines)

    with open(out / "report.md", "w") as f:
        f.write(report_text)

    return report_text


def _add_risk_section(lines: list[str], risk: dict):
    lines.append("### Risk Score")
    lines.append("")
    final_score = risk.get("final_score", 0)
    risk_level = risk.get("risk_level", "UNKNOWN")
    confidence = risk.get("evidence_confidence", 0)

    # Visual bar
    bar_len = 20
    filled = int(final_score * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| **Final Risk Score** | **{} / 1.00** |".format(round(final_score, 4)))
    lines.append("| **Risk Level** | **{}** |".format(risk_level))
    lines.append("| Evidence Confidence | {:.2%} |".format(confidence))
    lines.append("| Visual | `{}` |".format(bar))
    if risk.get("migration_adjusted"):
        lines.append("| Migration Adjustment | {} |".format(risk.get("migration_note", "")))
    lines.append("")


def _add_token_profile(lines: list[str], profile: dict):
    lines.append("## Token Profile")
    lines.append("")
    lines.append("| Property | Value |")
    lines.append("|----------|-------|")
    lines.append("| Address | {} |".format(_addr_link(profile.get("address", ""))))
    lines.append("| Symbol | {} |".format(profile.get("symbol", "N/A")))
    lines.append("| Name | {} |".format(profile.get("name", "N/A")))
    lines.append("| Decimals | {} |".format(profile.get("decimals", "N/A")))
    lines.append("| Total Supply | {} |".format(_format_amount(str(profile.get("total_supply", "N/A")))))
    lines.append("| Is Contract | {} |".format(profile.get("is_contract", False)))
    lines.append("| Proxy Address | {} |".format(profile.get("proxy_address", "None") or "None"))
    lines.append("| Implementation | {} |".format(profile.get("implementation_address", "None") or "None"))
    flags = profile.get("behavior_flags", [])
    lines.append("| Behavior Flags | {} |".format(", ".join(flags) if flags else "None"))
    lines.append("")


def _add_pool_summary(lines: list[str], pools: list[dict]):
    lines.append("## Pool Summary")
    lines.append("")
    verified = [p for p in pools if p.get("verified")]
    unverified = [p for p in pools if not p.get("verified")]

    lines.append("**{}** verified pool(s), **{}** unverified candidate(s).".format(
        len(verified), len(unverified)
    ))
    lines.append("")

    if verified:
        lines.append("| Pool Address | Protocol | Version | Token0 | Token1 | Fee | Confidence |")
        lines.append("|-------------|----------|---------|--------|--------|-----|------------|")
        for p in verified:
            lines.append("| {} | {} | {} | {} | {} | {} | {:.2%} |".format(
                _addr_link(p.get("pool_address", "")),
                p.get("protocol", ""),
                p.get("version", ""),
                p.get("token0", "")[:10] + "..." if p.get("token0") else "N/A",
                p.get("token1", "")[:10] + "..." if p.get("token1") else "N/A",
                p.get("fee", "N/A"),
                p.get("verification_confidence", 0),
            ))
    lines.append("")


def _add_address_table(lines: list[str], labels: list[dict], pools: list[dict]):
    lines.append("## Related Addresses")
    lines.append("")
    if labels:
        lines.append("| Address | Label | Category | Confidence |")
        lines.append("|---------|-------|----------|------------|")
        for l in labels:
            addr = l.get("address", "")
            lines.append("| {} | {} | {} | {:.0%} |".format(
                _addr_link(addr),
                l.get("label", ""),
                l.get("category", ""),
                l.get("confidence", 0),
            ))
    lines.append("")


def _add_tvl_timeline(lines: list[str], pool_conc: dict, pools: list[dict]):
    lines.append("## TVL & Price History")
    lines.append("")
    total_tvl = pool_conc.get("total_tvl", 0)
    main_pool = pool_conc.get("main_pool", "")
    main_pool_share = pool_conc.get("main_pool_share", 0)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| Total TVL (in token units) | {} |".format(_format_amount(str(total_tvl))))
    lines.append("| Active Pools | {} |".format(pool_conc.get("active_pools", 0)))
    if main_pool:
        lines.append("| Main Pool | {} |".format(_addr_link(main_pool)))
        lines.append("| Main Pool Share | {:.2%} |".format(main_pool_share))
    lines.append("")


def _add_liquidity_events(lines: list[str], events: list[dict]):
    lines.append("## Liquidity Events")
    lines.append("")
    adds = [e for e in events if e.get("event_type") == "LIQUIDITY_ADD"]
    removes = [e for e in events if e.get("event_type") == "LIQUIDITY_REMOVE"]

    lines.append("- **Liquidity Additions:** {} events".format(len(adds)))
    lines.append("- **Liquidity Removals:** {} events".format(len(removes)))
    lines.append("")

    # Show significant events
    significant = [e for e in removes if abs(int(e.get("token0_amount", "0"))) > 10 ** 18]
    if significant:
        lines.append("### Significant Liquidity Removals")
        lines.append("")
        lines.append("| Block | Timestamp | Pool | Actor | Amount0 | Amount1 |")
        lines.append("|-------|-----------|------|-------|---------|---------|")
        for e in significant[:20]:
            lines.append("| {} | {} | {} | {} | {} | {} |".format(
                e.get("block_number", ""),
                _ts_to_str(e.get("block_timestamp", 0)),
                _addr_link(e.get("pool_address", "")),
                _addr_link(e.get("actor", "N/A")),
                _format_amount(e.get("token0_amount", "0")),
                _format_amount(e.get("token1_amount", "0")),
            ))
        lines.append("")


def _add_lp_concentration(lines: list[str], lp_conc: dict, positions: list[dict]):
    lines.append("## LP Concentration")
    lines.append("")
    top_lp_share = lp_conc.get("top_lp_share", 0)
    top_n_share = lp_conc.get("top_5_share", 0)
    total_lps = lp_conc.get("num_lps", 0)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| Total LP Positions | {} |".format(lp_conc.get("total_lp_positions", 0)))
    lines.append("| Unique LPs | {} |".format(total_lps))
    lines.append("| Top LP Share | {:.2f}% |".format(top_lp_share))
    lines.append("| Top 5 LP Share | {:.2f}% |".format(top_n_share))
    lines.append("")

    # Show top holders
    if positions:
        lines.append("### Top LP Holders")
        lines.append("")
        lines.append("| Owner | Share % | Pool | Type |")
        lines.append("|-------|---------|------|------|")
        sorted_pos = sorted(positions, key=lambda p: p.get("share_pct", 0), reverse=True)
        for p in sorted_pos[:10]:
            nft_id = p.get("nft_token_id")
            pos_type = "V3 NFT #{}".format(nft_id) if nft_id else "V2 LP Token"
            lines.append("| {} | {:.4f}% | {} | {} |".format(
                _addr_link(p.get("owner", "")),
                p.get("share_pct", 0),
                _addr_link(p.get("pool_address", "")),
                pos_type,
            ))
        lines.append("")


def _add_withdrawal_severity(lines: list[str], severity: dict):
    lines.append("## Withdrawal Analysis")
    lines.append("")
    num_withdrawals = severity.get("num_withdrawals", 0)
    severity_pct = severity.get("withdrawal_severity", 0)
    total_removed = severity.get("total_removed_token0", 0)
    pre_event_tvl = severity.get("pre_event_tvl", 0)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| Pre-Crash Withdrawals | {} |".format(num_withdrawals))
    lines.append("| Total Removed (token0) | {} |".format(_format_amount(str(total_removed))))
    lines.append("| Pre-Event TVL | {} |".format(_format_amount(str(pre_event_tvl))))
    lines.append("| Withdrawal Severity | {:.2%} of pre-event TVL |".format(severity_pct))
    lines.append("")


def _add_incident_timeline(lines: list[str], timeline: dict, incident_block: int):
    lines.append("## Incident Timeline")
    lines.append("")
    total_events = timeline.get("total_events", 0)
    total_swaps = timeline.get("total_swaps", 0)
    total_liq = timeline.get("total_liquidity_events", 0)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| Total Events | {} |".format(total_events))
    lines.append("| Swaps | {} |".format(total_swaps))
    lines.append("| Liquidity Events | {} |".format(total_liq))
    br = timeline.get("block_range", {})
    lines.append("| Block Range | {} → {} |".format(br.get("first_block", "N/A"), br.get("last_block", "N/A")))
    lines.append("| Time Range | {} → {} |".format(
        _ts_to_str(br.get("first_timestamp", 0)),
        _ts_to_str(br.get("last_timestamp", 0)),
    ))
    lines.append("")

    # Migration check
    migration = timeline.get("liquidity_migration", {})
    if migration.get("migration_detected"):
        lines.append("### Liquidity Migration Detected")
        lines.append("")
        lines.append("The following migration candidates were found:")
        for mc in migration.get("migration_candidates", []):
            lines.append("- From {} (block {}) to {} (block {})".format(
                _addr_link(mc.get("remove_pool", "")),
                mc.get("remove_block", ""),
                _addr_link(mc.get("add_pool", "")),
                mc.get("add_block", ""),
            ))
        lines.append("")

    # Alternative causes
    alt = timeline.get("alternative_causes", {})
    if alt:
        lines.append("### Alternative Cause Check")
        lines.append("")
        if alt.get("large_token_distributions"):
            lines.append("- Large token distributions detected — possible airdrop or coordinated sell.")
        lines.append("")

    # Key events around incident
    sorted_events = timeline.get("sorted_events", [])
    if sorted_events:
        lines.append("### Key Events by Block")
        lines.append("")
        lines.append("| Block | Timestamp | Event | Pool | Actor | Detail |")
        lines.append("|-------|-----------|-------|------|-------|--------|")
        # Sample events (show last 30 if no incident block, or events around incident)
        if incident_block:
            incident_events = [
                e for e in sorted_events
                if abs(e.get("block_number", 0) - incident_block) <= 100
            ]
            display_events = incident_events[:30]
        else:
            display_events = sorted_events[-30:]

        for e in display_events:
            etype = e.get("event_type", "")
            source = e.get("source_event", "")
            label = "{} ({})".format(etype, source) if source else etype
            detail = ""
            if etype == "SWAP":
                detail = "Amount0: {}".format(_format_amount(e.get("token0_amount", "0")))
            elif "LIQUIDITY" in etype:
                detail = "Δ: {} / {}".format(
                    _format_amount(e.get("token0_amount", "0")),
                    _format_amount(e.get("token1_amount", "0")),
                )
            elif etype == "TOKEN_TRANSFER":
                detail = "Value: {}".format(_format_amount(e.get("token0_amount", "0")))
            lines.append("| {} | {} | {} | {} | {} | {} |".format(
                e.get("block_number", ""),
                _ts_to_str(e.get("block_timestamp", 0)),
                label,
                _addr_link(e.get("pool_address", "") or "N/A"),
                _addr_link(e.get("actor", "N/A")),
                detail,
            ))
        lines.append("")


def _add_risk_features(lines: list[str], risk: dict):
    lines.append("## Risk Feature Breakdown")
    lines.append("")
    features = risk.get("features", {})
    if features:
        lines.append("| Feature | Value | Weight | Contribution | Description |")
        lines.append("|---------|-------|--------|-------------|-------------|")
        total_contrib = 0
        for name, feat in features.items():
            weight = feat.get("weight", 0)
            value = feat.get("value", 0)
            contrib = round(weight * value, 4)
            total_contrib += contrib
            lines.append("| {} | {:.4f} | {:.2f} | {:.4f} | {} |".format(
                name.replace("_", " ").title(),
                value,
                weight,
                contrib,
                feat.get("description", ""),
            ))
        lines.append("| **Raw Score** | | | **{:.4f}** | |".format(total_contrib))
        lines.append("")

    lines.append("### Interpretation")
    lines.append("")
    risk_level = risk.get("risk_level", "UNKNOWN")
    if risk_level == "HIGH":
        lines.append("The evidence is **consistent with a liquidity-driven crash**. Withdrawal timing,")
        lines.append("concentration, and market impact suggest liquidity removal likely contributed to the crash.")
        lines.append("Examine the specific withdrawal events and associated actors for further verification.")
    elif risk_level == "MEDIUM":
        lines.append("Some risk indicators are present, but the evidence is not conclusive.")
        lines.append("Additional investigation into specific withdrawal patterns and address relationships")
        lines.append("is recommended before drawing firm conclusions.")
    else:
        lines.append("The available evidence suggests **low risk** of a liquidity-attributable crash.")
        lines.append("The market impact may be driven by normal trading activity or external factors.")
    lines.append("")


def _add_limitations(lines: list[str]):
    lines.append("## Limitations & Caveats")
    lines.append("")
    lines.append("1. **TVL estimates** for V3 Uniswap pools are approximate — actual liquidity is range-dependent.")
    lines.append("2. **Price estimates** use simple AMM formulas and may not reflect actual trade prices.")
    lines.append("3. **LP ownership** for V2 is reconstructed from Transfer events and may miss complex delegation patterns.")
    lines.append("4. **V3 position analysis** is limited to visible PositionManager events.")
    lines.append("5. **Alternative causes** (e.g., broader market events, exploits) are not exhaustively checked.")
    lines.append("6. **Confidence scores** reflect data quality and completeness, not certainty of malicious intent.")
    lines.append("7. A **high risk score indicates correlation, not causation** — always verify with independent data.")
    lines.append("")
    lines.append("> **Important:** This report is for informational purposes. It does not constitute financial advice.")
    lines.append("")


def _add_data_sources(lines: list[str]):
    lines.append("## Data Sources & Methodology")
    lines.append("")
    lines.append("- **RPC Provider:** Ethereum mainnet via configured ETH_RPC_URL")
    lines.append("- **Protocol Whitelist:** `config/protocols.ethereum.yaml`")
    lines.append("- **Pool Discovery:** Factory getPair/getPool + event logs (PairCreated, PoolCreated)")
    lines.append("- **Pool Verification:** On-chain factory, token pair, and event provenance checks")
    lines.append("- **Event Indexing:** Chunked log queries with checkpoint/resume support")
    lines.append("- **Position Reconstruction:** V2 LP-Transfer events; V3 PositionManager NFT ownership")
    lines.append("- **Risk Model:** Weighted feature combination with migration adjustment")
    lines.append("")
    lines.append("### Output Files")
    lines.append("")
    lines.append("| File | Description |")
    lines.append("|------|-------------|")
    lines.append("| `token_profile.json` | Token metadata and behavior flags |")
    lines.append("| `pool_candidates.json` | Raw pool discovery results |")
    lines.append("| `verified_pools.json` | Verified pool addresses with confidence |")
    lines.append("| `swaps.json` | Normalized swap events |")
    lines.append("| `liquidity_events.json` | Normalized liquidity change events |")
    lines.append("| `events_all.json` | All indexed events (combined) |")
    lines.append("| `positions.json` | LP position ownership |")
    lines.append("| `address_labels.json` | Address role annotations |")
    lines.append("| `metrics.json` | TVL, concentration, and withdrawal metrics |")
    lines.append("| `incident_timeline.json` | Chronological event timeline |")
    lines.append("| `risk_assessment.json` | Explainable risk score |")
    lines.append("| `report.md` | This report |")
    lines.append("")

