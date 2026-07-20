from app.config import SETTINGS
from app.risk import should_arm_cooldown, stake_for_ev


def test_ev_stake_tiers():
    assert stake_for_ev(-0.06).stake == 5.0
    assert stake_for_ev(-0.001).stake == 5.0
    assert stake_for_ev(0.0).stake == 10.0
    assert stake_for_ev(0.049999).stake == 10.0
    assert stake_for_ev(0.05).stake == 15.0
    assert stake_for_ev(0.30).stake == 15.0


def test_default_strategy_parameters():
    assert SETTINGS.min_trade_ev == -0.06
    assert SETTINGS.shadow_recent_window == 8
    assert SETTINGS.shadow_recent_min_pnl == -30.0
    assert SETTINGS.quality_window == 30
    assert SETTINGS.quality_min_samples == 10
    assert SETTINGS.quality_min_win_rate == 0.45
    assert SETTINGS.quality_min_profit_factor == 0.85


def test_cooldown_arms_after_each_completed_block_of_three_losses():
    assert should_arm_cooldown(1) is False
    assert should_arm_cooldown(2) is False
    assert should_arm_cooldown(3) is True
    assert should_arm_cooldown(4) is False
    assert should_arm_cooldown(5) is False
    assert should_arm_cooldown(6) is True
