from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from . import db
from .binance_client import BinanceClient
from .config import settings
from .ensemble import build_decision
from .pancake_client import from_env

_LAST: dict[str, Any] = {"ok": None, "message": "not_started"}
_LAST_SYNC = 0.0
_BOOTSTRAPPED = False


def status() -> dict[str, Any]:
    return dict(_LAST)


def bootstrap() -> dict[str, Any]:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return {"bootstrapped": True, "skipped": True}
    client = from_env()
    rows = client.closed_rounds(settings.bootstrap_lookback)
    saved = db.upsert_rounds(rows, source="bootstrap")
    _BOOTSTRAPPED = True
    return {"bootstrapped": True, "saved": saved, "lookback": settings.bootstrap_lookback}


def sync_recent(client=None) -> dict[str, Any]:
    client = client or from_env()
    current = client.current_epoch()
    rows = []
    start = max(1, current - settings.sync_recent_lookback)
    for epoch in range(start, current - 1):
        try:
            row = client.round(epoch)
        except Exception:
            continue
        if row.oracle_called and row.actual_winner in {"UP", "DOWN", "DRAW"}:
            rows.append(row)
    saved = db.upsert_rounds(rows, source="continuous_sync") if rows else 0
    return {"saved": saved, "current_epoch": current}


def settle_pending(client=None) -> int:
    client = client or from_env()
    settled = 0
    for decision in db.unsettled_decisions(80):
        epoch = int(decision["betting_epoch"])
        try:
            row = client.round(epoch)
        except Exception:
            continue
        if not row.oracle_called or row.actual_winner not in {"UP", "DOWN", "DRAW"}:
            continue
        db.upsert_rounds([row], source="settlement")
        if db.settle_decision_atomic(epoch, row):
            settled += 1
    return settled


def create_decision(snapshot, binance_data: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    existing = db.get_decision(snapshot.betting_epoch)
    if existing:
        return existing
    if not snapshot.decision_window:
        return None
    state = db.get_state()
    if binance_data is None:
        binance_data = BinanceClient().snapshot()
    db.save_snapshot(snapshot, binance_data)
    same_epoch_snapshots = db.load_snapshots(snapshot.betting_epoch)
    rounds = db.load_rounds(settings.m9_history_limit)
    settled_history = db.settled_component_history(settings.adaptive_weight_lookback)
    payout_history = db.payout_calibration_history(settings.payout_calibration_lookback)
    model = build_decision(
        snapshot=snapshot,
        same_epoch_snapshots=same_epoch_snapshots,
        rounds=rounds,
        binance_data=binance_data,
        state=state,
        settled_history=settled_history,
        payout_history=payout_history,
    )
    data = {
        "betting_epoch": snapshot.betting_epoch,
        "live_epoch": snapshot.live_epoch,
        "locked_at_chain_timestamp": snapshot.chain_timestamp,
        "locked_at_seconds_to_lock": snapshot.seconds_to_lock,
        # Kept as bank_before for backward API compatibility. Semantically
        # this is the bank visible when the decision was created.
        "bank_before": float(state.get("bank", settings.start_bank)),
        "snapshot": snapshot.to_dict(),
        **model,
    }
    db.insert_decision(data)
    return db.get_decision(snapshot.betting_epoch)


def tick() -> dict[str, Any]:
    global _LAST, _LAST_SYNC
    client = from_env()
    if not _BOOTSTRAPPED:
        bootstrap_result = bootstrap()
    else:
        bootstrap_result = None
    settled = settle_pending(client)
    now = time.time()
    sync_result = None
    if now - _LAST_SYNC >= settings.sync_closed_seconds:
        sync_result = sync_recent(client)
        _LAST_SYNC = now
    snapshot = client.snapshot()
    saved_snapshot = False
    decision = db.get_decision(snapshot.betting_epoch)
    if 0 < snapshot.seconds_to_lock <= settings.snapshot_start_seconds:
        db.save_snapshot(snapshot)
        saved_snapshot = True
    if decision is None and snapshot.decision_window:
        decision = create_decision(snapshot)
    _LAST = {
        "ok": True,
        "message": "tick_complete",
        "chain_timestamp": snapshot.chain_timestamp,
        "betting_epoch": snapshot.betting_epoch,
        "seconds_to_lock": snapshot.seconds_to_lock,
        "snapshot_saved": saved_snapshot,
        "decision_locked": bool(decision),
        "decision_signal": decision.get("signal") if decision else None,
        "settled_now": settled,
        "bootstrap": bootstrap_result,
        "sync": sync_result,
        "rpc": snapshot.rpc_status,
        "updated_at": int(time.time()),
    }
    return dict(_LAST)


async def loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
        except Exception as exc:
            global _LAST
            _LAST = {
                "ok": False,
                "message": "tick_error",
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": int(time.time()),
            }
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
        except asyncio.TimeoutError:
            pass
