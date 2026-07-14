from app.ensemble import (
    apply_payout_correction,
    m9_component,
    payout_calibration,
    select_side_and_stake,
)


def _round(epoch: int, winner: str, coeff: float = 2.0):
    return {
        "epoch": epoch,
        "actual_winner": winner,
        "winner_coeff_gross": coeff,
        "move_points": 0.2,
    }


def test_m9_bayesian_smoothing_never_turns_two_matches_into_100_percent():
    winners = [
        "UP",
        "DOWN",
        "UP",
        "DOWN",
        "UP",
        "UP",
        "DOWN",
        "UP",
        "DOWN",
        "UP",
        "UP",
        "DOWN",
    ]
    rounds = [_round(i, winner) for i, winner in enumerate(winners)]
    result = m9_component(rounds)
    assert 0.38 <= result.probability_up <= 0.62
    assert result.probability_up != 1.0


def test_corrected_positive_ev_can_choose_less_probable_down_with_support():
    state = {"trades_count": 200, "bank": 500}
    result = select_side_and_stake(
        probability_up=0.57,
        coeff_up=1.50,
        coeff_down=2.80,
        agreement_up=0.30,
        state=state,
        payout_calibration_ready=True,
    )
    assert result["signal"] == "DOWN"
    assert result["selection_reason"] == "BEST_CORRECTED_EV"


def test_negative_ev_falls_back_to_more_probable_side():
    state = {"trades_count": 200, "bank": 500}
    result = select_side_and_stake(
        probability_up=0.57,
        coeff_up=1.55,
        coeff_down=2.00,
        agreement_up=0.60,
        state=state,
        payout_calibration_ready=True,
    )
    assert result["signal"] == "UP"
    assert result["selection_reason"] == "NEGATIVE_EV_PROBABILITY_FALLBACK"
    assert result["stake"] == 5.0


def test_low_agreement_small_ev_falls_back_to_probability():
    state = {"trades_count": 200, "bank": 500}
    result = select_side_and_stake(
        probability_up=0.57,
        coeff_up=1.60,
        coeff_down=2.40,
        agreement_up=0.90,
        state=state,
        payout_calibration_ready=True,
    )
    assert result["signal"] == "UP"
    assert result["selection_reason"] == "LOW_AGREEMENT_PROBABILITY_FALLBACK"
    assert result["stake"] == 5.0


def test_warmup_keeps_fixed_stake():
    state = {"trades_count": 10, "bank": 500}
    result = select_side_and_stake(
        probability_up=0.62,
        coeff_up=2.20,
        coeff_down=1.70,
        agreement_up=0.90,
        state=state,
        payout_calibration_ready=False,
    )
    assert result["stake"] == 5.0
    assert "WARMUP" in result["decision_quality"]


def test_initial_payout_correction_is_conservative_before_enough_samples():
    calibration = payout_calibration([])
    assert calibration["ready"] is False
    assert calibration["up"]["correction"] == 0.75
    assert calibration["down"]["correction"] == 0.75
    assert abs(apply_payout_correction(3.0, calibration["up"]["correction"]) - 2.25) < 1e-12


def test_payout_calibration_uses_retained_completed_history():
    rows = []
    for i in range(60):
        rows.append(
            {
                "raw_expected_coeff_up": 2.5,
                "raw_expected_coeff_down": 2.0,
                "expected_coeff_up": 2.5,
                "expected_coeff_down": 2.0,
                "final_coeff_up": 1.75 + (i % 3) * 0.01,
                "final_coeff_down": 1.40 + (i % 3) * 0.01,
            }
        )
    calibration = payout_calibration(rows)
    assert calibration["ready"] is True
    assert 0.65 <= calibration["up"]["correction"] <= 0.75
    assert 0.65 <= calibration["down"]["correction"] <= 0.75


def test_report_case_498256_no_longer_chases_low_agreement_payout():
    state = {"trades_count": 5, "bank": 501.43}
    result = select_side_and_stake(
        probability_up=0.5610252367,
        coeff_up=1.01,
        coeff_down=3.1688695821 * 0.75,
        agreement_up=0.9547305346,
        state=state,
        payout_calibration_ready=False,
    )
    assert result["signal"] == "UP"
    assert result["selection_reason"] == "LOW_AGREEMENT_PROBABILITY_FALLBACK"
    assert result["stake"] == 5.0


def test_report_case_498254_negative_ev_uses_probability_not_payout():
    state = {"trades_count": 5, "bank": 501.43}
    result = select_side_and_stake(
        probability_up=0.4360689505,
        coeff_up=2.5109828073 * 0.75,
        coeff_down=1.01,
        agreement_up=0.0282075348,
        state=state,
        payout_calibration_ready=False,
    )
    assert result["signal"] == "DOWN"
    assert result["selection_reason"] == "NEGATIVE_EV_PROBABILITY_FALLBACK"
    assert result["stake"] == 5.0
