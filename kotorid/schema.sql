CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    avg_cost DOUBLE PRECISION NOT NULL,
    current_price DOUBLE PRECISION NOT NULL,
    market_value DOUBLE PRECISION NOT NULL,
    unrealized_pnl DOUBLE PRECISION NOT NULL,
    unrealized_pnl_pct DOUBLE PRECISION NOT NULL,
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    underlying TEXT,
    expiry TEXT,
    strike DOUBLE PRECISION,
    put_call TEXT,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (symbol)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    earnings_date TEXT,
    scanner_output TEXT,
    strategist_output TEXT,
    risk_manager_output TEXT,
    devils_advocate_output TEXT,
    portfolio_manager_output TEXT,
    final_decision TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ic_positions (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    expiry TEXT NOT NULL,
    short_call DOUBLE PRECISION NOT NULL,
    long_call DOUBLE PRECISION NOT NULL,
    short_put DOUBLE PRECISION NOT NULL,
    long_put DOUBLE PRECISION NOT NULL,
    spread_width DOUBLE PRECISION NOT NULL,
    entry_credit DOUBLE PRECISION NOT NULL,
    contracts INTEGER NOT NULL,
    max_loss DOUBLE PRECISION NOT NULL,
    current_debit DOUBLE PRECISION,
    pct_max_profit DOUBLE PRECISION,
    regime_at_entry TEXT,
    iv_percentile_at_entry DOUBLE PRECISION,
    expected_move DOUBLE PRECISION,
    exit_debit DOUBLE PRECISION,
    exit_reason TEXT,
    realized_pnl DOUBLE PRECISION,
    agent_run_id INTEGER REFERENCES agent_runs(id),
    order_id TEXT,
    position_warning_at TEXT,
    short_strike_warned_at TEXT
);

CREATE TABLE IF NOT EXISTS iv_history (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    iv DOUBLE PRECISION NOT NULL,
    iv_rank DOUBLE PRECISION,
    iv_percentile DOUBLE PRECISION,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS iv_crush_history (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    earnings_date TEXT NOT NULL,
    iv_before DOUBLE PRECISION NOT NULL,
    iv_after DOUBLE PRECISION NOT NULL,
    crush_pct DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS regime_snapshots (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    market_regime TEXT NOT NULL CHECK(market_regime IN ('normal','caution','no_trade')),
    earnings_regime TEXT NOT NULL CHECK(earnings_regime IN ('pre_earnings','post_earnings','none')),
    iv_regime TEXT NOT NULL CHECK(iv_regime IN ('high','normal','low')),
    vix DOUBLE PRECISION,
    adx DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS thesis (
    symbol TEXT PRIMARY KEY,
    position_type TEXT NOT NULL CHECK(position_type IN ('ic','directional')),
    entry_catalyst TEXT,
    catalyst_source TEXT,
    price_target DOUBLE PRECISION,
    stop_level DOUBLE PRECISION,
    time_horizon TEXT,
    status TEXT NOT NULL DEFAULT 'intact' CHECK(status IN ('intact','weakening','invalidated')),
    auto_populated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id SERIAL PRIMARY KEY,
    agent_run_id INTEGER REFERENCES agent_runs(id),
    symbol TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    order_status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK(order_status IN ('pending_approval','approved','rejected','placed','filled','skipped')),
    short_call DOUBLE PRECISION,
    long_call DOUBLE PRECISION,
    short_put DOUBLE PRECISION,
    long_put DOUBLE PRECISION,
    expected_credit DOUBLE PRECISION,
    contracts INTEGER,
    max_loss DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    symbol TEXT,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    notified_at TEXT
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id SERIAL PRIMARY KEY,
    priority TEXT NOT NULL CHECK(priority IN ('urgent','action_required','for_review')),
    item_type TEXT NOT NULL,
    symbol TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    actions TEXT NOT NULL DEFAULT '[]',
    ref_id INTEGER,
    created_at TEXT NOT NULL,
    dismissed_at TEXT
);

CREATE TABLE IF NOT EXISTS trailing_stops (
    symbol TEXT PRIMARY KEY,
    trail_type TEXT NOT NULL CHECK(trail_type IN ('percent','dollar')),
    trail_value DOUBLE PRECISION NOT NULL,
    high_water_mark DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefings (
    id SERIAL PRIMARY KEY,
    period TEXT NOT NULL CHECK(period IN ('daily','weekly','monthly')),
    content TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol TEXT NOT NULL,
    earnings_date DATE NOT NULL,
    eps_estimate DOUBLE PRECISION,
    reported_eps DOUBLE PRECISION,
    surprise_pct DOUBLE PRECISION,
    is_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, earnings_date)
);
