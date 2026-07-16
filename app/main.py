from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import __version__, db
from .config import settings
from .ensemble import payout_calibration
from .pancake_client import from_env
from .worker import create_decision, loop, settle_pending, status as worker_status, sync_recent, tick

_STOP: Optional[asyncio.Event] = None
_TASK: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _STOP, _TASK
    if not db.enabled():
        raise RuntimeError("DATABASE_URL is required for M9 FUSION EV")
    db.init_db()
    if settings.worker_enabled:
        _STOP = asyncio.Event()
        _TASK = asyncio.create_task(loop(_STOP))
    yield
    if _STOP is not None:
        _STOP.set()
    if _TASK is not None:
        try:
            await asyncio.wait_for(_TASK, timeout=5)
        except Exception:
            _TASK.cancel()


app = FastAPI(title="M9 FUSION EV", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "m9-fusion-ev-paper-bot",
        "version": __version__,
        "mode": "PAPER",
        "strategy": "selective EV filter plus Crowd+Binance consensus override",
        "decision_lock": f"T-{settings.prelock_seconds}",
        "stake_mode": [settings.base_stake] if not settings.variable_stake_enabled else [settings.base_stake, settings.medium_stake, settings.high_stake],
        "variable_stake_enabled": settings.variable_stake_enabled,
        "trade_filter_enabled": settings.trade_filter_enabled,
        "min_trade_ev": settings.min_trade_ev,
        "require_payout_bucket_ready": settings.require_payout_bucket_ready,
        "consensus_override_enabled": settings.consensus_override_enabled,
        "consensus_override_min_coeff": settings.consensus_override_min_coeff,
        "consensus_override_stake_usd": settings.consensus_override_stake_usd,
        "real_transactions": False,
        "urls": {
            "health": "/health",
            "signal": "/signal",
            "status": "/status?limit=30",
            "full_history_json": "/status?history=all&limit=100000",
            "history": "/history?limit=1000",
            "csv": "/history/export.csv",
            "model_performance": "/model/performance",
            "payout_calibration": "/payout/calibration",
            "strategy_performance": "/strategy/performance",
        },
    }


@app.get("/health")
def health():
    client = from_env()
    return {
        "ok": True,
        "service": "m9-fusion-ev-paper-bot",
        "version": __version__,
        "database_enabled": db.enabled(),
        "pancake_connected": client.is_connected(),
        "rpc": client.rpc_status(),
        "worker_enabled": settings.worker_enabled,
        "variable_stake_enabled": settings.variable_stake_enabled,
        "strong_ev_threshold": settings.strong_ev_threshold,
        "ev_reversal_min_ev": settings.ev_reversal_min_ev,
        "ev_reversal_min_agreement": settings.ev_reversal_min_agreement,
        "trade_filter_enabled": settings.trade_filter_enabled,
        "min_trade_ev": settings.min_trade_ev,
        "require_payout_bucket_ready": settings.require_payout_bucket_ready,
        "consensus_override_enabled": settings.consensus_override_enabled,
        "consensus_override_min_coeff": settings.consensus_override_min_coeff,
        "consensus_override_stake_usd": settings.consensus_override_stake_usd,
        "worker": worker_status(),
        "data_sources": [
            "PancakeSwap Prediction rounds and pools",
            "Chainlink latestRoundData from Prediction oracle",
            "Binance public market-data REST when available",
            "Bayesian M9 diagnostics (zero voting weight by default)",
            "pattern diagnostics (zero voting weight by default)",
        ],
    }


@app.get("/signal")
def signal(auto_lock: bool = True):
    client = from_env()
    settle_pending(client)
    snapshot = client.snapshot()
    decision = db.get_decision(snapshot.betting_epoch)
    if decision is None and auto_lock and snapshot.decision_window:
        decision = create_decision(snapshot)
    trade_executed = bool(decision.get("trade_executed", True)) if decision else False
    public_signal = (
        decision.get("signal") if decision and trade_executed else ("NO_TRADE" if decision else None)
    )
    public_status = (
        "WAIT" if decision is None else ("LOCKED" if trade_executed else "NO_TRADE")
    )
    features = (decision.get("features_json") or {}) if decision else {}
    return {
        "ok": True,
        "status": public_status,
        "decision_locked": bool(decision),
        "trade_executed": trade_executed if decision else None,
        "no_trade_reason": decision.get("no_trade_reason") if decision else None,
        "betting_epoch": snapshot.betting_epoch,
        "live_epoch": snapshot.live_epoch,
        "seconds_to_lock": snapshot.seconds_to_lock,
        "decision_window": snapshot.decision_window,
        "signal": public_signal,
        "analysis_signal": decision.get("signal") if decision else None,
        "stake": decision.get("stake") if decision else 0.0,
        "probability_up": decision.get("probability_up") if decision else None,
        "probability_down": decision.get("probability_down") if decision else None,
        "raw_expected_coeff_up": decision.get("raw_expected_coeff_up") if decision else None,
        "raw_expected_coeff_down": decision.get("raw_expected_coeff_down") if decision else None,
        "payout_correction_up": decision.get("payout_correction_up") if decision else None,
        "payout_correction_down": decision.get("payout_correction_down") if decision else None,
        "payout_bucket_up": decision.get("payout_bucket_up") if decision else None,
        "payout_bucket_down": decision.get("payout_bucket_down") if decision else None,
        "expected_coeff_up": decision.get("expected_coeff_up") if decision else snapshot.current_net_coeff_up,
        "expected_coeff_down": decision.get("expected_coeff_down") if decision else snapshot.current_net_coeff_down,
        "ev_up": decision.get("ev_up") if decision else None,
        "ev_down": decision.get("ev_down") if decision else None,
        "selected_ev": decision.get("selected_ev") if decision else None,
        "agreement": decision.get("agreement") if decision else None,
        "selection_reason": features.get("selection_reason"),
        "crowd_binance_consensus": features.get("crowd_binance_consensus"),
        "selected_expected_coeff": features.get("selected_expected_coeff"),
        "normal_ev_pass": features.get("normal_ev_pass"),
        "consensus_override_eligible": features.get("consensus_override_eligible"),
        "crowd_binance_override": features.get("crowd_binance_override"),
        "trade_rule": features.get("trade_rule"),
        "decision_quality": decision.get("decision_quality") if decision else "WAIT_T_MINUS_40",
        "components": decision.get("components_json") if decision else None,
        "weights": decision.get("weights_json") if decision else None,
        "snapshot": snapshot.to_dict(),
        "paper_state": db.get_state(),
        "real_transactions": False,
    }


@app.get("/status")
def status(
    response: Response,
    history: str = Query("recent", description="recent, all or none"),
    limit: int = Query(30, ge=1),
    offset: int = Query(0, ge=0),
    ascending: bool = False,
):
    mode = history.strip().lower()
    if mode not in {"recent", "all", "none"}:
        raise HTTPException(
            status_code=422,
            detail="history must be one of: recent, all, none",
        )

    total = db.decision_count()
    safe_limit = min(int(limit), settings.history_api_max_limit)
    rows = [] if mode == "none" else db.decision_history(
        limit=safe_limit,
        offset=offset,
        ascending=ascending,
    )

    # Opening /status?history=all&limit=100000 directly in a browser downloads
    # the complete JSON response as a file. Fetch/XHR clients can still parse it normally.
    if mode == "all":
        response.headers["Content-Disposition"] = (
            'attachment; filename="m9_fusion_ev_history.json"'
        )
        response.headers["X-History-Mode"] = "all"

    returned = len(rows)
    return {
        "ok": True,
        "service": "m9-fusion-ev-paper-bot",
        "version": __version__,
        "paper_state": db.get_state(),
        "worker": worker_status(),
        "history_storage": "postgresql_no_automatic_deletion",
        "history_mode": mode,
        "history_count": total,
        "history_limit": safe_limit,
        "history_offset": offset,
        "history_returned": returned,
        "history_has_more": offset + returned < total,
        "history_next_offset": offset + returned if offset + returned < total else None,
        "history_download_json": "/status?history=all&limit=100000",
        "history_download_csv": "/history/export.csv",
        "history": rows,
    }


@app.get("/history")
def history(
    limit: int = Query(1000, ge=1),
    offset: int = Query(0, ge=0),
    ascending: bool = False,
):
    return {
        "ok": True,
        "count": db.decision_count(),
        "limit": min(limit, settings.history_api_max_limit),
        "offset": offset,
        "history": db.decision_history(limit, offset, ascending),
    }


@app.get("/history/count")
def history_count():
    return {"ok": True, "count": db.decision_count()}


@app.get("/history/export.csv")
def history_csv():
    return Response(
        content=db.export_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=m9_fusion_ev_history.csv"},
    )


@app.get("/rounds")
def rounds(limit: int = Query(30, ge=1)):
    return {"ok": True, "rounds": db.recent_rounds(limit)}


@app.get("/snapshots/{epoch}")
def snapshots(epoch: int):
    return {"ok": True, "epoch": epoch, "snapshots": db.load_snapshots(epoch)}


@app.get("/payout/calibration")
def payout_calibration_status(limit: int = Query(500, ge=1, le=5000)):
    rows = db.payout_calibration_history(limit)
    return {
        "ok": True,
        "version": __version__,
        "calibration": payout_calibration(rows),
        "history_rows": len(rows),
    }


@app.get("/strategy/performance")
def strategy_performance():
    return {
        "ok": True,
        "current_version": __version__,
        "versions": db.strategy_performance(),
    }


@app.get("/model/performance")
def model_performance(limit: int = Query(300, ge=1, le=5000)):
    rows = db.settled_component_history(limit)
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        actual = 1.0 if row.get("final_winner") == "UP" else 0.0
        for component in row.get("components_json") or []:
            if not component.get("available"):
                continue
            name = str(component.get("name"))
            p = float(component.get("probability_up", 0.5))
            item = result.setdefault(name, {"count": 0.0, "brier_sum": 0.0, "correct": 0.0})
            item["count"] += 1
            item["brier_sum"] += (p - actual) ** 2
            item["correct"] += 1 if (p >= 0.5) == (actual == 1.0) else 0
    output = {}
    for name, item in result.items():
        count = item["count"] or 1.0
        output[name] = {
            "count": int(item["count"]),
            "brier_score": item["brier_sum"] / count,
            "direction_accuracy": item["correct"] / count,
        }
    return {"ok": True, "lookback": len(rows), "models": output}


@app.post("/admin/tick")
def admin_tick():
    try:
        return {"ok": True, "tick": tick()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}")


@app.post("/admin/sync")
def admin_sync():
    try:
        return {"ok": True, "sync": sync_recent()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}")
