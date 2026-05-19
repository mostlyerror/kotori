from datetime import date, datetime, timedelta, timezone
import json
import aiosqlite
import random

NOW = lambda: datetime.now(tz=timezone.utc).isoformat()
TODAY = date.today().isoformat()
EXPIRY_3D = (date.today() + timedelta(days=3)).isoformat()


async def seed_mock_data(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT COUNT(*) FROM positions")
    if (await cursor.fetchone())[0] > 0:
        return

    # Positions
    await db.executemany(
        """INSERT INTO positions
           (symbol, quantity, avg_cost, current_price, market_value,
            unrealized_pnl, unrealized_pnl_pct, instrument_type, last_updated)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            ("NVDA", 100, 842.10, 869.42, 86942.0, 2732.0, 0.0324, "stock", NOW()),
            ("META", 50, 583.20, 572.44, 28622.0, -538.0, -0.0184, "stock", NOW()),
            ("AMZN", 25, 196.30, 198.52, 4963.0, 55.5, 0.0113, "stock", NOW()),
            ("TSLA", -200, 0, 0, 0, 0, 0, "option", NOW()),
            ("SPY", -5, 2.10, 4.35, -2175.0, -1125.0, -0.1071, "option", NOW()),
        ]
    )

    # Open IC — TSLA
    await db.execute(
        """INSERT INTO ic_positions
           (symbol, entry_date, expiry, short_call, long_call, short_put, long_put,
            spread_width, entry_credit, contracts, max_loss, current_debit,
            pct_max_profit, regime_at_entry, iv_percentile_at_entry, expected_move)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("TSLA", TODAY, EXPIRY_3D, 200.0, 205.0, 185.0, 180.0,
         5.0, 1.85, 2, 630.0, 0.72, 0.611, "normal", 0.78, 7.20)
    )

    # Thesis entries
    await db.executemany(
        """INSERT INTO thesis
           (symbol, position_type, entry_catalyst, catalyst_source,
            price_target, stop_level, time_horizon, status, auto_populated,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("NVDA", "directional", "Insider cluster buy", "SEC Form 4 / Unusual Whales",
             1050.0, 820.0, "4 weeks", "intact", 0, NOW(), NOW()),
            ("META", "directional", "Earnings beat + guidance raise", "Q1 earnings call",
             620.0, 560.0, "6 weeks", "intact", 0, NOW(), NOW()),
            ("AMZN", "directional", "AWS re-acceleration + margin expansion", "Analyst notes",
             210.0, 190.0, "8 weeks", "intact", 0, NOW(), NOW()),
            ("TSLA", "ic", "Earnings IV crush", "4-agent pipeline",
             None, None, "3 days", "intact", 1, NOW(), NOW()),
            ("SPY", "directional", "Macro hedge — rate sensitivity", "Technical analysis",
             None, 448.0, "2 weeks", "invalidated", 0, NOW(), NOW()),
        ]
    )

    # Notes
    await db.executemany(
        "INSERT INTO notes (symbol, body, created_at) VALUES (?,?,?)",
        [
            ("NVDA", "3 insiders bought >$2M combined last week. Breakout above 200MA confirmed.", NOW()),
            ("NVDA", "Holding into earnings. Thesis intact per latest SEC Form 4 filings.", NOW()),
            ("TSLA", "Auto-entered via pipeline. IVP=78% at entry, expected move ±$7.20.", NOW()),
            ("SPY", "Stop level $448 breached pre-market. Consider closing today.", NOW()),
        ]
    )

    # IV history — 30 days per symbol
    random.seed(42)
    symbols_iv = {
        "NVDA": (0.45, 0.65), "META": (0.35, 0.55),
        "AMZN": (0.30, 0.50), "TSLA": (0.60, 0.90), "SPY": (0.15, 0.35),
    }
    iv_rows = []
    for sym, (lo, hi) in symbols_iv.items():
        ivs = [lo + random.random() * (hi - lo) for _ in range(30)]
        for i, iv in enumerate(ivs):
            d = (date.today() - timedelta(days=29 - i)).isoformat()
            rank = (iv - lo) / (hi - lo)
            pct = sum(1 for v in ivs[:i+1] if v < iv) / (i + 1)
            iv_rows.append((sym, d, round(iv, 4), round(rank, 4), round(pct, 4)))
    await db.executemany(
        "INSERT OR IGNORE INTO iv_history (symbol, date, iv, iv_rank, iv_percentile) VALUES (?,?,?,?,?)",
        iv_rows
    )

    # Regime snapshots
    await db.executemany(
        """INSERT INTO regime_snapshots
           (symbol, timestamp, market_regime, earnings_regime, iv_regime, vix, adx)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("NVDA", NOW(), "normal", "none", "normal", 18.4, 28.3),
            ("TSLA", NOW(), "normal", "pre_earnings", "high", 18.4, 42.1),
            ("META", NOW(), "normal", "none", "normal", 18.4, 22.7),
            ("AMZN", NOW(), "normal", "none", "normal", 18.4, 18.9),
            ("SPY", NOW(), "normal", "none", "low", 18.4, 15.2),
        ]
    )

    # Agent run for TSLA IC
    await db.execute(
        """INSERT INTO agent_runs
           (symbol, earnings_date, scanner_output, strategist_output,
            risk_manager_output, devils_advocate_output, portfolio_manager_output,
            final_decision, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            "TSLA", TODAY,
            json.dumps({"passed": True, "regime": "normal", "iv_percentile": 0.78}),
            json.dumps({"recommendation": "trade", "reasoning": "Strong IV crush history (avg 34%). IVP 78% well above 70% threshold. Front-week expiry captures earnings event cleanly."}),
            json.dumps({"contracts": 2, "max_loss": 630.0, "verdict": "approved", "reasoning": "2% risk limit satisfied. No sector concentration issues."}),
            json.dumps({"flag": None, "reasoning": "No material news catalyst to invalidate. Options chain liquid. Standard earnings play."}),
            json.dumps({"decision": "trade", "reasoning": "All three agents aligned. Proceed with 2 contracts."}),
            "trade", NOW()
        )
    )

    # Inbox items
    await db.executemany(
        """INSERT INTO inbox_items
           (priority, item_type, symbol, title, body, actions, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("urgent", "thesis_invalidated", "SPY",
             "SPY P — Thesis invalidated",
             "Stop level $448 breached. Current loss -$1,125 (-11.9%). Recommend closing to limit further drawdown.",
             json.dumps(["close", "keep", "note"]), NOW()),
            ("action_required", "ic_candidate", "AMZN",
             "AMZN IC Candidate — Pipeline recommends TRADE",
             "IVP 78% · SC205/LC210 SP190/LP185 · Credit $1.65 · 2 contracts · Max loss $670 · Expires Friday",
             json.dumps(["approve", "reject", "view_pipeline"]), NOW()),
            ("for_review", "profit_approaching", "TSLA",
             "TSLA IC — 61% of max profit captured",
             "Current debit $0.72 vs entry credit $1.85. 3 DTE. Approaching 50% target ($0.925).",
             json.dumps(["close_now", "dismiss"]), NOW()),
            ("for_review", "briefing_ready", None,
             "Daily briefing ready",
             "Portfolio +$1,027 today (+1.2% NAV). 1 open IC performing well. 1 position requires attention.",
             json.dumps(["read", "dismiss"]), NOW()),
        ]
    )

    # Daily briefing
    await db.execute(
        """INSERT INTO briefings (period, content, generated_at) VALUES (?,?,?)""",
        (
            "daily",
            f"""# Daily Briefing — {TODAY}

## Portfolio Summary
NAV $84,230 · +$1,027 today (+1.2%) · VIX 18.4 (normal regime)

## Open Positions

**[NVDA]** — Insider thesis intact. Price $869.42, up 3.2% from avg cost. No action required — hold to $1,050 target.

**[TSLA IC]** — Iron condor performing well. 61% of max profit captured with 3 DTE. Current debit $0.72 vs credit $1.85.

**[META]** — Thesis intact. Minor drawdown (-1.8%) within normal range.

**[AMZN]** — Flat, +1.1%. AWS re-acceleration thesis intact.

**[SPY P]** ⚠️ — Thesis invalidated. Stop level $448 breached pre-market. Recommend closing today.

## Recommended Actions
1. Close SPY P position (stop breached)
2. Review AMZN IC candidate (awaiting approval)
""",
            NOW()
        )
    )

    await db.commit()
