from app.shadow import summarize


def test_shadow_metrics_newest_first():
    rows = [
        {"shadow_pnl": -10.0},
        {"shadow_pnl": -10.0},
        {"shadow_pnl": 9.0},
        {"shadow_pnl": 8.0},
    ]
    metrics = summarize(rows)
    assert metrics.samples == 4
    assert metrics.wins == 2
    assert metrics.losses == 2
    assert metrics.current_loss_streak == 2
    assert metrics.pnl == -3.0
    assert round(metrics.profit_factor or 0, 2) == 0.85


def test_evaluate_blocks_bad_recent_window(monkeypatch):
    from app import shadow

    newest_first = [{"shadow_pnl": -10.0}] * 6 + [{"shadow_pnl": 8.0}] * 2

    def fake_rows(source_key, signal, lookback):
        return newest_first[:lookback]

    monkeypatch.setattr("app.db.shadow_rows", fake_rows)
    allowed, reason, _ = shadow.evaluate("X", "UP", -0.02)
    assert allowed is False
    assert reason == "SHADOW_RECENT_PNL_DEGRADED"


def test_evaluate_blocks_low_quality_profit_factor(monkeypatch):
    from app import shadow

    # Recent eight are healthy, but the 30-signal quality sample is weak.
    recent = [{"shadow_pnl": 8.0}] * 5 + [{"shadow_pnl": -10.0}] * 3
    quality = recent + [{"shadow_pnl": -10.0}] * 12

    def fake_rows(source_key, signal, lookback):
        return (recent if lookback == 8 else quality)[:lookback]

    monkeypatch.setattr("app.db.shadow_rows", fake_rows)
    allowed, reason, _ = shadow.evaluate("X", "DOWN", 0.02)
    assert allowed is False
    assert reason in {"QUALITY_WIN_RATE_BELOW_MINIMUM", "QUALITY_PROFIT_FACTOR_BELOW_MINIMUM"}


def test_evaluate_allows_healthy_signal(monkeypatch):
    from app import shadow

    rows = [{"shadow_pnl": 9.0}] * 7 + [{"shadow_pnl": -10.0}] * 3

    def fake_rows(source_key, signal, lookback):
        return rows[:lookback]

    monkeypatch.setattr("app.db.shadow_rows", fake_rows)
    allowed, reason, _ = shadow.evaluate("X", "DOWN", -0.02)
    assert allowed is True
    assert reason == "SHADOW_QUALITY_CONFIRMED"


def test_evaluate_allows_warmup(monkeypatch):
    from app import shadow

    rows = [{"shadow_pnl": -10.0}, {"shadow_pnl": 9.0}]

    def fake_rows(source_key, signal, lookback):
        return rows[:lookback]

    monkeypatch.setattr("app.db.shadow_rows", fake_rows)
    allowed, reason, _ = shadow.evaluate("X", "UP", 0.01)
    assert allowed is True
    assert reason == "QUALITY_WARMUP_ALLOWED"
