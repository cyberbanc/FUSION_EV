from app.ensemble import m9_component, select_side_and_stake


def _round(epoch: int, winner: str, coeff: float = 2.0):
    return {"epoch": epoch, "actual_winner": winner, "winner_coeff_gross": coeff, "move_points": 0.2}


def test_m9_bayesian_smoothing_never_turns_two_matches_into_100_percent():
    winners = ["UP", "DOWN", "UP", "DOWN", "UP", "UP", "DOWN", "UP", "DOWN", "UP", "UP", "DOWN"]
    rounds = [_round(i, winner) for i, winner in enumerate(winners)]
    result = m9_component(rounds)
    assert 0.38 <= result.probability_up <= 0.62
    assert result.probability_up != 1.0


def test_ev_can_choose_down_even_when_up_is_more_probable():
    state = {"trades_count": 200, "bank": 500}
    result = select_side_and_stake(
        probability_up=0.57,
        coeff_up=1.72,
        coeff_down=2.31,
        agreement_up=0.70,
        state=state,
    )
    assert result["signal"] == "DOWN"


def test_warmup_keeps_fixed_stake():
    state = {"trades_count": 10, "bank": 500}
    result = select_side_and_stake(
        probability_up=0.62,
        coeff_up=2.20,
        coeff_down=1.70,
        agreement_up=0.90,
        state=state,
    )
    assert result["stake"] == 5.0
    assert "WARMUP" in result["decision_quality"]
