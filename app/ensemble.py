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


def payout_bucket_index(raw_coefficient: float) -> int:
    value = float(raw_coefficient)
    for index, edge in enumerate(settings.payout_bucket_edges):
        if value < edge:
            return index
    return len(settings.payout_bucket_edges)


def payout_bucket_name(index: int) -> str:
    edges = settings.payout_bucket_edges
    if index <= 0:
        return f"< {edges[0]:.2f}"
    if index >= len(edges):
        return f">= {edges[-1]:.2f}"
    return f"{edges[index - 1]:.2f}-{edges[index]:.2f}"


def _bucket_prior(side: str, index: int) -> tuple[float, float]:
    if side == "up":
        initial = settings.payout_bucket_initial_up[index]
        maximum = settings.payout_bucket_max_up[index]
    else:
        initial = settings.payout_bucket_initial_down[index]
        maximum = settings.payout_bucket_max_down[index]
    initial = clip(initial, settings.payout_correction_min, settings.payout_correction_max)
    maximum = clip(maximum, settings.payout_correction_min, settings.payout_correction_max)
    return min(initial, maximum), maximum


def payout_calibration(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Learn payout retention by side and raw-coefficient bucket.

    A single UP/DOWN correction was too optimistic for large T-40 coefficients.
    v1.2 therefore learns five independent bands per side and uses a conservative
    lower quantile. Sparse bands stay close to their safe prior.
    """

    bucket_count = len(settings.payout_bucket_edges) + 1
    min_samples = max(1, settings.payout_bucket_min_samples)
    result: dict[str, Any] = {
        "bucket_edges": list(settings.payout_bucket_edges),
        "minimum_samples_per_bucket": min_samples,
        "lookback": len(history),
    }

    for side in ("up", "down"):
        grouped: list[list[float]] = [[] for _ in range(bucket_count)]
        for row in history:
            raw = row.get(f"raw_expected_coeff_{side}")
            if raw is None:
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
            if not math.isfinite(ratio):
                continue
            grouped[payout_bucket_index(raw_value)].append(clip(ratio, 0.20, 1.50))

        buckets: dict[str, Any] = {}
        weighted_correction = 0.0
        weighted_total = 0
        total_samples = 0
        ready_buckets = 0
        for index, ratios in enumerate(grouped):
            name = payout_bucket_name(index)
            initial, maximum = _bucket_prior(side, index)
            learned = (
                _quantile(ratios, settings.payout_calibration_quantile)
                if ratios
                else initial
            )
            learned = clip(learned, settings.payout_correction_min, maximum)
            readiness = min(1.0, len(ratios) / min_samples)
            correction = initial + (learned - initial) * readiness
            correction = clip(correction, settings.payout_correction_min, maximum)
            ready = len(ratios) >= min_samples
            total_samples += len(ratios)
            ready_buckets += int(ready)
            weight = max(1, len(ratios))
            weighted_correction += correction * weight
            weighted_total += weight
            buckets[name] = {
                "index": index,
                "correction": correction,
                "initial": initial,
                "maximum": maximum,
                "learned_quantile": learned,
                "sample_count": len(ratios),
                "ready": ready,
                "readiness": readiness,
            }

        result[side] = {
            # Compatibility summary for dashboards. Decisions use the bucket value.
            "correction": weighted_correction / max(1, weighted_total),
            "sample_count": total_samples,
            "ready": total_samples >= min_samples,
            "readiness": min(1.0, total_samples / min_samples),
            "ready_buckets": ready_buckets,
            "bucket_count": bucket_count,
            "buckets": buckets,
        }

    result["ready"] = bool(result["up"]["ready"] and result["down"]["ready"])
    return result


def calibration_for_raw(
    calibration: dict[str, Any], side: str, raw_coefficient: float
) -> dict[str, Any]:
    index = payout_bucket_index(raw_coefficient)
    name = payout_bucket_name(index)
    bucket = dict(calibration[side]["buckets"][name])
    bucket["name"] = name
    return bucket


def apply_payout_correction(raw_coefficient: float, correction: float) -> float:
    corrected = float(raw_coefficient) * clip(
        correction, settings.payout_correction_min, settings.payout_correction_max
    )
    return clip(corrected, 1.01, min(settings.payout_cap, raw_coefficient))


def _component_direction(
    probability_up: Optional[float], available: bool
) -> Optional[str]:
    if not available or probability_up is None:
        return None
    margin = max(0.0, settings.fallback_component_margin)
    if probability_up >= 0.5 + margin:
        return "UP"
    if probability_up <= 0.5 - margin:
        return "DOWN"
    return None


def select_side_and_stake(
    probability_up: float,
    coeff_up: float,
    coeff_down: float,
    agreement_up: float,
    state: dict[str, Any],
    payout_ready_up: bool = False,
    payout_ready_down: bool = False,
    crowd_probability_up: Optional[float] = None,
    crowd_available: bool = False,
    binance_probability_up: Optional[float] = None,
    binance_available: bool = False,
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
    crowd_direction = _component_direction(crowd_probability_up, crowd_available)
    binance_direction = _component_direction(binance_probability_up, binance_available)
    consensus_signal = (
        crowd_direction
        if settings.crowd_binance_fallback_enabled
        and crowd_direction is not None
        and crowd_direction == binance_direction
        else None
    )

    def values_for(side: str) -> tuple[float, float]:
        if side == "UP":
            return ev_up, agreement_up
        return ev_down, 1.0 - agreement_up

    signal = ev_signal
    selection_reason = "BEST_CORRECTED_EV"

    # Strong EV may lead. A contrarian EV reversal is held to a stricter bar.
    if ev_selected >= settings.strong_ev_threshold:
        if ev_signal != probability_signal and not (
            ev_selected >= settings.ev_reversal_min_ev
            and ev_agreement >= settings.ev_reversal_min_agreement
        ):
            signal = probability_signal
            selection_reason = "EV_REVERSAL_BLOCKED_PROBABILITY_FALLBACK"
    else:
        # Weak/negative EV: use Crowd+Binance only when both independently agree.
        if consensus_signal is not None:
            signal = consensus_signal
            selection_reason = "WEAK_EV_CROWD_BINANCE_FALLBACK"
        else:
            signal = probability_signal
            selection_reason = (
                "NEGATIVE_EV_PROBABILITY_FALLBACK"
                if ev_selected < 0
                else "WEAK_EV_PROBABILITY_FALLBACK"
            )

    selected_ev, agreement = values_for(signal)
    selected_expected_coeff = coeff_up if signal == "UP" else coeff_down
    selected_payout_ready = payout_ready_up if signal == "UP" else payout_ready_down

    normal_ev_pass = selected_ev > settings.min_trade_ev
    consensus_override_eligible = (
        settings.consensus_override_enabled
        and selection_reason == "WEAK_EV_CROWD_BINANCE_FALLBACK"
        and consensus_signal in {"UP", "DOWN"}
        and consensus_signal == signal
        and selected_expected_coeff >= settings.consensus_override_min_coeff
    )

    trades = int(state.get("trades_count", 0) or 0)
    bank = float(state.get("bank", settings.start_bank) or settings.start_bank)

    negative_fallback_blocked = (
        settings.trade_filter_enabled
        and selection_reason == "NEGATIVE_EV_PROBABILITY_FALLBACK"
        and not settings.negative_fallback_enabled
    )

    trade_executed = True
    no_trade_reason: Optional[str] = None
    if settings.trade_filter_enabled:
        if settings.require_payout_bucket_ready and not selected_payout_ready:
            trade_executed = False
            no_trade_reason = "PAYOUT_BUCKET_NOT_READY"
        elif negative_fallback_blocked:
            trade_executed = False
            no_trade_reason = "NEGATIVE_FALLBACK_DISABLED"
        elif not normal_ev_pass and not consensus_override_eligible:
            trade_executed = False
            no_trade_reason = "EV_NOT_ABOVE_MINIMUM"

    consensus_override_applied = (
        trade_executed
        and settings.trade_filter_enabled
        and not normal_ev_pass
        and consensus_override_eligible
    )

    if consensus_override_applied:
        stake = settings.consensus_override_stake_usd
        quality = "V1_3_3_FIXED_STAKE_CROWD_BINANCE_OVERRIDE"
    else:
        stake = settings.base_stake if trade_executed else 0.0
        quality = (
            "V1_3_3_FIXED_STAKE"
            if trade_executed
            else f"V1_3_3_NO_TRADE_{no_trade_reason}"
        )

    medium_ready = (
        trade_executed
        and settings.variable_stake_enabled
        and trades >= settings.min_trades_medium_stake
        and selected_payout_ready
        and selection_reason == "BEST_CORRECTED_EV"
    )
    high_ready = medium_ready and trades >= settings.min_trades_high_stake

    if (
        high_ready
        and selected_ev >= settings.ev_high_threshold
        and agreement >= settings.high_agreement
    ):
        stake = settings.high_stake
        quality = "HIGH_EDGE"
    elif (
        medium_ready
        and selected_ev >= settings.ev_medium_threshold
        and agreement >= settings.medium_agreement
    ):
        stake = settings.medium_stake
        quality = "MEDIUM_EDGE"
    elif (
        trade_executed
        and not settings.variable_stake_enabled
        and not consensus_override_applied
    ):
        quality = "V1_3_3_FIXED_STAKE"
    elif (
        trade_executed
        and trades < settings.min_trades_medium_stake
        and not consensus_override_applied
    ):
        quality = "WARMUP_FIXED_STAKE"

    if (
        selection_reason != "BEST_CORRECTED_EV"
        and not consensus_override_applied
    ):
        quality += f"_{selection_reason}"

    if trade_executed:
        if bank < 350:
            stake = settings.base_stake
            quality += "_BANK_LT_350"
        elif bank < 400:
            stake = min(stake, settings.medium_stake)
            quality += "_BANK_LT_400"

    return {
        "signal": signal,
        "trade_executed": trade_executed,
        "no_trade_reason": no_trade_reason,
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
        "crowd_binance_consensus": consensus_signal,
        "selected_expected_coeff": selected_expected_coeff,
        "normal_ev_pass": normal_ev_pass,
        "negative_fallback_enabled": settings.negative_fallback_enabled,
        "negative_fallback_blocked": negative_fallback_blocked,
        "consensus_override_eligible": consensus_override_eligible,
        "crowd_binance_override": consensus_override_applied,
        "trade_rule": (
            "CROWD_BINANCE_OVERRIDE"
            if consensus_override_applied
            else (
                "FILTER_DISABLED"
                if trade_executed and not settings.trade_filter_enabled
                else ("NORMAL_EV_FILTER" if trade_executed else "NO_TRADE")
            )
        ),
        "selected_payout_bucket_ready": selected_payout_ready,
        "variable_stake_ready": bool(medium_ready),
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
        # Kept for diagnostics/history; default voting weights are zero in v1.2.
        m9_component(rounds),
        pattern_component(rounds),
    ]
    component_by_name = {item.name: item for item in components}
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
    bucket_up = calibration_for_raw(calibration, "up", raw_coeff_up)
    bucket_down = calibration_for_raw(calibration, "down", raw_coeff_down)
    coeff_up = apply_payout_correction(raw_coeff_up, float(bucket_up["correction"]))
    coeff_down = apply_payout_correction(
        raw_coeff_down, float(bucket_down["correction"])
    )

    crowd = component_by_name["crowd"]
    binance = component_by_name["binance"]
    choice = select_side_and_stake(
        probability_up,
        coeff_up,
        coeff_down,
        agreement_up,
        state,
        payout_ready_up=bool(bucket_up["ready"]),
        payout_ready_down=bool(bucket_down["ready"]),
        crowd_probability_up=crowd.probability_up,
        crowd_available=crowd.available,
        binance_probability_up=binance.probability_up,
        binance_available=binance.available,
    )
    return {
        **choice,
        "raw_expected_coeff_up": raw_coeff_up,
        "raw_expected_coeff_down": raw_coeff_down,
        "payout_correction_up": float(bucket_up["correction"]),
        "payout_correction_down": float(bucket_down["correction"]),
        "payout_bucket_up": str(bucket_up["name"]),
        "payout_bucket_down": str(bucket_down["name"]),
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
            "crowd_binance_consensus": choice["crowd_binance_consensus"],
            "selected_expected_coeff": choice["selected_expected_coeff"],
            "normal_ev_pass": choice["normal_ev_pass"],
            "negative_fallback_enabled": choice["negative_fallback_enabled"],
            "negative_fallback_blocked": choice["negative_fallback_blocked"],
            "consensus_override_eligible": choice["consensus_override_eligible"],
            "crowd_binance_override": choice["crowd_binance_override"],
            "trade_rule": choice["trade_rule"],
            "consensus_override_enabled": settings.consensus_override_enabled,
            "consensus_override_min_coeff": settings.consensus_override_min_coeff,
            "consensus_override_stake_usd": settings.consensus_override_stake_usd,
            "selected_payout_bucket_ready": choice["selected_payout_bucket_ready"],
            "variable_stake_ready": choice["variable_stake_ready"],
            "trade_executed": choice["trade_executed"],
            "no_trade_reason": choice["no_trade_reason"],
            "trade_filter_enabled": settings.trade_filter_enabled,
            "min_trade_ev": settings.min_trade_ev,
            "require_payout_bucket_ready": settings.require_payout_bucket_ready,
            "payout_bucket_up": bucket_up,
            "payout_bucket_down": bucket_down,
            "payout_calibration": calibration,
        },
    }
