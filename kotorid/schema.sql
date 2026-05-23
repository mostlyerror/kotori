CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    current_price REAL NOT NULL,
    market_value REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    unrealized_pnl_pct REAL NOT NULL,
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    underlying TEXT,
    expiry TEXT,
    strike REAL,
    put_call TEXT,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (symbol)
);

CREATE TABLE IF NOT EXISTS ic_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    expiry TEXT NOT NULL,
    short_call REAL NOT NULL,
    long_call REAL NOT NULL,
    short_put REAL NOT NULL,
    long_put REAL NOT NULL,
    spread_width REAL NOT NULL,
    entry_credit REAL NOT NULL,
    contracts INTEGER NOT NULL,
    max_loss REAL NOT NULL,
    current_debit REAL,
    pct_max_profit REAL,
    regime_at_entry TEXT,
    iv_percentile_at_entry REAL,
    expected_move REAL,
    exit_debit REAL,
    exit_reason TEXT,
    realized_pnl REAL,
    agent_run_id INTEGER REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS iv_history (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    iv REAL NOT NULL,
    iv_rank REAL,
    iv_percentile REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS iv_crush_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    earnings_date TEXT NOT NULL,
    iv_before REAL NOT NULL,
    iv_after REAL NOT NULL,
    crush_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS regime_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    market_regime TEXT NOT NULL CHECK(market_regime IN ('normal','caution','no_trade')),
    earnings_regime TEXT NOT NULL CHECK(earnings_regime IN ('pre_earnings','post_earnings','none')),
    iv_regime TEXT NOT NULL CHECK(iv_regime IN ('high','normal','low')),
    vix REAL,
    adx REAL
);

CREATE TABLE IF NOT EXISTS thesis (
    symbol TEXT PRIMARY KEY,
    position_type TEXT NOT NULL CHECK(position_type IN ('ic','directional')),
    entry_catalyst TEXT,
    catalyst_source TEXT,
    price_target REAL,
    stop_level REAL,
    time_horizon TEXT,
    status TEXT NOT NULL DEFAULT 'intact' CHECK(status IN ('intact','weakening','invalidated')),
    auto_populated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_run_id INTEGER REFERENCES agent_runs(id),
    symbol TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    order_status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK(order_status IN ('pending_approval','approved','rejected','placed','filled','skipped')),
    short_call REAL,
    long_call REAL,
    short_put REAL,
    long_put REAL,
    expected_credit REAL,
    contracts INTEGER,
    max_loss REAL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    notified_at TEXT
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    trail_value REAL NOT NULL,
    high_water_mark REAL NOT NULL,
    stop_price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL CHECK(period IN ('daily','weekly','monthly')),
    content TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
