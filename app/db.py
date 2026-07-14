from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings
from .models import FusionSnapshot, RoundData


def enabled() -> bool:
    return bool(settings.database_url)


def connect():
    if not enabled():
        raise RuntimeError("DATABASE_URL is missing. Add PostgreSQL in Railway.")
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def init_db() -> None:
    if not enabled():
        return
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fusion_rounds (
                epoch BIGINT PRIMARY KEY,
                start_timestamp BIGINT,
                lock_timestamp BIGINT,
                close_timestamp BIGINT,
                lock_price DOUBLE PRECISION,
                close_price DOUBLE PRECISION,
                total_amount_bnb DOUBLE PRECISION,
                bull_amount_bnb DOUBLE PRECISION,
                bear_amount_bnb DOUBLE PRECISION,
                reward_base_bnb DOUBLE PRECISION,
                reward_amount_bnb DOUBLE PRECISION,
                oracle_called BOOLEAN NOT NULL DEFAULT FALSE,
                actual_winner TEXT,
                winner_coeff_gross DOUBLE PRECISION,
                winner_coeff_net DOUBLE PRECISION,
                move_points DOUBLE PRECISION,
                source TEXT NOT NULL DEFAULT 'pancake_final',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fusion_snapshots (
                id BIGSERIAL PRIMARY KEY,
                betting_epoch BIGINT NOT NULL,
                live_epoch BIGINT NOT NULL,
                bucket_seconds INTEGER NOT NULL,
                chain_timestamp BIGINT NOT NULL,
                seconds_to_lock INTEGER NOT NULL,
                chainlink_price DOUBLE PRECISION NOT NULL,
                oracle_updated_at BIGINT,
                oracle_age_seconds INTEGER,
                live_lock_price DOUBLE PRECISION,
                live_move_signed DOUBLE PRECISION,
                live_move_points DOUBLE PRECISION,
                provisional_winner TEXT,
                betting_total_bnb DOUBLE PRECISION,
                betting_bull_bnb DOUBLE PRECISION,
                betting_bear_bnb DOUBLE PRECISION,
                betting_bull_share_pct DOUBLE PRECISION,
                betting_bear_share_pct DOUBLE PRECISION,
                current_net_coeff_up DOUBLE PRECISION,
                current_net_coeff_down DOUBLE PRECISION,
                binance_json JSONB,
                snapshot_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (betting_epoch, bucket_seconds)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS fusion_snapshots_epoch_idx
            ON fusion_snapshots (betting_epoch, seconds_to_lock DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fusion_decisions (
                betting_epoch BIGINT PRIMARY KEY,
                live_epoch BIGINT NOT NULL,
                strategy_version TEXT NOT NULL DEFAULT 'legacy',
                locked_at_chain_timestamp BIGINT NOT NULL,
                locked_at_seconds_to_lock INTEGER NOT NULL,
                signal TEXT NOT NULL CHECK (signal IN ('UP','DOWN')),
                probability_up DOUBLE PRECISION NOT NULL,
                probability_down DOUBLE PRECISION NOT NULL,
                raw_expected_coeff_up DOUBLE PRECISION,
                raw_expected_coeff_down DOUBLE PRECISION,
                payout_correction_up DOUBLE PRECISION,
                payout_correction_down DOUBLE PRECISION,
                payout_bucket_up TEXT,
                payout_bucket_down TEXT,
                expected_coeff_up DOUBLE PRECISION NOT NULL,
                expected_coeff_down DOUBLE PRECISION NOT NULL,
                ev_up DOUBLE PRECISION NOT NULL,
                ev_down DOUBLE PRECISION NOT NULL,
                selected_ev DOUBLE PRECISION NOT NULL,
                agreement DOUBLE PRECISION NOT NULL,
                decision_quality TEXT NOT NULL,
                stake DOUBLE PRECISION NOT NULL,
                bank_before DOUBLE PRECISION NOT NULL,
                components_json JSONB NOT NULL,
                weights_json JSONB NOT NULL,
                features_json JSONB NOT NULL,
                snapshot_json JSONB NOT NULL,
                settled BOOLEAN NOT NULL DEFAULT FALSE,
                final_winner TEXT,
                final_coeff_gross DOUBLE PRECISION,
                final_coeff_net DOUBLE PRECISION,
                final_move_points DOUBLE PRECISION,
                outcome TEXT,
                pnl DOUBLE PRECISION,
                bank_before_settlement DOUBLE PRECISION,
                bank_after DOUBLE PRECISION,
                final_coeff_up DOUBLE PRECISION,
                final_coeff_down DOUBLE PRECISION,
                actual_ev_signal DOUBLE PRECISION,
                payout_ratio_signal DOUBLE PRECISION,
                settled_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Safe in-place migration from v1.0.x. Existing history is preserved.
        for statement in (
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS strategy_version TEXT NOT NULL DEFAULT 'legacy'",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS raw_expected_coeff_up DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS raw_expected_coeff_down DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS payout_correction_up DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS payout_correction_down DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS payout_bucket_up TEXT",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS payout_bucket_down TEXT",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS bank_before_settlement DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS final_coeff_up DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS final_coeff_down DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS actual_ev_signal DOUBLE PRECISION",
            "ALTER TABLE fusion_decisions ADD COLUMN IF NOT EXISTS payout_ratio_signal DOUBLE PRECISION",
        ):
            cur.execute(statement)

        # Backfill final hypothetical UP/DOWN payouts for already-settled v1.0.x
        # decisions. This immediately lets v1.1 learn from the retained history.
        cur.execute(
            """
            UPDATE fusion_decisions d SET
                final_coeff_up = CASE
                    WHEN r.bull_amount_bnb > 0
                    THEN (r.total_amount_bnb * (1.0 - %s)) / r.bull_amount_bnb
                    ELSE NULL END,
                final_coeff_down = CASE
                    WHEN r.bear_amount_bnb > 0
                    THEN (r.total_amount_bnb * (1.0 - %s)) / r.bear_amount_bnb
                    ELSE NULL END
            FROM fusion_rounds r
            WHERE d.betting_epoch = r.epoch
              AND d.settled = TRUE
              AND (d.final_coeff_up IS NULL OR d.final_coeff_down IS NULL)
            """,
            (settings.treasury_fee, settings.treasury_fee),
        )
        cur.execute(
            """
            UPDATE fusion_decisions SET
                actual_ev_signal = CASE
                    WHEN signal='UP' AND final_coeff_up IS NOT NULL
                        THEN probability_up * final_coeff_up - 1.0
                    WHEN signal='DOWN' AND final_coeff_down IS NOT NULL
                        THEN probability_down * final_coeff_down - 1.0
                    ELSE actual_ev_signal END,
                payout_ratio_signal = CASE
                    WHEN signal='UP' AND final_coeff_up IS NOT NULL
                         AND COALESCE(raw_expected_coeff_up, expected_coeff_up) > 1.0
                        THEN final_coeff_up / COALESCE(raw_expected_coeff_up, expected_coeff_up)
                    WHEN signal='DOWN' AND final_coeff_down IS NOT NULL
                         AND COALESCE(raw_expected_coeff_down, expected_coeff_down) > 1.0
                        THEN final_coeff_down / COALESCE(raw_expected_coeff_down, expected_coeff_down)
                    ELSE payout_ratio_signal END
            WHERE settled=TRUE
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fusion_state (
                id INTEGER PRIMARY KEY CHECK (id=1),
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
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            INSERT INTO fusion_state(id,start_bank,bank,peak_bank)
            VALUES(1,%s,%s,%s)
            ON CONFLICT(id) DO NOTHING
            """,
            (settings.start_bank, settings.start_bank, settings.start_bank),
        )
        conn.commit()


def upsert_rounds(rows: Iterable[RoundData], source: str = "pancake_final") -> int:
    count = 0
    with connect() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO fusion_rounds(
                    epoch,start_timestamp,lock_timestamp,close_timestamp,
                    lock_price,close_price,total_amount_bnb,bull_amount_bnb,bear_amount_bnb,
                    reward_base_bnb,reward_amount_bnb,oracle_called,actual_winner,
                    winner_coeff_gross,winner_coeff_net,move_points,source
                ) VALUES(
                    %(epoch)s,%(start_timestamp)s,%(lock_timestamp)s,%(close_timestamp)s,
                    %(lock_price)s,%(close_price)s,%(total_amount_bnb)s,%(bull_amount_bnb)s,%(bear_amount_bnb)s,
                    %(reward_base_bnb)s,%(reward_amount_bnb)s,%(oracle_called)s,%(actual_winner)s,
                    %(winner_coeff_gross)s,%(winner_coeff_net)s,%(move_points)s,%(source)s
                )
                ON CONFLICT(epoch) DO UPDATE SET
                    start_timestamp=EXCLUDED.start_timestamp,
                    lock_timestamp=EXCLUDED.lock_timestamp,
                    close_timestamp=EXCLUDED.close_timestamp,
                    lock_price=EXCLUDED.lock_price,
                    close_price=EXCLUDED.close_price,
                    total_amount_bnb=EXCLUDED.total_amount_bnb,
                    bull_amount_bnb=EXCLUDED.bull_amount_bnb,
                    bear_amount_bnb=EXCLUDED.bear_amount_bnb,
                    reward_base_bnb=EXCLUDED.reward_base_bnb,
                    reward_amount_bnb=EXCLUDED.reward_amount_bnb,
                    oracle_called=EXCLUDED.oracle_called,
                    actual_winner=EXCLUDED.actual_winner,
                    winner_coeff_gross=EXCLUDED.winner_coeff_gross,
                    winner_coeff_net=EXCLUDED.winner_coeff_net,
                    move_points=EXCLUDED.move_points,
                    source=EXCLUDED.source,
                    updated_at=NOW()
                """,
                {**row.to_dict(), "source": source},
            )
            count += 1
        conn.commit()
    return count


def load_rounds(limit: int = 1200) -> list[dict[str, Any]]:
    safe = max(1, min(int(limit), 10000))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM (
                SELECT * FROM fusion_rounds
                WHERE actual_winner IN ('UP','DOWN')
                ORDER BY epoch DESC LIMIT %s
            ) q ORDER BY epoch ASC
            """,
            (safe,),
        )
        return [dict(row) for row in cur.fetchall()]


def recent_rounds(limit: int = 30) -> list[dict[str, Any]]:
    safe = max(1, min(int(limit), settings.history_api_max_limit))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM fusion_rounds ORDER BY epoch DESC LIMIT %s", (safe,))
        return [dict(row) for row in cur.fetchall()]


def save_snapshot(snapshot: FusionSnapshot, binance: Optional[dict[str, Any]] = None) -> bool:
    bucket_size = max(1, settings.snapshot_bucket_seconds)
    bucket = int(round(snapshot.seconds_to_lock / bucket_size) * bucket_size)
    data = snapshot.to_dict()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fusion_snapshots(
                betting_epoch,live_epoch,bucket_seconds,chain_timestamp,seconds_to_lock,
                chainlink_price,oracle_updated_at,oracle_age_seconds,live_lock_price,
                live_move_signed,live_move_points,provisional_winner,betting_total_bnb,
                betting_bull_bnb,betting_bear_bnb,betting_bull_share_pct,
                betting_bear_share_pct,current_net_coeff_up,current_net_coeff_down,
                binance_json,snapshot_json
            ) VALUES(
                %(betting_epoch)s,%(live_epoch)s,%(bucket)s,%(chain_timestamp)s,%(seconds_to_lock)s,
                %(chainlink_price)s,%(oracle_updated_at)s,%(oracle_age_seconds)s,%(live_lock_price)s,
                %(live_move_signed)s,%(live_move_points)s,%(provisional_winner)s,%(betting_total_bnb)s,
                %(betting_bull_bnb)s,%(betting_bear_bnb)s,%(betting_bull_share_pct)s,
                %(betting_bear_share_pct)s,%(current_net_coeff_up)s,%(current_net_coeff_down)s,
                %(binance_json)s,%(snapshot_json)s
            ) ON CONFLICT(betting_epoch,bucket_seconds) DO UPDATE SET
                chain_timestamp=EXCLUDED.chain_timestamp,
                seconds_to_lock=EXCLUDED.seconds_to_lock,
                chainlink_price=EXCLUDED.chainlink_price,
                oracle_updated_at=EXCLUDED.oracle_updated_at,
                oracle_age_seconds=EXCLUDED.oracle_age_seconds,
                live_lock_price=EXCLUDED.live_lock_price,
                live_move_signed=EXCLUDED.live_move_signed,
                live_move_points=EXCLUDED.live_move_points,
                provisional_winner=EXCLUDED.provisional_winner,
                betting_total_bnb=EXCLUDED.betting_total_bnb,
                betting_bull_bnb=EXCLUDED.betting_bull_bnb,
                betting_bear_bnb=EXCLUDED.betting_bear_bnb,
                betting_bull_share_pct=EXCLUDED.betting_bull_share_pct,
                betting_bear_share_pct=EXCLUDED.betting_bear_share_pct,
                current_net_coeff_up=EXCLUDED.current_net_coeff_up,
                current_net_coeff_down=EXCLUDED.current_net_coeff_down,
                binance_json=COALESCE(EXCLUDED.binance_json,fusion_snapshots.binance_json),
                snapshot_json=EXCLUDED.snapshot_json
            """,
            {
                **data,
                "bucket": bucket,
                "binance_json": Jsonb(binance) if binance is not None else None,
                "snapshot_json": Jsonb(data),
            },
        )
        conn.commit()
        return True


def load_snapshots(epoch: int) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM fusion_snapshots WHERE betting_epoch=%s ORDER BY seconds_to_lock DESC",
            (int(epoch),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_state() -> dict[str, Any]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM fusion_state WHERE id=1")
        row = cur.fetchone()
        return dict(row) if row else {}


def get_decision(epoch: int) -> Optional[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM fusion_decisions WHERE betting_epoch=%s", (int(epoch),))
        row = cur.fetchone()
        return dict(row) if row else None


def insert_decision(data: dict[str, Any]) -> bool:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fusion_decisions(
                betting_epoch,live_epoch,strategy_version,locked_at_chain_timestamp,locked_at_seconds_to_lock,
                signal,probability_up,probability_down,
                raw_expected_coeff_up,raw_expected_coeff_down,payout_correction_up,payout_correction_down,
                payout_bucket_up,payout_bucket_down,
                expected_coeff_up,expected_coeff_down,
                ev_up,ev_down,selected_ev,agreement,decision_quality,stake,bank_before,
                components_json,weights_json,features_json,snapshot_json
            ) VALUES(
                %(betting_epoch)s,%(live_epoch)s,%(strategy_version)s,%(locked_at_chain_timestamp)s,%(locked_at_seconds_to_lock)s,
                %(signal)s,%(probability_up)s,%(probability_down)s,
                %(raw_expected_coeff_up)s,%(raw_expected_coeff_down)s,%(payout_correction_up)s,%(payout_correction_down)s,
                %(payout_bucket_up)s,%(payout_bucket_down)s,
                %(expected_coeff_up)s,%(expected_coeff_down)s,
                %(ev_up)s,%(ev_down)s,%(selected_ev)s,%(agreement)s,%(decision_quality)s,%(stake)s,%(bank_before)s,
                %(components_json)s,%(weights_json)s,%(features_json)s,%(snapshot_json)s
            ) ON CONFLICT(betting_epoch) DO NOTHING
            """,
            {
                **data,
                "components_json": Jsonb(data["components"]),
                "weights_json": Jsonb(data["weights"]),
                "features_json": Jsonb(data["features"]),
                "snapshot_json": Jsonb(data["snapshot"]),
            },
        )
        inserted = cur.rowcount == 1
        conn.commit()
        return inserted


def unsettled_decisions(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM fusion_decisions WHERE settled=FALSE ORDER BY betting_epoch ASC LIMIT %s",
            (max(1, min(limit, 500)),),
        )
        return [dict(row) for row in cur.fetchall()]


def _hypothetical_final_coefficients(row: RoundData) -> tuple[Optional[float], Optional[float]]:
    total = float(row.total_amount_bnb or 0.0)
    net_pool = total * (1.0 - settings.treasury_fee)
    up = net_pool / float(row.bull_amount_bnb) if row.bull_amount_bnb and row.bull_amount_bnb > 0 else None
    down = net_pool / float(row.bear_amount_bnb) if row.bear_amount_bnb and row.bear_amount_bnb > 0 else None
    return up, down


def settle_decision_atomic(epoch: int, row: RoundData) -> bool:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM fusion_decisions WHERE betting_epoch=%s FOR UPDATE", (int(epoch),))
        decision = cur.fetchone()
        if not decision or decision["settled"]:
            conn.rollback()
            return False
        cur.execute("SELECT * FROM fusion_state WHERE id=1 FOR UPDATE")
        state = cur.fetchone()
        signal = str(decision["signal"])
        winner = row.actual_winner or "DRAW"
        stake = float(decision["stake"])
        if winner == "DRAW":
            outcome = "DRAW"
            pnl = 0.0
        elif signal == winner:
            outcome = "WIN"
            coeff = float(row.winner_coeff_net or ((row.winner_coeff_gross or 1.0) * (1-settings.treasury_fee)))
            pnl = stake * (coeff - 1.0)
        else:
            outcome = "LOSS"
            pnl = -stake
        bank_before = float(state["bank"])
        bank_after = bank_before + pnl
        final_coeff_up, final_coeff_down = _hypothetical_final_coefficients(row)
        selected_final_coeff = final_coeff_up if signal == "UP" else final_coeff_down
        selected_probability = (
            float(decision["probability_up"])
            if signal == "UP"
            else float(decision["probability_down"])
        )
        actual_ev_signal = (
            selected_probability * selected_final_coeff - 1.0
            if selected_final_coeff is not None
            else None
        )
        raw_selected_coeff = (
            decision.get("raw_expected_coeff_up")
            if signal == "UP"
            else decision.get("raw_expected_coeff_down")
        )
        if raw_selected_coeff is None:
            raw_selected_coeff = (
                decision.get("expected_coeff_up")
                if signal == "UP"
                else decision.get("expected_coeff_down")
            )
        payout_ratio_signal = (
            selected_final_coeff / float(raw_selected_coeff)
            if selected_final_coeff is not None
            and raw_selected_coeff is not None
            and float(raw_selected_coeff) > 1.0
            else None
        )
        wins = int(state["wins"]) + (1 if outcome == "WIN" else 0)
        losses = int(state["losses"]) + (1 if outcome == "LOSS" else 0)
        draws = int(state["draws"]) + (1 if outcome == "DRAW" else 0)
        trades = int(state["trades_count"]) + 1
        current_streak = int(state["current_loss_streak"]) + 1 if outcome == "LOSS" else 0
        max_streak = max(int(state["max_loss_streak"]), current_streak)
        peak_bank = max(float(state["peak_bank"]), bank_after)
        drawdown = peak_bank - bank_after
        max_drawdown = max(float(state["max_drawdown"]), drawdown)
        cur.execute(
            """
            UPDATE fusion_decisions SET
                settled=TRUE,final_winner=%s,final_coeff_gross=%s,final_coeff_net=%s,
                final_move_points=%s,outcome=%s,pnl=%s,
                bank_before_settlement=%s,bank_after=%s,
                final_coeff_up=%s,final_coeff_down=%s,
                actual_ev_signal=%s,payout_ratio_signal=%s,
                settled_at=NOW(),updated_at=NOW()
            WHERE betting_epoch=%s
            """,
            (
                winner,row.winner_coeff_gross,row.winner_coeff_net,row.move_points,
                outcome,pnl,bank_before,bank_after,
                final_coeff_up,final_coeff_down,actual_ev_signal,payout_ratio_signal,
                int(epoch),
            ),
        )
        cur.execute(
            """
            UPDATE fusion_state SET
                bank=%s,wins=%s,losses=%s,draws=%s,trades_count=%s,
                current_loss_streak=%s,max_loss_streak=%s,peak_bank=%s,max_drawdown=%s,
                last_settled_epoch=%s,updated_at=NOW()
            WHERE id=1
            """,
            (
                bank_after,wins,losses,draws,trades,current_streak,max_streak,
                peak_bank,max_drawdown,int(epoch),
            ),
        )
        conn.commit()
        return True


def decision_history(limit: int = 30, offset: int = 0, ascending: bool = False) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), settings.history_api_max_limit))
    safe_offset = max(0, int(offset))
    order = "ASC" if ascending else "DESC"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM fusion_decisions ORDER BY betting_epoch {order} LIMIT %s OFFSET %s",
            (safe_limit, safe_offset),
        )
        return [dict(row) for row in cur.fetchall()]


def decision_count() -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM fusion_decisions")
        row = cur.fetchone()
        return int(row["count"])


def settled_component_history(limit: int = 300) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT betting_epoch,components_json,final_winner
            FROM fusion_decisions
            WHERE settled=TRUE AND final_winner IN ('UP','DOWN')
            ORDER BY betting_epoch DESC LIMIT %s
            """,
            (max(1, min(limit, 5000)),),
        )
        return [dict(row) for row in cur.fetchall()]


def payout_calibration_history(limit: int = 300) -> list[dict[str, Any]]:
    safe = max(1, min(int(limit), 5000))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT betting_epoch,
                   raw_expected_coeff_up,raw_expected_coeff_down,
                   expected_coeff_up,expected_coeff_down,
                   final_coeff_up,final_coeff_down
            FROM fusion_decisions
            WHERE settled=TRUE
              AND final_coeff_up IS NOT NULL
              AND final_coeff_down IS NOT NULL
            ORDER BY betting_epoch DESC LIMIT %s
            """,
            (safe,),
        )
        return [dict(row) for row in cur.fetchall()]


def strategy_performance() -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(strategy_version, 'legacy') AS strategy_version,
                   COUNT(*) FILTER (WHERE settled=TRUE) AS trades,
                   COUNT(*) FILTER (WHERE outcome='WIN') AS wins,
                   COUNT(*) FILTER (WHERE outcome='LOSS') AS losses,
                   COUNT(*) FILTER (WHERE outcome='DRAW') AS draws,
                   COALESCE(SUM(pnl) FILTER (WHERE settled=TRUE), 0) AS profit,
                   MIN(betting_epoch) AS first_epoch,
                   MAX(betting_epoch) AS last_epoch
            FROM fusion_decisions
            GROUP BY COALESCE(strategy_version, 'legacy')
            ORDER BY MIN(betting_epoch)
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        trades = int(row.get("trades") or 0)
        wins = int(row.get("wins") or 0)
        row["win_rate"] = wins / trades if trades else 0.0
    return rows


def export_csv() -> str:
    rows = decision_history(limit=settings.history_api_max_limit, ascending=True)
    output = io.StringIO()
    fields = [
        "betting_epoch","strategy_version","signal","probability_up","probability_down",
        "raw_expected_coeff_up","raw_expected_coeff_down",
        "payout_correction_up","payout_correction_down",
        "payout_bucket_up","payout_bucket_down",
        "expected_coeff_up","expected_coeff_down",
        "ev_up","ev_down","selected_ev","agreement","decision_quality","stake",
        "bank_before","bank_before_settlement","bank_after",
        "final_winner","final_coeff_net","final_coeff_up","final_coeff_down",
        "actual_ev_signal","payout_ratio_signal","outcome","pnl","locked_at_seconds_to_lock",
        "created_at","settled_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in fields})
    return output.getvalue()


def reset_all() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE fusion_decisions,fusion_snapshots,fusion_rounds RESTART IDENTITY")
        cur.execute("DELETE FROM fusion_state WHERE id=1")
        cur.execute(
            "INSERT INTO fusion_state(id,start_bank,bank,peak_bank) VALUES(1,%s,%s,%s)",
            (settings.start_bank,settings.start_bank,settings.start_bank),
        )
        conn.commit()
