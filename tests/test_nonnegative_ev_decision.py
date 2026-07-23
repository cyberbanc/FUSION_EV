from types import SimpleNamespace

from app.models import Forecast
from app import worker


def make_forecast(selected_ev: float) -> Forecast:
    return Forecast(
        signal="UP",
        probability_up=0.55,
        probability_down=0.45,
        raw_probability_up=0.55,
        raw_expected_coeff_up=2.0,
        raw_expected_coeff_down=2.0,
        payout_correction_up=1.0,
        payout_correction_down=1.0,
        payout_bucket_up="2.00-2.50",
        payout_bucket_down="2.00-2.50",
        payout_bucket_ready_up=True,
        payout_bucket_ready_down=True,
        expected_coeff_up=2.0,
        expected_coeff_down=2.0,
        ev_up=selected_ev,
        ev_down=-0.10,
        selected_ev=selected_ev,
        agreement=0.70,
        source_key="EV_PRIMARY",
        selection_reason="POSITIVE_EV_BEST_SIDE",
        components=[],
        weights={},
        features={},
    )


class Snapshot(SimpleNamespace):
    def to_dict(self):
        return {"betting_epoch": self.betting_epoch, "live_epoch": self.live_epoch}


def prepare(monkeypatch, selected_ev: float, shadow_allowed: bool = True):
    snapshot = Snapshot(
        betting_epoch=123,
        live_epoch=122,
        chain_timestamp=1000,
        seconds_to_lock=40,
    )
    monkeypatch.setattr(worker.db, "get_decision", lambda epoch: None)
    monkeypatch.setattr(worker, "forecast", lambda current: make_forecast(selected_ev))
    monkeypatch.setattr(
        worker,
        "evaluate_shadow",
        lambda source, signal, ev: (
            shadow_allowed,
            "SHADOW_QUALITY_CONFIRMED" if shadow_allowed else "QUALITY_WIN_RATE_BELOW_MINIMUM",
            {"samples": 30},
        ),
    )
    monkeypatch.setattr(worker.db, "consume_cooldown_round", lambda: False)
    monkeypatch.setattr(worker.db, "get_state", lambda: {"bank": 500.0})
    monkeypatch.setattr(worker.db, "insert_decision", lambda data: data)
    return snapshot


def test_negative_ev_is_recorded_but_not_traded(monkeypatch):
    decision = worker.create_locked_decision(prepare(monkeypatch, -0.0001))
    assert decision["trade_executed"] is False
    assert decision["stake"] == 0.0
    assert decision["stake_tier"] == "NO_TRADE"
    assert decision["no_trade_reason"] == "NEGATIVE_EV_DISABLED"


def test_mid_ev_trades_ten(monkeypatch):
    decision = worker.create_locked_decision(prepare(monkeypatch, 0.0))
    assert decision["trade_executed"] is True
    assert decision["stake"] == 10.0
    assert decision["stake_tier"] == "MID_NONNEGATIVE_EV"
    assert decision["no_trade_reason"] is None
    assert decision["stake_mode"] == "nonnegative_ev_10_15"


def test_high_ev_trades_fifteen(monkeypatch):
    decision = worker.create_locked_decision(prepare(monkeypatch, 0.05))
    assert decision["trade_executed"] is True
    assert decision["stake"] == 15.0
    assert decision["stake_tier"] == "HIGH_EV"


def test_high_ev_still_requires_existing_shadow_quality_rules(monkeypatch):
    decision = worker.create_locked_decision(
        prepare(monkeypatch, 0.20, shadow_allowed=False)
    )
    assert decision["trade_executed"] is False
    assert decision["stake"] == 0.0
    assert decision["no_trade_reason"] == "QUALITY_WIN_RATE_BELOW_MINIMUM"
