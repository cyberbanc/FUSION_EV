from __future__ import annotations

from dataclasses import dataclass

from .config import SETTINGS


@dataclass(frozen=True)
class StakeDecision:
    stake: float
    tier: str


def stake_for_ev(selected_ev: float) -> StakeDecision:
    """Return the stake tier for an already-approved signal.

    The size depends only on current EV. It is not a martingale and does not
    depend on previous wins or losses.
    """
    value = float(selected_ev)
    if value < SETTINGS.stake_mid_ev:
        return StakeDecision(float(SETTINGS.stake_low), "LOW_NEGATIVE_EV")
    if value < SETTINGS.stake_high_ev:
        return StakeDecision(float(SETTINGS.stake_mid), "MID_NONNEGATIVE_EV")
    return StakeDecision(float(SETTINGS.stake_high), "HIGH_EV")


def should_arm_cooldown(current_loss_streak: int) -> bool:
    trigger = int(SETTINGS.cooldown_loss_streak_trigger)
    return trigger > 0 and current_loss_streak > 0 and current_loss_streak % trigger == 0
