from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder

from . import db
from .config import SETTINGS
from .pancake_client import from_env
from .shadow import summarize
from .worker import bootstrap_rounds, loop, status as worker_status, tick

_STOP: Optional[asyncio.Event] = None
_TASK: Optional[asyncio.Task] = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _STOP, _TASK
    db.init_db()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(bootstrap_rounds, from_env()), timeout=45
        )
    except Exception:
        pass
    if SETTINGS.worker_enabled:
        _STOP = asyncio.Event()
        _TASK = asyncio.create_task(loop(_STOP))
    yield
    if _STOP:
        _STOP.set()
    if _TASK:
        try:
            await asyncio.wait_for(_TASK, timeout=8)
        except Exception:
            _TASK.cancel()


app = FastAPI(
    title="M9 Fusion EV Adaptive EV Shadow Bot",
    version=SETTINGS.version,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "m9-fusion-ev-paper-bot",
        "version": SETTINGS.version,
        "mode": "PAPER",
        "strategy": "adaptive EV stake $5/$10/$15 + shadow quality + loss cooldown",
        "stake_mode": "adaptive_ev_tiers",
        "stake_rules": {
            "ev_negative": SETTINGS.stake_low,
            "ev_zero_to_0_05": SETTINGS.stake_mid,
            "ev_ge_0_05": SETTINGS.stake_high,
        },
        "signal_url": "/signal",
        "status_url": "/status?history=recent&limit=30",
        "history_csv_url": "/history/export.csv",
        "shadow_performance_url": "/shadow/performance",
    }


@app.get("/health")
def health():
    client = from_env()
    return {
        "ok": True,
        "service": "m9-fusion-ev-paper-bot",
        "version": SETTINGS.version,
        "connected": client.is_connected(),
        "database_connected": db.ping(),
        "worker": worker_status(),
        "tables": db.table_names(),
        "trade_filter_enabled": SETTINGS.trade_filter_enabled,
        "shadow_filter_enabled": SETTINGS.shadow_filter_enabled,
        "min_trade_ev": SETTINGS.min_trade_ev,
        "stake_mode": "adaptive_ev_tiers",
        "stake_rules": {
            "ev_negative": SETTINGS.stake_low,
            "ev_zero_to_0_05": SETTINGS.stake_mid,
            "ev_ge_0_05": SETTINGS.stake_high,
        },
    }


@app.get("/signal")
def signal():
    result = tick()
    epoch = result.get("betting_epoch")
    decision = db.get_decision(int(epoch)) if epoch is not None else None
    if decision:
        return _json_safe(
            {
                "ok": True,
                "status": "LOCKED",
                "decision_locked": True,
                **decision,
                "worker_tick": result,
            }
        )
    return {
        "ok": True,
        "status": "WAIT",
        "decision_locked": False,
        "betting_epoch": epoch,
        "seconds_to_lock": result.get("seconds_to_lock"),
        "worker_tick": result,
    }


@app.get("/status")
def status(
    history: str = Query("recent", pattern="^(recent|all|none)$"),
    limit: int = Query(30, ge=1),
    offset: int = Query(0, ge=0),
):
    safe_limit = min(limit, SETTINGS.history_api_max_limit)
    rows = [] if history == "none" else db.history(safe_limit, offset)
    count = db.history_count()
    state = db.get_state()
    return _json_safe(
        {
            "ok": True,
            "service": "m9-fusion-ev-paper-bot",
            "version": SETTINGS.version,
            "paper_state": state,
            "worker": worker_status(),
            "history_storage": "postgresql_no_automatic_deletion",
            "history_mode": history,
            "history_count": count,
            "history_limit": safe_limit,
            "history_offset": offset,
            "history_returned": len(rows),
            "history_has_more": offset + len(rows) < count,
            "history_next_offset": offset + len(rows) if offset + len(rows) < count else None,
            "history_download_json": "/status?history=all&limit=100000",
            "history_download_csv": "/history/export.csv",
            "history": rows,
        }
    )


@app.get("/history")
def history(
    limit: int = Query(1000, ge=1),
    offset: int = Query(0, ge=0),
    settled_only: bool = False,
    trades_only: bool = False,
):
    rows = db.history(limit, offset, settled_only=settled_only, trades_only=trades_only)
    return _json_safe(
        {
            "ok": True,
            "count": db.history_count(settled_only=settled_only, trades_only=trades_only),
            "limit": min(limit, SETTINGS.history_api_max_limit),
            "offset": offset,
            "history": rows,
        }
    )


@app.get("/history/export.csv")
def history_export():
    rows = db.history(SETTINGS.history_api_max_limit, 0)
    output = io.StringIO()
    if not rows:
        output.write("betting_epoch\n")
    else:
        columns = list(rows[0].keys())
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _json_safe(v) for k, v in row.items()})
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=m9_fusion_ev_history_v1_3_6.csv"},
    )


def _performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [r for r in rows if r.get("trade_executed") and r.get("outcome") in {"WIN", "LOSS"}]
    wins = sum(1 for r in trades if r.get("outcome") == "WIN")
    losses = sum(1 for r in trades if r.get("outcome") == "LOSS")
    pnl = sum(float(r.get("pnl") or 0) for r in trades)
    gross_profit = sum(float(r.get("pnl") or 0) for r in trades if float(r.get("pnl") or 0) > 0)
    gross_loss = -sum(float(r.get("pnl") or 0) for r in trades if float(r.get("pnl") or 0) < 0)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(trades) if trades else 0,
        "pnl": pnl,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
    }


@app.get("/model/performance")
def model_performance(limit: int = Query(5000, ge=1)):
    rows = db.history(min(limit, SETTINGS.history_api_max_limit), 0, settled_only=True)
    overall = _performance(rows)
    by_version: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_version.setdefault(str(row.get("strategy_version") or "unknown"), []).append(row)
    return _json_safe(
        {
            "ok": True,
            "overall": overall,
            "by_version": {key: _performance(value) for key, value in by_version.items()},
        }
    )


@app.get("/shadow/performance")
def shadow_performance():
    source_keys = ["EV_PRIMARY", "CROWD_BINANCE_FALLBACK", "PROBABILITY_FALLBACK"]
    result: dict[str, Any] = {}
    for source in source_keys:
        source_rows = db.shadow_rows(source, None, SETTINGS.shadow_source_lookback)
        result[source] = {
            "overall": summarize(source_rows).to_dict(),
            "UP": summarize(db.shadow_rows(source, "UP", SETTINGS.shadow_side_lookback)).to_dict(),
            "DOWN": summarize(db.shadow_rows(source, "DOWN", SETTINGS.shadow_side_lookback)).to_dict(),
        }
    return {
        "ok": True,
        "version": SETTINGS.version,
        "filter_settings": {
            "source_lookback": SETTINGS.shadow_source_lookback,
            "side_lookback": SETTINGS.shadow_side_lookback,
            "min_samples": SETTINGS.shadow_min_samples,
            "min_profit_factor": SETTINGS.shadow_min_profit_factor,
            "min_win_rate": SETTINGS.shadow_min_win_rate,
            "recent_window": SETTINGS.shadow_recent_window,
            "recent_min_pnl": SETTINGS.shadow_recent_min_pnl,
            "quality_window": SETTINGS.quality_window,
            "quality_min_samples": SETTINGS.quality_min_samples,
            "quality_min_win_rate": SETTINGS.quality_min_win_rate,
            "quality_min_profit_factor": SETTINGS.quality_min_profit_factor,
            "cooldown_loss_streak_trigger": SETTINGS.cooldown_loss_streak_trigger,
            "cooldown_rounds": SETTINGS.cooldown_rounds,
        },
        "sources": result,
    }
