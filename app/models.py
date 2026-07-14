from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class RoundData:
    epoch: int
    start_timestamp: int
    lock_timestamp: int
    close_timestamp: int
    lock_price: Optional[float]
    close_price: Optional[float]
    total_amount_bnb: float
    bull_amount_bnb: float
    bear_amount_bnb: float
    reward_base_bnb: float
    reward_amount_bnb: float
    oracle_called: bool
    actual_winner: Optional[str]
    winner_coeff_gross: Optional[float]
    winner_coeff_net: Optional[float]
    move_points: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FusionSnapshot:
    chain_timestamp: int
    current_epoch: int
    betting_epoch: int
    live_epoch: int
    seconds_to_lock: int
    decision_window: bool
    safe_to_decide: bool
    chainlink_price: float
    oracle_updated_at: int
    oracle_age_seconds: int
    oracle_round_id: int
    live_lock_price: Optional[float]
    live_move_signed: Optional[float]
    live_move_points: Optional[float]
    provisional_winner: Optional[str]
    betting_total_bnb: float
    betting_bull_bnb: float
    betting_bear_bnb: float
    betting_bull_share_pct: float
    betting_bear_share_pct: float
    current_gross_coeff_up: Optional[float]
    current_gross_coeff_down: Optional[float]
    current_net_coeff_up: Optional[float]
    current_net_coeff_down: Optional[float]
    live_round: dict[str, Any]
    betting_round: dict[str, Any]
    rpc_status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComponentSignal:
    name: str
    probability_up: float
    reliability: float
    available: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
