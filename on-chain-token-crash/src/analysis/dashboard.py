"""Dashboard visualization — generates a standalone HTML dashboard.

Reads analysis output files and renders an interactive dashboard using Chart.js.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def generate_dashboard(
    output_dir: str | Path = "output",
) -> str:
    """Generate a standalone HTML dashboard from analysis outputs.

    Returns the path to the generated dashboard.html file.
    """
    out = Path(output_dir)

    holdings = _load_json(out / "holdings.json", {})
    token_profile = _load_json(out / "token_profile.json", {})
    verified_pools = _load_json(out / "verified_pools.json", [])
    metrics = _load_json(out / "metrics.json", {})
    risk = _load_json(out / "risk_assessment.json", {})

    holdings_data = holdings.get("holdings", [])
    pool_ident = holdings.get("pool_identification", [])

    if not pool_ident and verified_pools:
        pool_ident = [
            {
                "pool_address": p.get("pool_address", ""),
                "protocol": p.get("protocol", ""),
                "version": p.get("version", ""),
                "token0": p.get("token0", ""),
                "token1": p.get("token1", ""),
                "in_holders_list": False,
            }
            for p in verified_pools
            if p.get("verified", True)
        ]

    top_holders = [h for h in holdings_data if not h.get("is_pool")][:20]
    pool_holders = [h for h in holdings_data if h.get("is_pool")]

    tvl_data = metrics.get("tvl_timeline", [])
    pool_conc = metrics.get("pool_concentration", {})

    risk_score = risk.get("final_score", 0)
    risk_level = risk.get("risk_level", "N/A")
    symbol = token_profile.get("symbol", "TOKEN")
    chain_id = token_profile.get("chain_id", 1)
    token_addr = token_profile.get("address", "")

    html = _build_html(
        symbol=symbol,
        chain_id=chain_id,
        token_address=token_addr,
        holdings_data=holdings_data,
        top_holders=top_holders,
        pool_holders=pool_holders,
        pool_ident=pool_ident,
        tvl_data=tvl_data,
        pool_conc=pool_conc,
        risk_score=risk_score,
        risk_level=risk_level,
        verified_pools=verified_pools,
        holdings_count=holdings.get("holdings_count", 0),
        total_addresses=holdings.get("total_unique_addresses", 0),
        query_time=holdings.get("query_time_human", ""),
    )

    dashboard_path = out / "dashboard.html"
    with open(dashboard_path, "w") as f:
        f.write(html)

    return str(dashboard_path.resolve())


def _build_html(
    symbol: str,
    chain_id: int,
    token_address: str,
    holdings_data: list,
    top_holders: list,
    pool_holders: list,
    pool_ident: list,
    tvl_data: list,
    pool_conc: dict,
    risk_score: float,
    risk_level: str,
    verified_pools: list,
    holdings_count: int,
    total_addresses: int,
    query_time: str,
) -> str:
    import json as _json
    top_h_json = _json.dumps(top_holders, indent=2)
    pool_h_json = _json.dumps(pool_holders, indent=2)
    pool_i_json = _json.dumps(pool_ident, indent=2)
    risk_lvl_class = risk_level.lower() if risk_level != "N/A" else "medium"
    main_pool_share = pool_conc.get("main_pool_share", 0) * 100
    empty_note = ""
    if holdings_count == 0 and total_addresses == 0:
        empty_note = (
            '<p class="subtitle" style="color:#c62828">'
            "No transfer/holdings data in this block window — "
            "pool list and risk score below still reflect discovery results."
            "</p>"
        )

    table_top = _table_top_holders(top_holders, symbol)
    table_pool = _table_pool_holders(pool_holders, symbol)
    table_ident = _table_pool_ident(pool_ident)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{symbol} Holdings Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f5f7fa;color:#1a1a2e;padding:20px}}
.container{{max-width:1400px;margin:0 auto}}
h1{{font-size:24px;margin-bottom:4px}}
.subtitle{{color:#666;font-size:14px;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;margin-bottom:20px}}
.card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,0.08)}}
.card h2{{font-size:15px;color:#555;margin-bottom:12px}}
.stat-value{{font-size:28px;font-weight:700}}
.stat-label{{font-size:12px;color:#888}}
.badge{{display:inline-block;padding:4px 12px;border-radius:12px;font-weight:600;font-size:14px}}
.bg-green{{background:#e8f5e9;color:#2e7d32}}
.bg-orange{{background:#fff3e0;color:#e65100}}
.bg-red{{background:#fce4ec;color:#c62828}}
.bg-low{{background:#e8f5e9;color:#2e7d32}}
.bg-medium{{background:#fff3e0;color:#e65100}}
.bg-high{{background:#fce4ec;color:#c62828}}
.fw{{grid-column:1/-1}}
.chart-box{{position:relative;height:250px}}
.chart-box-sm{{position:relative;height:200px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 6px;border-bottom:2px solid #eee;color:#666;font-weight:600}}
td{{padding:6px;border-bottom:1px solid #f0f0f0}}
.addr{{font-family:'SF Mono',Monaco,monospace;font-size:12px;color:#1565c0}}
.plabel{{display:inline-block;padding:2px 6px;border-radius:4px;font-size:11px;background:#e3f2fd;color:#1565c0}}
.scroll{{max-height:400px;overflow-y:auto}}
</style>
</head>
<body>
<div class="container">
  <h1>{symbol} Holdings & Liquidity Dashboard</h1>
  <p class="subtitle">Chain ID: {chain_id} · Token: <span class="addr">{token_address or "N/A"}</span> · Queried: {query_time or "N/A"}</p>
  {empty_note}
  <div class="grid">
    <div class="card">
      <div class="stat-value">{total_addresses}</div>
      <div class="stat-label">Unique Addresses (from transfers)</div>
    </div>
    <div class="card">
      <div class="stat-value">{holdings_count}</div>
      <div class="stat-label">Holders with Balance</div>
    </div>
    <div class="card">
      <div class="stat-value">{len(verified_pools)}</div>
      <div class="stat-label">Verified Pools</div>
    </div>
    <div class="card">
      <div class="stat-value"><span class="badge bg-{risk_lvl_class}">{risk_level}</span></div>
      <div class="stat-label">Risk Score: {risk_score:.4f}</div>
    </div>
  </div>
  <div class="grid">
    <div class="card">
      <h2>Holder Distribution</h2>
      <div class="chart-box-sm"><canvas id="c1"></canvas></div>
    </div>
    <div class="card">
      <h2>Pool Concentration</h2>
      <div class="chart-box-sm"><canvas id="c2"></canvas></div>
    </div>
    <div class="card">
      <h2>Top 10 Non-Pool Holders</h2>
      <div class="chart-box-sm"><canvas id="c3"></canvas></div>
    </div>
  </div>
  <div class="grid">
    <div class="card fw">
      <h2>Top 20 Non-Pool Holders by Balance</h2>
      <div class="scroll"><table><thead><tr><th>#</th><th>Address</th><th>Balance ({symbol})</th><th>Tx Count</th><th>Label</th></tr></thead><tbody>{table_top}</tbody></table></div>
    </div>
  </div>
  <div class="grid">
    <div class="card fw">
      <h2>Pool Addresses in Holder List</h2>
      <div class="scroll"><table><thead><tr><th>Pool Address</th><th>Protocol</th><th>Balance ({symbol})</th><th>Label</th></tr></thead><tbody>{table_pool}</tbody></table></div>
    </div>
  </div>
  <div class="grid">
    <div class="card fw">
      <h2>Pool Identification Summary</h2>
      <div class="scroll"><table><thead><tr><th>Pool Address</th><th>Protocol / Version</th><th>Token Pair</th><th>In Holders List</th></tr></thead><tbody>{table_ident}</tbody></table></div>
    </div>
  </div>
</div>
<script>
const topH = {top_h_json};
const poolH = {pool_h_json};
const poolI = {pool_i_json};
new Chart(document.getElementById('c1'),{{type:'pie',data:{{labels:['Pool LP Holders','Regular Holders'],datasets:[{{data:[{len(pool_holders)},{max(0,holdings_count-len(pool_holders))}],backgroundColor:['#42a5f5','#ef5350']}}]}},options:{{responsive:true,plugins:{{legend:{{position:'bottom'}}}}}}}});
new Chart(document.getElementById('c2'),{{type:'doughnut',data:{{labels:['Main Pool Share','Other Pools'],datasets:[{{data:[{main_pool_share:.1f},{max(0,100-main_pool_share):.1f}],backgroundColor:['#ff7043','#e0e0e0']}}]}},options:{{responsive:true,plugins:{{legend:{{position:'bottom'}}}}}}}});
new Chart(document.getElementById('c3'),{{type:'bar',data:{{labels:topH.slice(0,10).map(d=>d.address.slice(0,8)+'...'),datasets:[{{label:'Balance',data:topH.slice(0,10).map(d=>d.balance_decimal),backgroundColor:'#42a5f5'}}]}},options:{{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true}}}}}}}});
</script>
</body>
</html>"""
    return html


def _table_top_holders(holders: list, symbol: str) -> str:
    rows = []
    for i, h in enumerate(holders[:20], 1):
        label = ""
        if h.get("pool_label"):
            label = f'<span class="plabel">{h["pool_label"]}</span>'
        rows.append(
            f"<tr><td>{i}</td><td class=\"addr\">{h.get('address','')}</td>"
            f"<td>{_fmt_bal(h.get('balance_decimal',0),symbol)}</td>"
            f"<td>{h.get('tx_count',0)}</td><td>{label}</td></tr>"
        )
    return "\n".join(rows)


def _table_pool_holders(holders: list, symbol: str) -> str:
    rows = []
    for h in holders:
        rows.append(
            f"<tr><td class=\"addr\">{h.get('address','')}</td>"
            f"<td>{h.get('pool_label','')}</td>"
            f"<td>{_fmt_bal(h.get('balance_decimal',0),symbol)}</td>"
            f"<td><span class=\"plabel\">POOL</span></td></tr>"
        )
    return "\n".join(rows)


def _table_pool_ident(pools: list) -> str:
    rows = []
    for p in pools:
        t0 = (p.get("token0") or "")[:10] + "..."
        t1 = (p.get("token1") or "")[:10] + "..."
        pair = f"{t0}/{t1}"
        in_list = "Yes" if p.get("in_holders_list") else "No"
        rows.append(
            f"<tr><td class=\"addr\">{p.get('pool_address','')}</td>"
            f"<td>{p.get('protocol','')} {p.get('version','')}</td>"
            f"<td>{pair}</td><td>{in_list}</td></tr>"
        )
    return "\n".join(rows)


def _fmt_bal(bal: float, symbol: str) -> str:
    if bal >= 1_000_000:
        return f"{bal/1_000_000:.2f}M {symbol}"
    if bal >= 1_000:
        return f"{bal/1_000:.2f}K {symbol}"
    return f"{bal:.4f} {symbol}"


def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default
    return default
