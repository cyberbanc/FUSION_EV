from pathlib import Path


def test_worker_has_explicit_negative_ev_gate():
    source = (Path(__file__).parents[1] / "app" / "worker.py").read_text()
    assert 'no_trade_reason = "NEGATIVE_EV_DISABLED"' in source
    assert 'trade_executed = allowed and stake_decision.eligible and stake > 0.0' in source
    assert '"stake_mode": "nonnegative_ev_10_15"' in source
    assert 'SETTINGS.stake_mid' in source
    assert 'SETTINGS.stake_high' in source
