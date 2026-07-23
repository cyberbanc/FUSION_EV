from __future__ import annotations

from dataclasses import dataclass

from .config import SETTINGS


@dataclass(frozen=True)
class StakeDecision:
    stake: float
    tier: str
    eligible: bool


def stake_for_ev(selected_ev: float) -> StakeDecision:
    """Return the non-negative EV stake decision.

    Negative selected EV is never eligible for a trade. Non-negative EV below
    the HIGH_EV threshold receives $10; HIGH_EV receives $15. The stake never
    depends on previous wins or losses.
    """
    value = float(selected_ev)
    if value < SETTINGS.stake_mid_ev:
        return StakeDecision(0.0, "NEGATIVE_EV_DISABLED", False)
    if value < SETTINGS.stake_high_ev:
        return StakeDecision(float(SETTINGS.stake_mid), "MID_NONNEGATIVE_EV", True)
    return StakeDecision(float(SETTINGS.stake_high), "HIGH_EV", True)


def should_arm_cooldown(current_loss_streak: int) -> bool:
    trigger = int(SETTINGS.cooldown_loss_streak_trigger)
    return trigger > 0 and current_loss_streak > 0 and current_loss_streak % trigger == 0
