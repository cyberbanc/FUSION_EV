from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from . import db
from .config import SETTINGS
from .ensemble import forecast
from .pancake_client import PancakeClient, from_env
from .shadow import evaluate as evaluate_shadow
from .risk import stake_for_ev

_LAST_TICK: dict[str, Any] = {}
_LAST_TICK_LOCK = threading.Lock()
_LAST_SIGNAL_CACHE: dict[str, Any] = {}
_LAST_SIGNAL_CACHE_LOCK = threading.Lock()
_TICK_LOCK = threading.Lock()
_LAST_SYNC_AT = 0.0


def _round_record(round_data) -> dict[str, Any]:
    winner = round_data.actual_winner
    gross = round_data.coefficient_gross(winner) if winner in {"UP", "DOWN"} else None
    return {
        "epoch": round_data.epoch,
        "start_timestamp": round_data.start_timestamp,
        "lock_timestamp": round_data.lock_timestamp,
        "close_timestamp": round_data.close_timestamp,
        "lock_price": round_data.lock_price,
        "close_price": round_data.close_price,
        "lock_oracle_id": round_data.lock_oracle_id,
        "close_oracle_id": round_data.close_oracle_id,
        "total_amount_bnb": round_data.total_amount_bnb,
        "bull_amount_bnb": round_data.bull_amount_bnb,
        "bear_amount_bnb": round_data.bear_amount_bnb,
        "reward_base_bnb": round_data.reward_base_bnb,
        "reward_amount_bnb": round_data.reward_amount_bnb,
        "oracle_called": round_data.oracle_called,
        "actual_winner": winner,
        "winner_coeff_gross": gross,
        "winner_coeff_net": gross * (1 - SETTINGS.treasury_fee) if gross else None,
    }


def bootstrap_rounds(client: PancakeClient) -> dict[str, Any]:
    current = client.current_epoch()
    saved = 0
    start = max(1, current - SETTINGS.bootstrap_lookback)
    for epoch in range(start, max(start, current - 1)):
        try:
            round_data = client.round(epoch)
            if round_data.oracle_called:
                db.upsert_round(_round_record(round_data))
                saved += 1
        except Exception:
            continue
    return {"saved": saved, "current_epoch": current}


def sync_recent_closed(client: PancakeClient, current_epoch: int) -> dict[str, Any] | None:
    global _LAST_SYNC_AT
    now = time.time()
    if now - _LAST_SYNC_AT < SETTINGS.sync_closed_seconds:
        return None
    _LAST_SYNC_AT = now
    saved = 0
    end = current_epoch - 2
    start = max(1, end - SETTINGS.sync_recent_lookback + 1)
    for epoch in range(start, end + 1):
        try:
            round_data = client.round(epoch)
            if round_data.oracle_called:
                db.upsert_round(_round_record(round_data))
                saved += 1
        except Exception:
            continue
    return {"saved": saved, "from_epoch": start, "to_epoch": end}


def settle_finished(client: PancakeClient, current_epoch: int | None = None) -> int:
    current_epoch = current_epoch or client.current_epoch()
    settled_count = 0
    for decision in db.unsettled_decisions(limit=200):
        epoch = int(decision["betting_epoch"])
        if epoch > current_epoch - 2:
            continue
        try:
            round_data = client.round(epoch)
            if not round_data.oracle_called or round_data.actual_winner is None:
                continue
            db.upsert_round(_round_record(round_data))
            winner = round_data.actual_winner
            coeff_up_gross = round_data.coefficient_gross("UP")
            coeff_down_gross = round_data.coefficient_gross("DOWN")
            coeff_up = coeff_up_gross * (1 - SETTINGS.treasury_fee) if coeff_up_gross else None
            coeff_down = coeff_down_gross * (1 - SETTINGS.treasury_fee) if coeff_down_gross else None
            winner_gross = coeff_up_gross if winner == "UP" else coeff_down_gross if winner == "DOWN" else None
            winner_net = coeff_up if winner == "UP" else coeff_down if winner == "DOWN" else None
            signal = str(decision.get("signal") or "")
            trade_executed = bool(decision.get("trade_executed"))
            stake = float(decision.get("stake") or 0.0)
            if winner == "DRAW":
                outcome = "REFUND" if trade_executed else "SKIP"
                pnl = 0.0
                shadow_pnl = 0.0
            elif signal == winner:
                outcome = "WIN" if trade_executed else "SKIP"
                selected_coeff = coeff_up if signal == "UP" else coeff_down
                pnl = stake * ((selected_coeff or 1.0) - 1.0) if trade_executed else 0.0
                shadow_pnl = SETTINGS.shadow_stake * ((selected_coeff or 1.0) - 1.0)
            else:
                outcome = "LOSS" if trade_executed else "SKIP"
                pnl = -stake if trade_executed else 0.0
                shadow_pnl = -SETTINGS.shadow_stake

            probability = (
                float(decision.get("probability_up") or 0.5)
                if signal == "UP"
                else float(decision.get("probability_down") or 0.5)
            )
            selected_final_coeff = coeff_up if signal == "UP" else coeff_down
            actual_ev = probability * selected_final_coeff - 1.0 if selected_final_coeff else None
            raw_expected = (
                float(decision.get("raw_expected_coeff_up") or 0)
                if signal == "UP"
                else float(decision.get("raw_expected_coeff_down") or 0)
            )
            payout_ratio = selected_final_coeff / raw_expected if selected_final_coeff and raw_expected > 0 else None
            move_points = (
                abs(float(round_data.close_price) - float(round_data.lock_price))
                if round_data.close_price is not None and round_data.lock_price is not None
                else None
            )
            changed = db.settle_decision_atomic(
                epoch,
                final_winner=winner,
                final_coeff_gross=winner_gross,
                final_coeff_net=winner_net,
                final_coeff_up=coeff_up,
                final_coeff_down=coeff_down,
                final_move_points=move_points,
                outcome=outcome,
                pnl=pnl,
                shadow_pnl=shadow_pnl,
                actual_ev_signal=actual_ev,
                payout_ratio_signal=payout_ratio,
            )
            if changed:
                settled_count += 1
        except Exception:
            continue
    return settled_count


def create_locked_decision(snapshot) -> dict[str, Any]:
    existing = db.get_decision(snapshot.betting_epoch)
    if existing:
        return existing

    result = forecast(snapshot)
    selected_ready = (
        result.payout_bucket_ready_up if result.signal == "UP" else result.payout_bucket_ready_down
    )
    shadow_allowed, shadow_reason, shadow_stats = evaluate_shadow(
        result.source_key, result.signal, result.selected_ev
    )

    # Cooldown is consumed by the next newly created decision, regardless of
    # whether the model would otherwise trade that round. This implements
    # "three settled real losses -> skip one full decision round".
    cooldown_applied = db.consume_cooldown_round()

    allowed = True
    no_trade_reason: str | None = None
    if cooldown_applied:
        allowed = False
        no_trade_reason = "COOLDOWN_AFTER_3_REAL_LOSSES"
    elif SETTINGS.require_payout_bucket_ready and not selected_ready:
        allowed = False
        no_trade_reason = "PAYOUT_BUCKET_NOT_READY"
    elif SETTINGS.trade_filter_enabled and not shadow_allowed:
        allowed = False
        no_trade_reason = shadow_reason

    stake_decision = stake_for_ev(result.selected_ev)
    if allowed and not stake_decision.eligible:
        allowed = False
        no_trade_reason = "NEGATIVE_EV_DISABLED"

    stake = stake_decision.stake if allowed else 0.0
    stake_tier = stake_decision.tier if allowed else "NO_TRADE"
    trade_executed = allowed and stake_decision.eligible and stake > 0.0
    quality_prefix = "V1_3_6_6_TRADE" if trade_executed else "V1_3_6_6_NO_TRADE"
    decision_quality = f"{quality_prefix}_{no_trade_reason or shadow_reason}_{result.selection_reason}"
    state = db.get_state()
    features = dict(result.features)
    features.update(
        {
            "trade_executed": trade_executed,
            "no_trade_reason": no_trade_reason,
            "trade_rule": "NONNEGATIVE_EV_TIER_ALL_FILTERS_PASSED" if trade_executed else "NO_TRADE",
            "min_trade_ev": SETTINGS.min_trade_ev,
            "shadow_filter_enabled": SETTINGS.shadow_filter_enabled,
            "shadow_allowed": shadow_allowed,
            "shadow_reason": shadow_reason,
            "shadow_stats": shadow_stats,
            "selected_payout_bucket_ready": selected_ready,
            "stake_mode": "nonnegative_ev_10_15",
            "stake_tier": stake_tier,
            "stake_rules": {
                "negative_ev": "NO_TRADE",
                "mid_ev_stake": SETTINGS.stake_mid,
                "high_ev_stake": SETTINGS.stake_high,
                "mid_threshold": SETTINGS.stake_mid_ev,
                "high_threshold": SETTINGS.stake_high_ev,
            },
            "cooldown_applied": cooldown_applied,
            "cooldown_loss_streak_trigger": SETTINGS.cooldown_loss_streak_trigger,
            "cooldown_rounds": SETTINGS.cooldown_rounds,
        }
    )
    return db.insert_decision(
        {
            "betting_epoch": snapshot.betting_epoch,
            "live_epoch": snapshot.live_epoch,
            "locked_at_chain_timestamp": snapshot.chain_timestamp,
            "locked_at_seconds_to_lock": snapshot.seconds_to_lock,
            "signal": result.signal,
            "probability_up": result.probability_up,
            "probability_down": result.probability_down,
            "expected_coeff_up": result.expected_coeff_up,
            "expected_coeff_down": result.expected_coeff_down,
            "ev_up": result.ev_up,
            "ev_down": result.ev_down,
            "selected_ev": result.selected_ev,
            "agreement": result.agreement,
            "decision_quality": decision_quality,
            "stake": stake,
            "bank_before": float(state.get("bank") or SETTINGS.start_bank),
            "components_json": [x.to_dict() for x in result.components],
            "weights_json": result.weights,
            "features_json": features,
            "snapshot_json": snapshot.to_dict(),
            "raw_expected_coeff_up": result.raw_expected_coeff_up,
            "raw_expected_coeff_down": result.raw_expected_coeff_down,
            "payout_correction_up": result.payout_correction_up,
            "payout_correction_down": result.payout_correction_down,
            "strategy_version": SETTINGS.version,
            "payout_bucket_up": result.payout_bucket_up,
            "payout_bucket_down": result.payout_bucket_down,
            "trade_executed": trade_executed,
            "no_trade_reason": no_trade_reason,
            "source_key": result.source_key,
            "selection_reason": result.selection_reason,
            "fib_line": None,
            "fib_index": None,
            "fib_step": None,
            "shadow_allowed": shadow_allowed,
            "shadow_reason": shadow_reason,
            "shadow_stats_json": shadow_stats,
            "stake_mode": "nonnegative_ev_10_15",
            "stake_tier": stake_tier,
            "cooldown_applied": cooldown_applied,
        }
    )


def tick() -> dict[str, Any]:
    # The background worker and a manual /signal request may call tick at the
    # same time. Serializing the whole cycle prevents duplicate RPC work and
    # overlapping settlement/snapshot operations. Database uniqueness remains
    # the final safety layer.
    with _TICK_LOCK:
        started = time.time()
        client = from_env()
        snapshot = client.market_snapshot()
        settled = settle_finished(client, snapshot.current_epoch)
        sync = sync_recent_closed(client, snapshot.current_epoch)
        snapshot_saved = False
        if SETTINGS.prelock_seconds <= snapshot.seconds_to_lock <= SETTINGS.snapshot_start_seconds:
            snapshot_saved = db.save_snapshot(
                {
                    **snapshot.to_dict(),
                    "bull_amount_bnb": snapshot.betting_round.bull_amount_bnb,
                    "bear_amount_bnb": snapshot.betting_round.bear_amount_bnb,
                }
            )
        decision = db.get_decision(snapshot.betting_epoch)
        created = False
        if decision is None and snapshot.decision_window:
            decision = create_locked_decision(snapshot)
            created = True
        result = {
            "ok": True,
            "message": "tick_complete",
            "chain_timestamp": snapshot.chain_timestamp,
            "current_epoch": snapshot.current_epoch,
            "betting_epoch": snapshot.betting_epoch,
            "live_epoch": snapshot.live_epoch,
            "seconds_to_lock": snapshot.seconds_to_lock,
            "decision_window": snapshot.decision_window,
            "snapshot_saved": snapshot_saved,
            "decision_locked": decision is not None,
            "trade_executed": decision.get("trade_executed") if decision else None,
            "decision_signal": decision.get("signal") if decision else None,
            "no_trade_reason": decision.get("no_trade_reason") if decision else None,
            "stake": float(decision.get("stake") or 0) if decision else 0.0,
            "stake_mode": "nonnegative_ev_10_15",
            "stake_tier": decision.get("stake_tier") if decision else None,
            "stake_rules": {
                "negative_ev": "NO_TRADE",
                "mid_ev_stake": SETTINGS.stake_mid,
                "high_ev_stake": SETTINGS.stake_high,
                "mid_ev_threshold": SETTINGS.stake_mid_ev,
                "high_ev_threshold": SETTINGS.stake_high_ev,
            },
            "settled_now": settled,
            "sync": sync,
            "rpc": client.rpc_status(),
            "created_or_existing_decision": created,
            "duration_ms": round((time.time() - started) * 1000, 2),
            "updated_at": int(time.time()),
        }
        with _LAST_TICK_LOCK:
            _LAST_TICK.clear()
            _LAST_TICK.update(result)
        # Cache the complete live snapshot and locked decision for the public
        # /signal endpoint. Reading this cache is immediate and never starts
        # another BSC/Chainlink/Pancake RPC cycle.
        with _LAST_SIGNAL_CACHE_LOCK:
            _LAST_SIGNAL_CACHE.clear()
            _LAST_SIGNAL_CACHE.update(
                {
                    "ok": True,
                    "snapshot": snapshot.to_dict(),
                    "decision": dict(decision) if decision else None,
                    "worker_tick": dict(result),
                    "updated_at": int(time.time()),
                }
            )
        return result


def signal_cache() -> dict[str, Any]:
    """Return the most recent worker calculation without performing RPC work."""
    with _LAST_SIGNAL_CACHE_LOCK:
        return dict(_LAST_SIGNAL_CACHE)


def status() -> dict[str, Any]:
    with _LAST_TICK_LOCK:
        last_tick = dict(_LAST_TICK)
    state = db.get_state() if db.enabled() else {}
    return {
        "enabled": SETTINGS.worker_enabled,
        "strategy": "m9_fusion_ev_nonnegative_ev_10_15_shadow_quality_cooldown",
        "version": SETTINGS.version,
        "stake_mode": "nonnegative_ev_10_15",
        "stake_rules": {
            "negative_ev": "NO_TRADE",
            "mid_ev_stake": SETTINGS.stake_mid,
            "high_ev_stake": SETTINGS.stake_high,
            "mid_ev_threshold": SETTINGS.stake_mid_ev,
            "high_ev_threshold": SETTINGS.stake_high_ev,
            "still_requires_payout_shadow_quality_cooldown": True,
        },
        "shadow_filter_enabled": SETTINGS.shadow_filter_enabled,
        "shadow_recent_window": SETTINGS.shadow_recent_window,
        "shadow_recent_min_pnl": SETTINGS.shadow_recent_min_pnl,
        "quality_window": SETTINGS.quality_window,
        "quality_min_samples": SETTINGS.quality_min_samples,
        "quality_min_win_rate": SETTINGS.quality_min_win_rate,
        "quality_min_profit_factor": SETTINGS.quality_min_profit_factor,
        "cooldown_loss_streak_trigger": SETTINGS.cooldown_loss_streak_trigger,
        "cooldown_rounds": SETTINGS.cooldown_rounds,
        "cooldown_rounds_remaining": int(state.get("cooldown_rounds_remaining") or 0),
        "require_payout_bucket_ready": SETTINGS.require_payout_bucket_ready,
        "last_tick": last_tick,
    }


async def loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
        except Exception as exc:
            error_result = {
                "ok": False,
                "message": "tick_error",
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": int(time.time()),
            }
            with _LAST_TICK_LOCK:
                _LAST_TICK.clear()
                _LAST_TICK.update(error_result)
            with _LAST_SIGNAL_CACHE_LOCK:
                # Keep the previous good snapshot/decision when available,
                # but expose the latest worker error to the dashboard.
                _LAST_SIGNAL_CACHE["worker_tick"] = dict(error_result)
                _LAST_SIGNAL_CACHE["error"] = error_result["error"]
                _LAST_SIGNAL_CACHE["updated_at"] = error_result["updated_at"]
        try:
            await asyncio.wait_for(stop.wait(), timeout=SETTINGS.poll_seconds)
        except asyncio.TimeoutError:
            pass
