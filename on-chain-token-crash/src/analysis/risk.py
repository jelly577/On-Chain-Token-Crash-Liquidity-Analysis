"""Explainable risk score — combines pool concentration, LP concentration, withdrawal severity,
temporal proximity, role sensitivity, and market impact into a confidence-qualified score."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def calculate_temporal_proximity(
    withdrawal_events: list[dict],
    incident_block: int,
    block_timestamps: dict[int, int],
) -> tuple[float, str]:
    """Calculate the temporal proximity of withdrawals to the crash.

    Returns (score, explanation).
    """
    if not withdrawal_events or incident_block == 0:
        return 0.0, "No incident block or no withdrawals to evaluate."

    # Find the closest withdrawal before the crash
    closest_distance = float("inf")
    for evt in withdrawal_events:
        block = evt.get("block_number", 0)
        if block <= incident_block:
            distance = incident_block - block
            # Rough time estimate: ~12s per block on Ethereum
            time_distance_sec = distance * 12
            if time_distance_sec < closest_distance:
                closest_distance = time_distance_sec

    if closest_distance == float("inf"):
        return 0.0, "No pre-crash withdrawals found."

    # Score: closer = higher risk
    # < 1 hour: 1.0, < 6 hours: 0.8, < 24 hours: 0.5, < 7 days: 0.3, else: 0.1
    if closest_distance < 3600:
        score = 1.0
        desc = "Withdrawal within 1 hour of crash"
    elif closest_distance < 21600:
        score = 0.8
        desc = "Withdrawal within 6 hours of crash"
    elif closest_distance < 86400:
        score = 0.5
        desc = "Withdrawal within 24 hours of crash"
    elif closest_distance < 604800:
        score = 0.3
        desc = "Withdrawal within 7 days of crash"
    else:
        score = 0.1
        desc = "Withdrawal more than 7 days before crash"

    return score, desc


def calculate_role_sensitivity(
    labels: list[dict],
    deployer: Optional[str] = None,
) -> tuple[float, str]:
    """Check if the deployer or associated addresses are involved in withdrawals/sells.

    Returns (score, explanation).
    """
    if not deployer:
        return 0.0, "Deployer unknown — cannot assess role sensitivity."

    # Find deployer-related labels
    deployer_labels = [
        l for l in labels
        if l.get("address", "").lower() == deployer.lower()
    ]
    co_lp_labels = [
        l for l in labels
        if "Co-LP" in l.get("label", "")
    ]

    if deployer_labels:
        # Deployer is directly involved
        return 0.8, "Deployer is directly involved in pool(s)."

    if co_lp_labels:
        return 0.6, "Deployer-associated addresses are LPs."

    return 0.3, "No direct deployer-LP overlap detected."


def calculate_market_impact(
    timeline: list[dict],
    incident_block: int,
) -> tuple[float, str]:
    """Estimate market impact from price and TVL changes around the crash.

    Returns (score, explanation).
    """
    if not timeline or incident_block == 0:
        return 0.0, "No timeline data or incident block."

    # Get price before and after
    pre_prices = [
        e for e in timeline
        if e.get("block_number", 0) <= incident_block and e.get("price", 0) > 0
    ]
    post_prices = [
        e for e in timeline
        if e.get("block_number", 0) > incident_block and e.get("price", 0) > 0
    ]

    if pre_prices:
        avg_pre_price = sum(e["price"] for e in pre_prices) / len(pre_prices)
    else:
        avg_pre_price = 0.0

    if post_prices:
        avg_post_price = sum(e["price"] for e in post_prices) / len(post_prices)
    else:
        avg_post_price = 0.0

    if avg_pre_price > 0 and avg_post_price > 0:
        price_drop = (avg_pre_price - avg_post_price) / avg_pre_price
    else:
        price_drop = 0.0

    score = min(price_drop, 1.0)  # cap at 1.0 (100% drop)
    desc = "Price change: {:.2%} drop".format(price_drop) if price_drop > 0 else "No significant price change detected."

    return score, desc


def calculate_risk_score(
    pool_concentration: dict[str, Any],
    lp_concentration: dict[str, Any],
    withdrawal_severity: dict[str, Any],
    withdrawal_events: list[dict],
    timeline: list[dict],
    labels: list[dict],
    deployer: Optional[str] = None,
    incident_block: int = 0,
    migration: Optional[dict] = None,
) -> dict[str, Any]:
    """Calculate the explainable risk score from all available features.

    Formula:
        raw_score = 0.15 * pool_concentration
                  + 0.15 * lp_concentration
                  + 0.20 * withdrawal_severity
                  + 0.15 * temporal_proximity
                  + 0.15 * role_sensitivity
                  + 0.15 * market_impact
                  + 0.05 * combined_activity

        final_score = clamp(raw_score - migration_adjustment, 0, 1) * evidence_confidence
    """
    # Feature 1: Pool concentration (main pool share)
    pool_conc = pool_concentration.get("main_pool_share", 0)
    pool_conc_feature = min(pool_conc, 1.0)
    pool_conc_desc = "Main pool holds {:.2%} of total DEX liquidity.".format(pool_conc)

    # Feature 2: LP concentration (top LP share)
    lp_conc = lp_concentration.get("top_lp_share", 0)
    lp_conc_feature = min(lp_conc / 100, 1.0)
    lp_conc_desc = "Largest LP holds {:.2f}% of pool shares.".format(lp_conc)

    # Feature 3: Withdrawal severity
    severity = withdrawal_severity.get("withdrawal_severity", 0)
    sev_feature = min(severity, 1.0)
    sev_desc = "Liquidity removed before crash is {:.2%} of pre-event TVL.".format(severity)

    # Feature 4: Temporal proximity
    prox_score, prox_desc = calculate_temporal_proximity(
        withdrawal_events or withdrawal_severity.get("withdrawal_events", []),
        incident_block,
        {},
    )

    # Feature 5: Role sensitivity
    role_score, role_desc = calculate_role_sensitivity(labels, deployer)

    # Feature 6: Market impact
    market_score, market_desc = calculate_market_impact(timeline, incident_block)

    # Feature 7: Combined activity
    num_withdrawals = withdrawal_severity.get("num_withdrawals", 0)
    has_large_sells = any(
        abs(int(e.get("token0_amount", "0"))) > 10 ** 22
        for e in withdrawal_events
    )
    combined_act = 1.0 if (num_withdrawals > 3 and has_large_sells) else (
        0.5 if (num_withdrawals > 1 or has_large_sells) else 0.0
    )
    combined_desc = "Suspicious activity: {} withdrawals{}".format(
        num_withdrawals,
        " and large sells detected." if has_large_sells else "."
    )

    # Raw score
    raw_score = (
        0.15 * pool_conc_feature
        + 0.15 * lp_conc_feature
        + 0.20 * sev_feature
        + 0.15 * prox_score
        + 0.15 * role_score
        + 0.15 * market_score
        + 0.05 * combined_act
    )

    # Migration adjustment
    migration_adjustment = 0.0
    migration_note = ""
    if migration and migration.get("migration_detected"):
        migration_adjustment = 0.3
        migration_note = "Liquidity migration detected — reducing risk by 0.30."

    # Evidence confidence
    evidence_count = sum(1 for v in [
        pool_conc > 0,
        lp_conc > 0,
        sev_feature > 0,
        prox_score > 0,
        market_score > 0,
    ] if v)
    evidence_confidence = min(0.5 + (evidence_count * 0.1), 1.0)

    final_score = max(0, min(raw_score - migration_adjustment, 1.0)) * evidence_confidence

    # Risk level
    if final_score >= 0.7:
        risk_level = "HIGH"
    elif final_score >= 0.4:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "final_score": round(final_score, 4),
        "risk_level": risk_level,
        "raw_score": round(raw_score, 4),
        "evidence_confidence": round(evidence_confidence, 4),
        "migration_adjusted": migration_adjustment > 0,
        "migration_note": migration_note,
        "features": {
            "pool_concentration": {
                "value": round(pool_conc_feature, 4),
                "weight": 0.15,
                "description": pool_conc_desc,
                "raw_data": pool_conc,
            },
            "lp_concentration": {
                "value": round(lp_conc_feature, 4),
                "weight": 0.15,
                "description": lp_conc_desc,
                "raw_data": lp_conc,
            },
            "withdrawal_severity": {
                "value": round(sev_feature, 4),
                "weight": 0.20,
                "description": sev_desc,
                "raw_data": severity,
            },
            "temporal_proximity": {
                "value": round(prox_score, 4),
                "weight": 0.15,
                "description": prox_desc,
            },
            "role_sensitivity": {
                "value": round(role_score, 4),
                "weight": 0.15,
                "description": role_desc,
            },
            "market_impact": {
                "value": round(market_score, 4),
                "weight": 0.15,
                "description": market_desc,
            },
            "combined_activity": {
                "value": round(combined_act, 4),
                "weight": 0.05,
                "description": combined_desc,
            },
        },
    }


def compute_risk(
    pool_concentration: dict,
    lp_concentration: dict,
    withdrawal_severity: dict,
    timeline: list[dict],
    labels: list[dict],
    deployer: Optional[str] = None,
    incident_block: int = 0,
    migration: Optional[dict] = None,
    output_dir: str | Path = "output",
) -> dict[str, Any]:
    """Main entry point: compute explainable risk score."""
    out = Path(output_dir)

    risk_result = calculate_risk_score(
        pool_concentration=pool_concentration,
        lp_concentration=lp_concentration,
        withdrawal_severity=withdrawal_severity,
        withdrawal_events=withdrawal_severity.get("withdrawal_events", []),
        timeline=timeline,
        labels=labels,
        deployer=deployer,
        incident_block=incident_block,
        migration=migration,
    )
    _write_json(out / "risk_assessment.json", risk_result)

    return risk_result


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

