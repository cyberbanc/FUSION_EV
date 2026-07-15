from app.ensemble import (
    adaptive_weights,
    apply_payout_correction,
    calibration_for_raw,
    m9_component,
    payout_calibration,
    select_side_and_stake,
)
from app.models import ComponentSignal


def _round(epoch: int, winner: str, coeff: float = 2.0):
    return {
        "epoch": epoch,
        "actual_winner": winner,
        "winner_coeff_gross": coeff,
        "move_points": 0.2,
    }


def test_m9_bayesian_smoothing_never_turns_few_matches_into_100_percent():
    winners = [
        "UP", "DOWN", "UP", "DOWN", "UP", "UP",
        "DOWN", "UP", "DOWN", "UP", "UP", "DOWN",
    ]
    result = m9_component([_round(i, winner) for i, winner in enumerate(winners)])
    assert 0.38 <= result.probability_up <= 0.62
    assert result.probability_up != 1.0


def test_m9_and_patterns_have_zero_voting_weight_by_default():
    components = [
        ComponentSignal("price", 0.55, 1.0, True, "test"),
        ComponentSignal("binance", 0.55, 1.0, True, "test"),
        ComponentSignal("crowd", 0.55, 1.0, True, "test"),
        ComponentSignal("m9", 0.62, 1.0, True, "test"),
        ComponentSignal("pattern", 0.62, 1.0, True, "test"),
    ]
    weights = adaptive_weights(components, [])
    assert weights["m9"] == 0.0
    assert weights["pattern"] == 0.0
    assert abs(sum(weights.values()) - 1.0) < 1e-12


def test_ev_reversal_below_35_percent_is_blocked():
    result = select_side_and_stake(
        probability_up=0.55,
        coeff_up=1.40,
        coeff_down=2.95,  # DOWN EV 32.75%, below reversal threshold
        agreement_up=0.65,
        state={"trades_count": 500, "bank": 500},
        payout_ready_up=True,
        payout_ready_down=True,
        crowd_probability_up=0.54,
        crowd_available=True,
        binance_probability_up=0.48,
        binance_available=True,
    )
    assert result["signal"] == "UP"
    assert result["selection_reason"] == "EV_REVERSAL_BLOCKED_PROBABILITY_FALLBACK"
    assert result["trade_executed"] is False
    assert result["stake"] == 0.0
    assert result["no_trade_reason"] == "EV_NOT_ABOVE_MINIMUM"


def test_strong_ev_reversal_requires_ev_and_agreement_and_can_pass():
    result = select_side_and_stake(
        probability_up=0.55,
        coeff_up=1.35,
        coeff_down=3.20,  # DOWN EV 44%
        agreement_up=0.65,  # DOWN agreement 35%
        state={"trades_count": 500, "bank": 500},
        payout_ready_up=True,
        payout_ready_down=True,
    )
    assert result["signal"] == "DOWN"
    assert result["selection_reason"] == "BEST_CORRECTED_EV"


def test_strong_ev_reversal_with_low_agreement_is_blocked():
    result = select_side_and_stake(
        probability_up=0.55,
        coeff_up=1.35,
        coeff_down=3.20,
        agreement_up=0.80,  # DOWN agreement only 20%
        state={"trades_count": 500, "bank": 500},
        payout_ready_up=True,
        payout_ready_down=True,
    )
    assert result["signal"] == "UP"
    assert result["selection_reason"] == "EV_REVERSAL_BLOCKED_PROBABILITY_FALLBACK"


def test_weak_ev_uses_crowd_binance_consensus():
    result = select_side_and_stake(
        probability_up=0.54,
        coeff_up=1.80,
        coeff_down=2.10,  # best EV below 30%
        agreement_up=0.60,
        state={"trades_count": 50, "bank": 500},
        payout_ready_up=True,
        payout_ready_down=True,
        crowd_probability_up=0.47,
        crowd_available=True,
        binance_probability_up=0.48,
        binance_available=True,
    )
    assert result["signal"] == "DOWN"
    assert result["crowd_binance_consensus"] == "DOWN"
    assert result["selection_reason"] == "WEAK_EV_CROWD_BINANCE_FALLBACK"
    assert result["trade_executed"] is False
    assert result["stake"] == 0.0


def test_weak_ev_without_consensus_uses_probability():
    result = select_side_and_stake(
        probability_up=0.54,
        coeff_up=1.90,
        coeff_down=2.20,
        agreement_up=0.60,
        state={"trades_count": 50, "bank": 500},
        payout_ready_up=True,
        payout_ready_down=True,
        crowd_probability_up=0.47,
        crowd_available=True,
        binance_probability_up=0.53,
        binance_available=True,
    )
    assert result["signal"] == "UP"
    assert result["selection_reason"] == "WEAK_EV_PROBABILITY_FALLBACK"
    assert result["trade_executed"] is True
    assert result["stake"] == 10.0


def test_bucket_priors_are_conservative_for_large_coefficients():
    calibration = payout_calibration([])
    up = calibration_for_raw(calibration, "up", 3.55)
    down = calibration_for_raw(calibration, "down", 3.55)
    assert up["name"] == ">= 3.00"
    assert down["name"] == ">= 3.00"
    assert up["correction"] == 0.60
    assert down["correction"] == 0.70
    assert abs(apply_payout_correction(3.55, up["correction"]) - 2.13) < 1e-12
    assert abs(apply_payout_correction(3.55, down["correction"]) - 2.485) < 1e-12


def test_bucket_calibration_does_not_mix_small_and_large_coefficients():
    rows = []
    for i in range(20):
        rows.append({
            "raw_expected_coeff_up": 1.40,
            "raw_expected_coeff_down": 1.40,
            "final_coeff_up": 1.33,
            "final_coeff_down": 1.33,
        })
        rows.append({
            "raw_expected_coeff_up": 3.50,
            "raw_expected_coeff_down": 3.50,
            "final_coeff_up": 1.925 + (i % 2) * 0.01,
            "final_coeff_down": 2.275 + (i % 2) * 0.01,
        })
    calibration = payout_calibration(rows)
    low_up = calibration_for_raw(calibration, "up", 1.40)
    high_up = calibration_for_raw(calibration, "up", 3.50)
    high_down = calibration_for_raw(calibration, "down", 3.50)
    assert low_up["ready"] is True
    assert high_up["ready"] is True
    assert high_up["correction"] <= 0.60
    assert high_down["correction"] <= 0.70
    assert low_up["correction"] > high_up["correction"]


def test_variable_stakes_are_disabled_by_default():
    result = select_side_and_stake(
        probability_up=0.62,
        coeff_up=2.50,
        coeff_down=1.40,
        agreement_up=0.90,
        state={"trades_count": 1000, "bank": 500},
        payout_ready_up=True,
        payout_ready_down=True,
    )
    assert result["selected_ev"] >= 0.50
    assert result["stake"] == 10.0
    assert result["variable_stake_ready"] is False


def test_positive_ev_trade_is_executed():
    result = select_side_and_stake(
        probability_up=0.54,
        coeff_up=1.95,
        coeff_down=1.80,
        agreement_up=0.65,
        state={"trades_count": 10, "bank": 500},
        payout_ready_up=True,
        payout_ready_down=True,
    )
    assert result["selected_ev"] > 0
    assert result["trade_executed"] is True
    assert result["stake"] == 10.0
    assert result["no_trade_reason"] is None


def test_positive_ev_waits_for_payout_bucket_when_required():
    result = select_side_and_stake(
        probability_up=0.54,
        coeff_up=1.95,
        coeff_down=1.80,
        agreement_up=0.65,
        state={"trades_count": 10, "bank": 500},
        payout_ready_up=False,
        payout_ready_down=False,
    )
    assert result["selected_ev"] > 0
    assert result["trade_executed"] is False
    assert result["stake"] == 0.0
    assert result["no_trade_reason"] == "PAYOUT_BUCKET_NOT_READY"
