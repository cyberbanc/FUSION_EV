from __future__ import annotations

import math
import statistics
from typing import Any, Optional

from .config import settings
from .models import ComponentSignal, FusionSnapshot


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _tanh(value: float) -> float:
    return math.tanh(value) if math.isfinite(value) else 0.0


def _quantile(values: list[float], q: float) -> float:
    """Linear quantile without a numpy dependency."""
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = clip(q, 0.0, 1.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def coefficient_zone(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 1.6:
        return "<1.6"
    if value < 1.8:
        return "1.6-1.8"
    if value < 2.0:
        return "1.8-2.0"
    return ">=2.0"


def typical_move(rounds: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> float:
    prior = [
        abs(float(row.get("live_move_points") or 0))
        for row in snapshots
        if row.get("live_move_points")
    ]
    if len(prior) >= 3:
        return max(0.03, statistics.median(prior))
    moves = [float(row.get("move_points") or 0) for row in rounds[-100:] if row.get("move_points")]
    if moves:
        return max(0.03, statistics.median(moves) * math.sqrt(40.0 / 300.0))
    return 0.15


def price_component(
    snapshot: FusionSnapshot,
    same_epoch_snapshots: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
) -> ComponentSignal:
    if snapshot.live_move_signed is None:
        return ComponentSignal("price", 0.5, 0.0, False, "live_lock_price_missing")
    scale = typical_move(rounds, [])
    move_norm = snapshot.live_move_signed / scale
    ordered = sorted(
        same_epoch_snapshots,
        key=lambda row: int(row.get("seconds_to_lock", 0)),
        reverse=True,
    )
    momentum = 0.0
    acceleration = 0.0
    if ordered:
        oldest_price = float(ordered[0].get("chainlink_price") or snapshot.chainlink_price)
        momentum = (snapshot.chainlink_price - oldest_price) / scale
    if len(ordered) >= 3:
        middle = ordered[len(ordered) // 2]
        middle_price = float(middle.get("chainlink_price") or snapshot.chainlink_price)
        first_leg = (
            middle_price - float(ordered[0].get("chainlink_price") or middle_price)
        ) / scale
        second_leg = (snapshot.chainlink_price - middle_price) / scale
        acceleration = second_leg - first_leg
    score = (
        0.65 * _tanh(move_norm / 1.4)
        + 0.25 * _tanh(momentum / 1.2)
        + 0.10 * _tanh(acceleration)
    )
    probability = clip(0.5 + 0.15 * score, 0.35, 0.65)
    freshness = clip(1.0 - max(0, snapshot.oracle_age_seconds - 20) / 80.0, 0.25, 1.0)
    return ComponentSignal(
        "price",
        probability,
        freshness,
        True,
        "chainlink_live_move_momentum",
        {
            "typical_move": scale,
            "move_norm": move_norm,
            "momentum_norm": momentum,
            "acceleration": acceleration,
        },
    )


def binance_component(data: dict[str, Any]) -> ComponentSignal:
    if not data.get("available"):
        return ComponentSignal(
            "binance", 0.5, 0.0, False, str(data.get("reason", "unavailable")), data
        )
    returns = data.get("returns_bp") or {}
    r15 = float(returns.get("15") or 0.0)
    r30 = float(returns.get("30") or 0.0)
    r60 = float(returns.get("60") or 0.0)
    taker = float(data.get("taker_imbalance_60s") or 0.0)
    book = float(data.get("book_imbalance_top20") or 0.0)
    ema1 = float(data.get("ema_spread_1m_bp") or 0.0)
    ema5 = float(data.get("ema_spread_5m_bp") or 0.0)
    score = (
        0.28 * _tanh(r15 / 8.0)
        + 0.22 * _tanh(r30 / 12.0)
        + 0.15 * _tanh(r60 / 18.0)
        + 0.15 * _tanh(taker * 2.5)
        + 0.10 * _tanh(book * 2.0)
        + 0.06 * _tanh(ema1 / 8.0)
        + 0.04 * _tanh(ema5 / 12.0)
    )
    probability = clip(0.5 + 0.12 * score, 0.38, 0.62)
    return ComponentSignal("binance", probability, 1.0, True, "binance_microstructure", data)


def crowd_component(
    snapshot: FusionSnapshot, same_epoch_snapshots: list[dict[str, Any]]
) -> ComponentSignal:
    imbalance = (snapshot.betting_bull_share_pct - snapshot.betting_bear_share_pct) / 100.0
    share_change = 0.0
    pool_growth = 0.0
    if same_epoch_snapshots:
        oldest = max(
            same_epoch_snapshots, key=lambda row: int(row.get("seconds_to_lock", 0))
        )
        share_change = (
            snapshot.betting_bull_share_pct
            - float(oldest.get("betting_bull_share_pct") or 50.0)
        ) / 100.0
        old_total = float(oldest.get("betting_total_bnb") or 0.0)
        if old_total > 0:
            pool_growth = snapshot.betting_total_bnb / old_total - 1.0
    price_sign = 0.0
    if snapshot.live_move_signed:
        price_sign = 1.0 if snapshot.live_move_signed > 0 else -1.0
    if abs(imbalance) > 0.70:
        base = -0.15 * imbalance
        mode = "extreme_crowd_mild_contrarian"
    else:
        base = 0.28 * imbalance
        mode = "moderate_crowd_mild_follow"
    alignment = 0.12 * price_sign if imbalance * price_sign > 0 else -0.05 * price_sign
    score = base + 0.35 * share_change + alignment
    probability = clip(0.5 + 0.10 * _tanh(score * 2.5), 0.43, 0.57)
    pool_reliability = clip(
        snapshot.betting_total_bnb / max(settings.min_pool_bnb, 0.01), 0.15, 1.0
    )
    return ComponentSignal(
        "crowd",
        probability,
        pool_reliability,
        True,
        mode,
        {
            "imbalance": imbalance,
            "bull_share_change": share_change,
            "pool_growth": pool_growth,
            "price_sign": price_sign,
        },
    )


def m9_component(rounds: list[dict[str, Any]]) -> ComponentSignal:
    if len(rounds) < 9:
        return ComponentSignal(
            "m9", 0.5, 0.0, False, "insufficient_history", {"rounds": len(rounds)}
        )
    suffix = tuple(str(row["actual_winner"]) for row in rounds[-4:])
    zone = coefficient_zone(rounds[-1].get("winner_coeff_gross"))
    up_weight = 5.0
    down_weight = 5.0
    matches = 0
    total = len(rounds)
    for idx in range(4, total):
        key = tuple(str(row["actual_winner"]) for row in rounds[idx - 4 : idx])
        prior_zone = coefficient_zone(rounds[idx - 1].get("winner_coeff_gross"))
        if key != suffix or prior_zone != zone:
            continue
        age = total - idx
        weight = math.exp(-age / 500.0)
        if rounds[idx]["actual_winner"] == "UP":
            up_weight += weight
        else:
            down_weight += weight
        matches += 1
    probability = up_weight / (up_weight + down_weight)
    reliability = clip(matches / 50.0, 0.10, 1.0) if matches else 0.10
    return ComponentSignal(
        "m9",
        clip(probability, 0.38, 0.62),
        reliability,
        True,
        "bayesian_exact_state_with_decay",
        {
            "state": list(suffix),
            "coefficient_zone": zone,
            "matches": matches,
            "up_weight": up_weight,
            "down_weight": down_weight,
        },
    )


def pattern_component(rounds: list[dict[str, Any]]) -> ComponentSignal:
    max_length = min(settings.pattern_max_length, 5)
    best: Optional[dict[str, Any]] = None
    winners = [str(row["actual_winner"]) for row in rounds]
    for length in range(3, max_length + 1):
        if len(winners) <= length:
            continue
        suffix = tuple(winners[-length:])
        outcomes: list[tuple[int, str]] = []
        for idx in range(length, len(winners)):
            if tuple(winners[idx - length : idx]) == suffix:
                outcomes.append((idx, winners[idx]))
        count = len(outcomes)
        if count < settings.pattern_min_count:
            continue
        midpoint = len(winners) // 2
        first = [outcome for idx, outcome in outcomes if idx < midpoint]
        second = [outcome for idx, outcome in outcomes if idx >= midpoint]
        if not first or not second:
            continue
        p_first = first.count("UP") / len(first)
        p_second = second.count("UP") / len(second)
        if (p_first - 0.5) * (p_second - 0.5) <= 0:
            continue
        up = 5.0 + sum(
            math.exp(-(len(winners) - idx) / 500.0)
            for idx, outcome in outcomes
            if outcome == "UP"
        )
        down = 5.0 + sum(
            math.exp(-(len(winners) - idx) / 500.0)
            for idx, outcome in outcomes
            if outcome == "DOWN"
        )
        probability = up / (up + down)
        score = abs(probability - 0.5) * math.sqrt(count)
        candidate = {
            "length": length,
            "pattern": list(suffix),
            "count": count,
            "probability": probability,
            "first_half_probability": p_first,
            "second_half_probability": p_second,
            "score": score,
        }
        if best is None or score > best["score"]:
            best = candidate
    if best is None:
        return ComponentSignal("pattern", 0.5, 0.0, False, "no_stable_pattern")
    reliability = clip(best["count"] / 80.0, 0.20, 1.0)
    return ComponentSignal(
        "pattern",
        clip(float(best["probability"]), 0.38, 0.62),
        reliability,
        True,
        "stable_multilength_pattern",
        best,
    )


def adaptive_weights(
    components: list[ComponentSignal], settled_history: list[dict[str, Any]]
) -> dict[str, float]:
    base = {
        "price": settings.weight_price,
        "binance": settings.weight_binance,
        "crowd": settings.weight_crowd,
        "m9": settings.weight_m9,
        "pattern": settings.weight_pattern,
    }
    briers: dict[str, list[float]] = {key: [] for key in base}
    for row in settled_history:
        actual = 1.0 if row.get("final_winner") == "UP" else 0.0
        for item in row.get("components_json") or []:
            name = item.get("name")
            if name in briers and item.get("available"):
                p = float(item.get("probability_up", 0.5))
                briers[name].append((p - actual) ** 2)
    current = {item.name: item for item in components}
    raw: dict[str, float] = {}
    for name, base_weight in base.items():
        component = current.get(name)
        if component is None or not component.available or component.reliability <= 0:
            raw[name] = 0.0
            continue
        scores = briers[name]
        if scores:
            brier = sum(scores) / len(scores)
            quality = clip(1.15 - 2.0 * (brier - 0.25), 0.50, 1.25)
            sample_factor = min(1.0, len(scores) / 100.0)
            quality = 1.0 + (quality - 1.0) * sample_factor
        else:
            quality = 1.0
        raw[name] = base_weight * component.reliability * quality
    total = sum(raw.values())
    if total <= 0:
        return {
            "price": 1.0,
            "binance": 0.0,
            "crowd": 0.0,
            "m9": 0.0,
            "pattern": 0.0,
        }
    return {name: value / total for name, value in raw.items()}


def raw_conservative_coefficient(
    current: Optional[float], total_pool: float, side_pool: float
) -> float:
    """Pool-based estimate before adaptive final-payout calibration."""
    neutral = settings.neutral_net_coefficient
    if current is None or current <= 1.0:
        return neutral
    pool_confidence = clip(
        total_pool / max(settings.min_pool_bnb, 0.01), 0.0, 1.0
    )
    side_confidence = clip(
        side_pool / max(settings.min_side_pool_bnb, 0.005), 0.0, 1.0
    )
    confidence = pool_confidence * side_confidence
    blended = neutral + (min(float(current), settings.payout_cap) - neutral) * confidence
    conservative = 1.0 + (blended - 1.0) * settings.payout_haircut
    return clip(conservative, 1.01, settings.payout_cap)


def payout_calibration(
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Learn conservative side-specific correction from completed rounds.

    Each historical row compares the raw coefficient available at decision time
    with the coefficient implied by the final PancakeSwap pools. The lower
    quantile is intentionally used so occasional very favourable payouts do not
    make the forecast optimistic.
    """

    result: dict[str, Any] = {}
    min_samples = max(1, settings.payout_calibration_min_samples)
    initial = clip(
        settings.payout_initial_correction,
        settings.payout_correction_min,
        settings.payout_correction_max,
    )

    for side in ("up", "down"):
        ratios: list[float] = []
        for row in history:
            raw = row.get(f"raw_expected_coeff_{side}")
            if raw is None:
                # Compatibility with v1.0.x rows: old expected coefficient was
                # the raw estimate because adaptive correction did not exist.
                raw = row.get(f"expected_coeff_{side}")
            final = row.get(f"final_coeff_{side}")
            try:
                raw_value = float(raw)
                final_value = float(final)
            except (TypeError, ValueError):
                continue
            if raw_value <= 1.0 or final_value <= 1.0:
                continue
            ratio = final_value / raw_value
            if math.isfinite(ratio):
                ratios.append(clip(ratio, 0.25, 1.50))

        learned = (
            _quantile(ratios, settings.payout_calibration_quantile)
            if ratios
            else initial
        )
        learned = clip(
            learned, settings.payout_correction_min, settings.payout_correction_max
        )
        readiness = min(1.0, len(ratios) / min_samples)
        correction = initial + (learned - initial) * readiness
        correction = clip(
            correction, settings.payout_correction_min, settings.payout_correction_max
        )
        result[side] = {
            "correction": correction,
            "learned_quantile": learned,
            "sample_count": len(ratios),
            "ready": len(ratios) >= min_samples,
            "readiness": readiness,
        }

    result["ready"] = bool(result["up"]["ready"] and result["down"]["ready"])
    result["minimum_samples"] = min_samples
    result["lookback"] = len(history)
    return result


def apply_payout_correction(raw_coefficient: float, correction: float) -> float:
    """Apply correction to the whole coefficient, never below 1.01."""
    corrected = float(raw_coefficient) * clip(
        correction, settings.payout_correction_min, settings.payout_correction_max
    )
    return clip(corrected, 1.01, min(settings.payout_cap, raw_coefficient))


def select_side_and_stake(
    probability_up: float,
    coeff_up: float,
    coeff_down: float,
    agreement_up: float,
    state: dict[str, Any],
    payout_calibration_ready: bool = False,
) -> dict[str, Any]:
    probability_down = 1.0 - probability_up
    ev_up = probability_up * coeff_up - 1.0
    ev_down = probability_down * coeff_down - 1.0

    if ev_up >= ev_down:
        ev_signal = "UP"
        ev_selected = ev_up
        ev_agreement = agreement_up
    else:
        ev_signal = "DOWN"
        ev_selected = ev_down
        ev_agreement = 1.0 - agreement_up

    probability_signal = "UP" if probability_up >= probability_down else "DOWN"
    probability_signal_ev = ev_up if probability_signal == "UP" else ev_down
    probability_signal_agreement = (
        agreement_up if probability_signal == "UP" else 1.0 - agreement_up
    )

    signal = ev_signal
    selected_ev = ev_selected
    agreement = ev_agreement
    selection_reason = "BEST_CORRECTED_EV"

    if settings.negative_ev_probability_fallback and ev_selected < 0:
        signal = probability_signal
        selected_ev = probability_signal_ev
        agreement = probability_signal_agreement
        selection_reason = "NEGATIVE_EV_PROBABILITY_FALLBACK"
    elif (
        ev_signal != probability_signal
        and ev_agreement < settings.low_agreement_threshold
        and ev_selected < settings.low_agreement_min_ev
    ):
        signal = probability_signal
        selected_ev = probability_signal_ev
        agreement = probability_signal_agreement
        selection_reason = "LOW_AGREEMENT_PROBABILITY_FALLBACK"

    trades = int(state.get("trades_count", 0) or 0)
    bank = float(state.get("bank", settings.start_bank) or settings.start_bank)
    stake = settings.base_stake
    quality = "FORCED_MINIMUM" if selected_ev < 0 else "LOW_EDGE"

    variable_stake_ready = (
        trades >= settings.min_trades_variable_stake and payout_calibration_ready
    )
    if variable_stake_ready:
        if selected_ev >= settings.ev_high_threshold and agreement >= settings.high_agreement:
            stake = settings.high_stake
            quality = "HIGH_EDGE"
        elif (
            selected_ev >= settings.ev_medium_threshold
            and agreement >= settings.medium_agreement
        ):
            stake = settings.medium_stake
            quality = "MEDIUM_EDGE"
    else:
        if trades < settings.min_trades_variable_stake:
            quality = "WARMUP_FIXED_STAKE" if selected_ev >= 0 else "WARMUP_FORCED_MINIMUM"
        else:
            quality = (
                "PAYOUT_CALIBRATION_FIXED_STAKE"
                if selected_ev >= 0
                else "PAYOUT_CALIBRATION_FORCED_MINIMUM"
            )

    if selection_reason != "BEST_CORRECTED_EV":
        quality += f"_{selection_reason}"

    if bank < 350:
        stake = settings.base_stake
        quality += "_BANK_LT_350"
    elif bank < 400:
        stake = min(stake, settings.medium_stake)
        quality += "_BANK_LT_400"

    return {
        "signal": signal,
        "probability_up": probability_up,
        "probability_down": probability_down,
        "ev_up": ev_up,
        "ev_down": ev_down,
        "selected_ev": selected_ev,
        "agreement": agreement,
        "stake": stake,
        "decision_quality": quality,
        "selection_reason": selection_reason,
        "ev_candidate_signal": ev_signal,
        "probability_signal": probability_signal,
        "variable_stake_ready": variable_stake_ready,
    }


def build_decision(
    snapshot: FusionSnapshot,
    same_epoch_snapshots: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    binance_data: dict[str, Any],
    state: dict[str, Any],
    settled_history: list[dict[str, Any]],
    payout_history: list[dict[str, Any]],
) -> dict[str, Any]:
    components = [
        price_component(snapshot, same_epoch_snapshots, rounds),
        binance_component(binance_data),
        crowd_component(snapshot, same_epoch_snapshots),
        m9_component(rounds),
        pattern_component(rounds),
    ]
    weights = adaptive_weights(components, settled_history)
    raw_probability = sum(
        weights.get(item.name, 0.0) * item.probability_up for item in components
    )
    probability_up = 0.5 + (raw_probability - 0.5) * settings.probability_shrink
    probability_up = clip(probability_up, 0.35, 0.65)
    agreement_up = sum(
        weights.get(item.name, 0.0)
        for item in components
        if item.available and item.probability_up >= 0.5
    )

    raw_coeff_up = raw_conservative_coefficient(
        snapshot.current_net_coeff_up,
        snapshot.betting_total_bnb,
        snapshot.betting_bull_bnb,
    )
    raw_coeff_down = raw_conservative_coefficient(
        snapshot.current_net_coeff_down,
        snapshot.betting_total_bnb,
        snapshot.betting_bear_bnb,
    )
    calibration = payout_calibration(payout_history)
    coeff_up = apply_payout_correction(
        raw_coeff_up, float(calibration["up"]["correction"])
    )
    coeff_down = apply_payout_correction(
        raw_coeff_down, float(calibration["down"]["correction"])
    )

    choice = select_side_and_stake(
        probability_up,
        coeff_up,
        coeff_down,
        agreement_up,
        state,
        payout_calibration_ready=bool(calibration["ready"]),
    )
    return {
        **choice,
        "raw_expected_coeff_up": raw_coeff_up,
        "raw_expected_coeff_down": raw_coeff_down,
        "payout_correction_up": float(calibration["up"]["correction"]),
        "payout_correction_down": float(calibration["down"]["correction"]),
        "expected_coeff_up": coeff_up,
        "expected_coeff_down": coeff_down,
        "components": [item.to_dict() for item in components],
        "weights": weights,
        "features": {
            "raw_probability_up": raw_probability,
            "probability_shrink": settings.probability_shrink,
            "agreement_up": agreement_up,
            "binance_available": bool(binance_data.get("available")),
            "history_rounds": len(rounds),
            "same_epoch_snapshots": len(same_epoch_snapshots),
            "selection_reason": choice["selection_reason"],
            "ev_candidate_signal": choice["ev_candidate_signal"],
            "probability_signal": choice["probability_signal"],
            "variable_stake_ready": choice["variable_stake_ready"],
            "payout_calibration": calibration,
        },
    }
