from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from psycopg2 import sql

from .config import SETTINGS
from .risk import should_arm_cooldown

_LOCK = threading.RLock()
_DECISIONS_TABLE = "paper_decisions"
_STATE_TABLE = "paper_state"
_ROUNDS_TABLE = "round_history"
_SNAPSHOTS_TABLE = "fusion_snapshots_v136"


def enabled() -> bool:
    return bool(SETTINGS.database_url)


@contextmanager
def conn():
    if not enabled():
        raise RuntimeError("DATABASE_URL is required")
    c = psycopg2.connect(SETTINGS.database_url, connect_timeout=10)
    try:
        yield c
    finally:
        c.close()


def _existing_tables(cur) -> set[str]:
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public'
        """
    )
    return {r[0] for r in cur.fetchall()}


def _table_columns(cur) -> dict[str, set[str]]:
    cur.execute(
        """
        SELECT table_name,column_name FROM information_schema.columns
        WHERE table_schema='public'
        """
    )
    result: dict[str, set[str]] = {}
    for table, column in cur.fetchall():
        result.setdefault(table, set()).add(column)
    return result


def _choose(
    existing: set[str],
    candidates: Iterable[str],
    default: str,
    *,
    columns: dict[str, set[str]] | None = None,
    signature: set[str] | None = None,
) -> str:
    for name in candidates:
        if name in existing:
            return name
    if columns and signature:
        scored = []
        for table, table_columns in columns.items():
            score = len(signature & table_columns)
            if score:
                scored.append((score, len(table_columns), table))
        if scored:
            scored.sort(reverse=True)
            best_score, _, best_table = scored[0]
            if best_score >= max(2, len(signature) // 2):
                return best_table
    return default


def _ident(name: str):
    return sql.Identifier(name)


def _add_columns(cur, table: str, specs: dict[str, str]) -> None:
    for name, ddl in specs.items():
        cur.execute(
            sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}").format(
                _ident(table), _ident(name), sql.SQL(ddl)
            )
        )


def init_db() -> None:
    global _DECISIONS_TABLE, _STATE_TABLE, _ROUNDS_TABLE, _SNAPSHOTS_TABLE
    with _LOCK, conn() as c, c.cursor() as cur:
        existing = _existing_tables(cur)
        columns = _table_columns(cur)
        _DECISIONS_TABLE = _choose(
            existing,
            ("paper_decisions", "decisions", "fusion_decisions", "paper_history", "fusion_history"),
            "paper_decisions",
            columns=columns,
            signature={"betting_epoch", "signal", "stake", "strategy_version", "bank_after"},
        )
        _STATE_TABLE = _choose(
            existing,
            ("paper_state", "fusion_state", "state"),
            "paper_state",
            columns=columns,
            signature={"bank", "wins", "losses", "trades_count", "peak_bank"},
        )
        _ROUNDS_TABLE = _choose(
            existing,
            ("round_history", "rounds_history", "fusion_rounds"),
            "round_history",
            columns=columns,
            signature={"epoch", "lock_price", "close_price", "oracle_called"},
        )
        # Snapshots are disposable model inputs, so v1.3.6 uses its own
        # table instead of risking a conflict with an older snapshot schema.
        _SNAPSHOTS_TABLE = "fusion_snapshots_v136"

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY,
                    start_bank DOUBLE PRECISION NOT NULL,
                    bank DOUBLE PRECISION NOT NULL,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    draws INTEGER NOT NULL DEFAULT 0,
                    trades_count INTEGER NOT NULL DEFAULT 0,
                    current_loss_streak INTEGER NOT NULL DEFAULT 0,
                    max_loss_streak INTEGER NOT NULL DEFAULT 0,
                    peak_bank DOUBLE PRECISION NOT NULL,
                    max_drawdown DOUBLE PRECISION NOT NULL DEFAULT 0,
                    last_settled_epoch BIGINT,
                    fib_index_even INTEGER NOT NULL DEFAULT 0,
                    fib_index_odd INTEGER NOT NULL DEFAULT 0,
                    cooldown_rounds_remaining INTEGER NOT NULL DEFAULT 0,
                    cooldown_trigger_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(_ident(_STATE_TABLE))
        )
        _add_columns(
            cur,
            _STATE_TABLE,
            {
                "start_bank": "DOUBLE PRECISION NOT NULL DEFAULT 500",
                "bank": "DOUBLE PRECISION NOT NULL DEFAULT 500",
                "wins": "INTEGER NOT NULL DEFAULT 0",
                "losses": "INTEGER NOT NULL DEFAULT 0",
                "draws": "INTEGER NOT NULL DEFAULT 0",
                "trades_count": "INTEGER NOT NULL DEFAULT 0",
                "current_loss_streak": "INTEGER NOT NULL DEFAULT 0",
                "max_loss_streak": "INTEGER NOT NULL DEFAULT 0",
                "peak_bank": "DOUBLE PRECISION NOT NULL DEFAULT 500",
                "max_drawdown": "DOUBLE PRECISION NOT NULL DEFAULT 0",
                "last_settled_epoch": "BIGINT",
                "fib_index_even": "INTEGER NOT NULL DEFAULT 0",
                "fib_index_odd": "INTEGER NOT NULL DEFAULT 0",
                "cooldown_rounds_remaining": "INTEGER NOT NULL DEFAULT 0",
                "cooldown_trigger_count": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            },
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}(id,start_bank,bank,peak_bank)
                VALUES(1,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """
            ).format(_ident(_STATE_TABLE)),
            (SETTINGS.start_bank, SETTINGS.start_bank, SETTINGS.start_bank),
        )

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    betting_epoch BIGINT PRIMARY KEY,
                    live_epoch BIGINT,
                    locked_at_chain_timestamp BIGINT,
                    locked_at_seconds_to_lock INTEGER,
                    signal TEXT,
                    probability_up DOUBLE PRECISION,
                    probability_down DOUBLE PRECISION,
                    expected_coeff_up DOUBLE PRECISION,
                    expected_coeff_down DOUBLE PRECISION,
                    ev_up DOUBLE PRECISION,
                    ev_down DOUBLE PRECISION,
                    selected_ev DOUBLE PRECISION,
                    agreement DOUBLE PRECISION,
                    decision_quality TEXT,
                    stake DOUBLE PRECISION NOT NULL DEFAULT 0,
                    bank_before DOUBLE PRECISION,
                    components_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    weights_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    features_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    snapshot_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    settled BOOLEAN NOT NULL DEFAULT FALSE,
                    final_winner TEXT,
                    final_coeff_gross DOUBLE PRECISION,
                    final_coeff_net DOUBLE PRECISION,
                    final_move_points DOUBLE PRECISION,
                    outcome TEXT,
                    pnl DOUBLE PRECISION,
                    bank_after DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    settled_at TIMESTAMPTZ,
                    raw_expected_coeff_up DOUBLE PRECISION,
                    raw_expected_coeff_down DOUBLE PRECISION,
                    payout_correction_up DOUBLE PRECISION,
                    payout_correction_down DOUBLE PRECISION,
                    bank_before_settlement DOUBLE PRECISION,
                    final_coeff_up DOUBLE PRECISION,
                    final_coeff_down DOUBLE PRECISION,
                    actual_ev_signal DOUBLE PRECISION,
                    payout_ratio_signal DOUBLE PRECISION,
                    strategy_version TEXT,
                    payout_bucket_up TEXT,
                    payout_bucket_down TEXT,
                    trade_executed BOOLEAN NOT NULL DEFAULT FALSE,
                    no_trade_reason TEXT,
                    source_key TEXT,
                    selection_reason TEXT,
                    fib_line TEXT,
                    fib_index INTEGER,
                    fib_step INTEGER,
                    shadow_allowed BOOLEAN,
                    shadow_reason TEXT,
                    shadow_stats_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    shadow_pnl DOUBLE PRECISION,
                    stake_mode TEXT,
                    stake_tier TEXT,
                    cooldown_applied BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            ).format(_ident(_DECISIONS_TABLE))
        )
        _add_columns(
            cur,
            _DECISIONS_TABLE,
            {
                "live_epoch": "BIGINT",
                "locked_at_chain_timestamp": "BIGINT",
                "locked_at_seconds_to_lock": "INTEGER",
                "signal": "TEXT",
                "probability_up": "DOUBLE PRECISION",
                "probability_down": "DOUBLE PRECISION",
                "expected_coeff_up": "DOUBLE PRECISION",
                "expected_coeff_down": "DOUBLE PRECISION",
                "ev_up": "DOUBLE PRECISION",
                "ev_down": "DOUBLE PRECISION",
                "selected_ev": "DOUBLE PRECISION",
                "agreement": "DOUBLE PRECISION",
                "decision_quality": "TEXT",
                "stake": "DOUBLE PRECISION NOT NULL DEFAULT 0",
                "bank_before": "DOUBLE PRECISION",
                "components_json": "JSONB NOT NULL DEFAULT '[]'::jsonb",
                "weights_json": "JSONB NOT NULL DEFAULT '{}'::jsonb",
                "features_json": "JSONB NOT NULL DEFAULT '{}'::jsonb",
                "snapshot_json": "JSONB NOT NULL DEFAULT '{}'::jsonb",
                "settled": "BOOLEAN NOT NULL DEFAULT FALSE",
                "final_winner": "TEXT",
                "final_coeff_gross": "DOUBLE PRECISION",
                "final_coeff_net": "DOUBLE PRECISION",
                "final_move_points": "DOUBLE PRECISION",
                "outcome": "TEXT",
                "pnl": "DOUBLE PRECISION",
                "bank_after": "DOUBLE PRECISION",
                "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                "settled_at": "TIMESTAMPTZ",
                "raw_expected_coeff_up": "DOUBLE PRECISION",
                "raw_expected_coeff_down": "DOUBLE PRECISION",
                "payout_correction_up": "DOUBLE PRECISION",
                "payout_correction_down": "DOUBLE PRECISION",
                "bank_before_settlement": "DOUBLE PRECISION",
                "final_coeff_up": "DOUBLE PRECISION",
                "final_coeff_down": "DOUBLE PRECISION",
                "actual_ev_signal": "DOUBLE PRECISION",
                "payout_ratio_signal": "DOUBLE PRECISION",
                "strategy_version": "TEXT",
                "payout_bucket_up": "TEXT",
                "payout_bucket_down": "TEXT",
                "trade_executed": "BOOLEAN NOT NULL DEFAULT FALSE",
                "no_trade_reason": "TEXT",
                "source_key": "TEXT",
                "selection_reason": "TEXT",
                "fib_line": "TEXT",
                "fib_index": "INTEGER",
                "fib_step": "INTEGER",
                "shadow_allowed": "BOOLEAN",
                "shadow_reason": "TEXT",
                "shadow_stats_json": "JSONB NOT NULL DEFAULT '{}'::jsonb",
                "shadow_pnl": "DOUBLE PRECISION",
                "stake_mode": "TEXT",
                "stake_tier": "TEXT",
                "cooldown_applied": "BOOLEAN NOT NULL DEFAULT FALSE",
            },
        )
        try:
            cur.execute(
                sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (betting_epoch)").format(
                    _ident(f"{_DECISIONS_TABLE}_epoch_unique"), _ident(_DECISIONS_TABLE)
                )
            )
        except Exception:
            c.rollback()
            raise

        # Reuse the existing 1.3.3 history immediately as shadow training data.
        cur.execute(
            sql.SQL(
                """
                UPDATE {} SET
                    source_key=CASE
                        WHEN decision_quality ILIKE '%CROWD_BINANCE%' THEN 'CROWD_BINANCE_FALLBACK'
                        WHEN decision_quality ILIKE '%PROBABILITY_FALLBACK%' THEN 'PROBABILITY_FALLBACK'
                        WHEN COALESCE(selected_ev,-999) >= 0 THEN 'EV_PRIMARY'
                        ELSE 'PROBABILITY_FALLBACK'
                    END,
                    selection_reason=COALESCE(selection_reason,
                        CASE
                            WHEN decision_quality ILIKE '%CROWD_BINANCE%' THEN 'WEAK_EV_CROWD_BINANCE_FALLBACK'
                            WHEN decision_quality ILIKE '%PROBABILITY_FALLBACK%' THEN 'NEGATIVE_EV_PROBABILITY_FALLBACK'
                            ELSE 'POSITIVE_EV_BEST_SIDE'
                        END)
                WHERE source_key IS NULL
                """
            ).format(_ident(_DECISIONS_TABLE))
        )
        cur.execute(
            sql.SQL(
                """
                UPDATE {} SET shadow_pnl=CASE
                    WHEN final_winner NOT IN ('UP','DOWN') OR signal NOT IN ('UP','DOWN') THEN 0
                    WHEN signal<>final_winner THEN %s
                    WHEN signal='UP' AND final_coeff_up IS NOT NULL THEN %s*(final_coeff_up-1)
                    WHEN signal='DOWN' AND final_coeff_down IS NOT NULL THEN %s*(final_coeff_down-1)
                    ELSE 0 END
                WHERE COALESCE(settled,FALSE)=TRUE AND shadow_pnl IS NULL
                """
            ).format(_ident(_DECISIONS_TABLE)),
            (-SETTINGS.shadow_stake, SETTINGS.shadow_stake, SETTINGS.shadow_stake),
        )

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    epoch BIGINT PRIMARY KEY,
                    start_timestamp BIGINT,
                    lock_timestamp BIGINT,
                    close_timestamp BIGINT,
                    lock_price DOUBLE PRECISION,
                    close_price DOUBLE PRECISION,
                    lock_oracle_id NUMERIC(78,0),
                    close_oracle_id NUMERIC(78,0),
                    total_amount_bnb DOUBLE PRECISION,
                    bull_amount_bnb DOUBLE PRECISION,
                    bear_amount_bnb DOUBLE PRECISION,
                    reward_base_bnb DOUBLE PRECISION,
                    reward_amount_bnb DOUBLE PRECISION,
                    oracle_called BOOLEAN,
                    actual_winner TEXT,
                    winner_coeff_gross DOUBLE PRECISION,
                    winner_coeff_net DOUBLE PRECISION,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(_ident(_ROUNDS_TABLE))
        )
        _add_columns(
            cur,
            _ROUNDS_TABLE,
            {
                "start_timestamp": "BIGINT",
                "lock_timestamp": "BIGINT",
                "close_timestamp": "BIGINT",
                "lock_price": "DOUBLE PRECISION",
                "close_price": "DOUBLE PRECISION",
                "lock_oracle_id": "NUMERIC(78,0)",
                "close_oracle_id": "NUMERIC(78,0)",
                "total_amount_bnb": "DOUBLE PRECISION",
                "bull_amount_bnb": "DOUBLE PRECISION",
                "bear_amount_bnb": "DOUBLE PRECISION",
                "reward_base_bnb": "DOUBLE PRECISION",
                "reward_amount_bnb": "DOUBLE PRECISION",
                "oracle_called": "BOOLEAN",
                "actual_winner": "TEXT",
                "winner_coeff_gross": "DOUBLE PRECISION",
                "winner_coeff_net": "DOUBLE PRECISION",
                "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            },
        )
        cur.execute(
            sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (epoch)").format(
                _ident(f"{_ROUNDS_TABLE}_epoch_unique"), _ident(_ROUNDS_TABLE)
            )
        )

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    betting_epoch BIGINT NOT NULL,
                    live_epoch BIGINT NOT NULL,
                    seconds_to_lock INTEGER NOT NULL,
                    bucket INTEGER NOT NULL,
                    chain_timestamp BIGINT NOT NULL,
                    chainlink_price DOUBLE PRECISION NOT NULL,
                    live_move_signed DOUBLE PRECISION,
                    bull_amount_bnb DOUBLE PRECISION,
                    bear_amount_bnb DOUBLE PRECISION,
                    snapshot_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(betting_epoch,bucket)
                )
                """
            ).format(_ident(_SNAPSHOTS_TABLE))
        )
        _add_columns(
            cur,
            _SNAPSHOTS_TABLE,
            {
                "betting_epoch": "BIGINT",
                "live_epoch": "BIGINT",
                "seconds_to_lock": "INTEGER",
                "bucket": "INTEGER",
                "chain_timestamp": "BIGINT",
                "chainlink_price": "DOUBLE PRECISION",
                "live_move_signed": "DOUBLE PRECISION",
                "bull_amount_bnb": "DOUBLE PRECISION",
                "bear_amount_bnb": "DOUBLE PRECISION",
                "snapshot_json": "JSONB NOT NULL DEFAULT '{}'::jsonb",
                "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            },
        )
        cur.execute(
            sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (betting_epoch,bucket)").format(
                _ident(f"{_SNAPSHOTS_TABLE}_epoch_bucket_unique"), _ident(_SNAPSHOTS_TABLE)
            )
        )
        c.commit()


def ping() -> bool:
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1")
            return bool(cur.fetchone()[0])
    except Exception:
        return False


def get_state(for_update: bool = False, cursor=None) -> dict[str, Any]:
    if cursor is not None:
        query = sql.SQL("SELECT * FROM {} WHERE id=1{}").format(
            _ident(_STATE_TABLE), sql.SQL(" FOR UPDATE" if for_update else "")
        )
        cursor.execute(query)
        row = cursor.fetchone()
        return dict(row) if row else {}
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql.SQL("SELECT * FROM {} WHERE id=1").format(_ident(_STATE_TABLE)))
        row = cur.fetchone()
        return dict(row) if row else {}


def get_decision(betting_epoch: int) -> dict[str, Any] | None:
    """Return one decision by betting epoch, or None when it does not exist.

    This is the single read helper used by the worker and the cached /signal
    endpoint. Keeping it in the database layer prevents duplicate SQL and
    preserves compatibility with whichever legacy decisions table init_db()
    selected for the current PostgreSQL database.
    """
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE betting_epoch=%s LIMIT 1").format(
                _ident(_DECISIONS_TABLE)
            ),
            (int(betting_epoch),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def insert_decision(data: dict[str, Any]) -> dict[str, Any]:
    columns = [
        "betting_epoch", "live_epoch", "locked_at_chain_timestamp",
        "locked_at_seconds_to_lock", "signal", "probability_up",
        "probability_down", "expected_coeff_up", "expected_coeff_down",
        "ev_up", "ev_down", "selected_ev", "agreement", "decision_quality",
        "stake", "bank_before", "components_json", "weights_json",
        "features_json", "snapshot_json", "raw_expected_coeff_up",
        "raw_expected_coeff_down", "payout_correction_up",
        "payout_correction_down", "strategy_version", "payout_bucket_up",
        "payout_bucket_down", "trade_executed", "no_trade_reason",
        "source_key", "selection_reason", "fib_line", "fib_index", "fib_step",
        "shadow_allowed", "shadow_reason", "shadow_stats_json",
        "stake_mode", "stake_tier", "cooldown_applied",
    ]
    values = []
    json_cols = {"components_json", "weights_json", "features_json", "snapshot_json", "shadow_stats_json"}
    for col in columns:
        value = data.get(col)
        values.append(Json(value if value is not None else ([] if col == "components_json" else {})) if col in json_cols else value)
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (betting_epoch) DO NOTHING RETURNING *").format(
            _ident(_DECISIONS_TABLE),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in columns),
        )
        cur.execute(query, values)
        row = cur.fetchone()
        c.commit()
    return dict(row) if row else (get_decision(int(data["betting_epoch"])) or {})


def unsettled_decisions(limit: int = 100) -> list[dict[str, Any]]:
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT * FROM {} WHERE COALESCE(settled,FALSE)=FALSE ORDER BY betting_epoch ASC LIMIT %s"
            ).format(_ident(_DECISIONS_TABLE)),
            (int(limit),),
        )
        return [dict(r) for r in cur.fetchall()]


def settle_decision_atomic(
    epoch: int,
    *,
    final_winner: str,
    final_coeff_gross: float | None,
    final_coeff_net: float | None,
    final_coeff_up: float | None,
    final_coeff_down: float | None,
    final_move_points: float | None,
    outcome: str,
    pnl: float,
    shadow_pnl: float,
    actual_ev_signal: float | None,
    payout_ratio_signal: float | None,
) -> bool:
    with _LOCK, conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE betting_epoch=%s FOR UPDATE").format(
                _ident(_DECISIONS_TABLE)
            ),
            (int(epoch),),
        )
        decision = cur.fetchone()
        if not decision or decision.get("settled"):
            c.rollback()
            return False
        state = get_state(for_update=True, cursor=cur)
        bank_before_settlement = float(state.get("bank") or SETTINGS.start_bank)
        trade_executed = bool(decision.get("trade_executed"))
        new_bank = bank_before_settlement + (float(pnl) if trade_executed else 0.0)

        wins = int(state.get("wins") or 0)
        losses = int(state.get("losses") or 0)
        draws = int(state.get("draws") or 0)
        trades = int(state.get("trades_count") or 0)
        current_loss = int(state.get("current_loss_streak") or 0)
        max_loss = int(state.get("max_loss_streak") or 0)
        cooldown_remaining = int(state.get("cooldown_rounds_remaining") or 0)
        cooldown_trigger_count = int(state.get("cooldown_trigger_count") or 0)
        if trade_executed:
            trades += 1
            if outcome == "WIN":
                wins += 1
                current_loss = 0
            elif outcome == "LOSS":
                losses += 1
                current_loss += 1
                max_loss = max(max_loss, current_loss)
                if should_arm_cooldown(current_loss):
                    cooldown_remaining = max(cooldown_remaining, int(SETTINGS.cooldown_rounds))
                    cooldown_trigger_count += 1
            else:
                draws += 1
                current_loss = 0


        peak = max(float(state.get("peak_bank") or bank_before_settlement), new_bank)
        max_drawdown = max(float(state.get("max_drawdown") or 0.0), peak - new_bank)
        cur.execute(
            sql.SQL(
                """
                UPDATE {} SET
                    bank=%s,wins=%s,losses=%s,draws=%s,trades_count=%s,
                    current_loss_streak=%s,max_loss_streak=%s,peak_bank=%s,
                    max_drawdown=%s,cooldown_rounds_remaining=%s,
                    cooldown_trigger_count=%s,
                    last_settled_epoch=GREATEST(COALESCE(last_settled_epoch,0),%s),
                    updated_at=NOW()
                WHERE id=1
                """
            ).format(_ident(_STATE_TABLE)),
            (
                new_bank, wins, losses, draws, trades, current_loss, max_loss,
                peak, max_drawdown, cooldown_remaining, cooldown_trigger_count, int(epoch),
            ),
        )
        cur.execute(
            sql.SQL(
                """
                UPDATE {} SET
                    settled=TRUE,final_winner=%s,final_coeff_gross=%s,
                    final_coeff_net=%s,final_coeff_up=%s,final_coeff_down=%s,
                    final_move_points=%s,outcome=%s,pnl=%s,shadow_pnl=%s,
                    bank_before_settlement=%s,bank_after=%s,actual_ev_signal=%s,
                    payout_ratio_signal=%s,settled_at=NOW(),updated_at=NOW()
                WHERE betting_epoch=%s AND COALESCE(settled,FALSE)=FALSE
                """
            ).format(_ident(_DECISIONS_TABLE)),
            (
                final_winner, final_coeff_gross, final_coeff_net, final_coeff_up,
                final_coeff_down, final_move_points, outcome,
                float(pnl) if trade_executed else 0.0, float(shadow_pnl),
                bank_before_settlement, new_bank, actual_ev_signal,
                payout_ratio_signal, int(epoch),
            ),
        )
        changed = cur.rowcount == 1
        c.commit()
        return changed



def consume_cooldown_round() -> bool:
    """Atomically consume one pending cooldown decision, if any."""
    with _LOCK, conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        state = get_state(for_update=True, cursor=cur)
        remaining = int(state.get("cooldown_rounds_remaining") or 0)
        if remaining <= 0:
            c.commit()
            return False
        cur.execute(
            sql.SQL(
                "UPDATE {} SET cooldown_rounds_remaining=%s,updated_at=NOW() WHERE id=1"
            ).format(_ident(_STATE_TABLE)),
            (remaining - 1,),
        )
        c.commit()
        return True

def upsert_round(data: dict[str, Any]) -> None:
    cols = [
        "epoch", "start_timestamp", "lock_timestamp", "close_timestamp",
        "lock_price", "close_price", "lock_oracle_id", "close_oracle_id",
        "total_amount_bnb", "bull_amount_bnb", "bear_amount_bnb",
        "reward_base_bnb", "reward_amount_bnb", "oracle_called",
        "actual_winner", "winner_coeff_gross", "winner_coeff_net",
    ]
    values = [data.get(c) for c in cols]
    with conn() as c, c.cursor() as cur:
        query = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (epoch) DO UPDATE SET {}"
        ).format(
            _ident(_ROUNDS_TABLE),
            sql.SQL(",").join(map(sql.Identifier, cols)),
            sql.SQL(",").join(sql.Placeholder() for _ in cols),
            sql.SQL(",").join(
                sql.SQL("{}=EXCLUDED.{}").format(_ident(x), _ident(x))
                for x in cols if x != "epoch"
            ) + sql.SQL(",updated_at=NOW()"),
        )
        cur.execute(query, values)
        c.commit()


def recent_rounds(limit: int = 1200) -> list[dict[str, Any]]:
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT * FROM {} WHERE actual_winner IN ('UP','DOWN') ORDER BY epoch DESC LIMIT %s"
            ).format(_ident(_ROUNDS_TABLE)),
            (int(limit),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        rows.reverse()
        return rows


def save_snapshot(data: dict[str, Any]) -> bool:
    bucket = int(data["seconds_to_lock"]) // max(1, SETTINGS.snapshot_bucket_seconds)
    with conn() as c, c.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}(
                    betting_epoch,live_epoch,seconds_to_lock,bucket,chain_timestamp,
                    chainlink_price,live_move_signed,bull_amount_bnb,bear_amount_bnb,snapshot_json
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (betting_epoch,bucket) DO NOTHING
                """
            ).format(_ident(_SNAPSHOTS_TABLE)),
            (
                int(data["betting_epoch"]), int(data["live_epoch"]),
                int(data["seconds_to_lock"]), bucket, int(data["chain_timestamp"]),
                float(data["chainlink_price"]), float(data["live_move_signed"]),
                float(data.get("bull_amount_bnb") or 0),
                float(data.get("bear_amount_bnb") or 0), Json(data),
            ),
        )
        changed = cur.rowcount == 1
        c.commit()
        return changed


def snapshots_for_epoch(betting_epoch: int, limit: int = 30) -> list[dict[str, Any]]:
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT * FROM {} WHERE betting_epoch=%s ORDER BY seconds_to_lock DESC LIMIT %s"
            ).format(_ident(_SNAPSHOTS_TABLE)),
            (int(betting_epoch), int(limit)),
        )
        return [dict(r) for r in cur.fetchall()]


def history(
    limit: int = 100,
    offset: int = 0,
    *,
    settled_only: bool = False,
    trades_only: bool = False,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if settled_only:
        clauses.append("COALESCE(settled,FALSE)=TRUE")
    if trades_only:
        clauses.append("COALESCE(trade_executed,FALSE)=TRUE")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    safe_limit = max(1, min(int(limit), SETTINGS.history_api_max_limit))
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {}{} ORDER BY betting_epoch DESC LIMIT %s OFFSET %s").format(
                _ident(_DECISIONS_TABLE), sql.SQL(where)
            ),
            (*params, safe_limit, max(0, int(offset))),
        )
        return [dict(r) for r in cur.fetchall()]


def history_count(*, settled_only: bool = False, trades_only: bool = False) -> int:
    clauses = []
    if settled_only:
        clauses.append("COALESCE(settled,FALSE)=TRUE")
    if trades_only:
        clauses.append("COALESCE(trade_executed,FALSE)=TRUE")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {}{}").format(
                _ident(_DECISIONS_TABLE), sql.SQL(where)
            )
        )
        return int(cur.fetchone()[0])


def shadow_rows(source_key: str, signal: str | None, lookback: int) -> list[dict[str, Any]]:
    clauses = [
        "COALESCE(settled,FALSE)=TRUE",
        "final_winner IN ('UP','DOWN')",
        "source_key=%s",
    ]
    params: list[Any] = [source_key]
    if signal:
        clauses.append("signal=%s")
        params.append(signal)
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT betting_epoch,signal,final_winner,final_coeff_up,final_coeff_down,shadow_pnl FROM {} WHERE {} ORDER BY betting_epoch DESC LIMIT %s"
            ).format(_ident(_DECISIONS_TABLE), sql.SQL(" AND ".join(clauses))),
            (*params, int(lookback)),
        )
        return [dict(r) for r in cur.fetchall()]


def payout_ratios(side: str, bucket: str, lookback: int) -> list[float]:
    raw_col = "raw_expected_coeff_up" if side == "UP" else "raw_expected_coeff_down"
    final_col = "final_coeff_up" if side == "UP" else "final_coeff_down"
    bucket_col = "payout_bucket_up" if side == "UP" else "payout_bucket_down"
    with conn() as c, c.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT {final},{raw} FROM {table} WHERE COALESCE(settled,FALSE)=TRUE AND {bucket}=%s AND {final}>0 AND {raw}>0 ORDER BY betting_epoch DESC LIMIT %s"
            ).format(
                final=_ident(final_col), raw=_ident(raw_col),
                table=_ident(_DECISIONS_TABLE), bucket=_ident(bucket_col),
            ),
            (bucket, int(lookback)),
        )
        ratios = []
        for final_value, raw_value in cur.fetchall():
            try:
                ratio = float(final_value) / float(raw_value)
                if math.isfinite(ratio) and ratio > 0:
                    ratios.append(ratio)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        return ratios


def table_names() -> dict[str, str]:
    return {
        "decisions": _DECISIONS_TABLE,
        "state": _STATE_TABLE,
        "rounds": _ROUNDS_TABLE,
        "snapshots": _SNAPSHOTS_TABLE,
    }
