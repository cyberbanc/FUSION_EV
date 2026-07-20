from __future__ import annotations

from app import main


def test_signal_uses_cached_locked_decision_without_rpc_tick(monkeypatch):
    decision = {
        "betting_epoch": 123,
        "live_epoch": 122,
        "signal": "UP",
        "stake": 15.0,
        "trade_executed": True,
        "snapshot_json": {"seconds_to_lock": 40, "chainlink_price": 600.0},
    }
    monkeypatch.setattr(
        main,
        "signal_cache",
        lambda: {
            "ok": True,
            "snapshot": {"betting_epoch": 123, "live_epoch": 122, "seconds_to_lock": 31},
            "decision": decision,
            "worker_tick": {
                "betting_epoch": 123,
                "live_epoch": 122,
                "seconds_to_lock": 31,
                "decision_window": True,
            },
            "updated_at": 999,
        },
    )

    result = main.signal()

    assert result["ok"] is True
    assert result["status"] == "LOCKED"
    assert result["decision_locked"] is True
    assert result["signal"] == "UP"
    assert result["stake"] == 15.0
    assert result["seconds_to_lock"] == 31
    assert result["snapshot"]["seconds_to_lock"] == 31
    assert not hasattr(main, "tick")


def test_signal_wait_uses_live_cache_and_does_not_need_database(monkeypatch):
    monkeypatch.setattr(
        main,
        "signal_cache",
        lambda: {
            "ok": True,
            "snapshot": {
                "betting_epoch": 456,
                "live_epoch": 455,
                "seconds_to_lock": 91,
                "decision_window": False,
                "chainlink_price": 601.25,
            },
            "decision": None,
            "worker_tick": {
                "betting_epoch": 456,
                "live_epoch": 455,
                "seconds_to_lock": 91,
                "decision_window": False,
            },
            "updated_at": 1000,
        },
    )
    monkeypatch.setattr(main.db, "enabled", lambda: False)

    result = main.signal()

    assert result["ok"] is True
    assert result["status"] == "WAIT"
    assert result["decision_locked"] is False
    assert result["betting_epoch"] == 456
    assert result["live_epoch"] == 455
    assert result["seconds_to_lock"] == 91
    assert result["snapshot"]["chainlink_price"] == 601.25
    assert result["stake"] == 0.0
