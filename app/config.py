from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _csv_floats(name: str, default: Iterable[float]) -> tuple[float, ...]:
    raw = os.getenv(name)
    if not raw:
        return tuple(default)
    values: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        values.append(float(part))
    return tuple(values) or tuple(default)


def _csv_strings(name: str, default: Iterable[str]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return tuple(default)
    values = tuple(x.strip() for x in raw.split(",") if x.strip())
    return values or tuple(default)


@dataclass(frozen=True)
class Settings:
    version: str = os.getenv("MODEL_VERSION", "1.3.6.2")
    database_url: str = os.getenv("DATABASE_URL", "")
    bsc_rpc_urls: tuple[str, ...] = _csv_strings(
        "BSC_RPC_URLS",
        (os.getenv("BSC_RPC_URL", "https://bsc-dataseed.bnbchain.org"),),
    )
    prediction_contract: str = os.getenv(
        "PREDICTION_CONTRACT",
        "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA",
    )
    worker_enabled: bool = _bool("WORKER_ENABLED", True)
    poll_seconds: float = _float("POLL_SECONDS", 3.0)
    prelock_seconds: int = _int("PRELOCK_SECONDS", 40)
    min_decision_seconds: int = _int("MIN_DECISION_SECONDS", 8)
    snapshot_start_seconds: int = _int("SNAPSHOT_START_SECONDS", 100)
    snapshot_bucket_seconds: int = _int("SNAPSHOT_BUCKET_SECONDS", 5)
    sync_closed_seconds: int = _int("SYNC_CLOSED_SECONDS", 30)
    sync_recent_lookback: int = _int("SYNC_RECENT_LOOKBACK", 16)
    bootstrap_lookback: int = _int("BOOTSTRAP_LOOKBACK", 160)
    start_bank: float = _float("START_BANK", 500.0)
    treasury_fee: float = _float("TREASURY_FEE", 0.03)

    # EV-tiered paper stake. The size depends only on current selected EV,
    # never on the previous trade result.
    stake_low: float = _float("STAKE_LOW", 5.0)
    stake_mid: float = _float("STAKE_MID", 10.0)
    stake_high: float = _float("STAKE_HIGH", 15.0)
    stake_mid_ev: float = _float("STAKE_MID_EV", 0.0)
    stake_high_ev: float = _float("STAKE_HIGH_EV", 0.05)

    probability_shrink: float = _float("PROBABILITY_SHRINK", 0.75)
    payout_haircut: float = _float("PAYOUT_HAIRCUT", 0.85)
    payout_cap: float = _float("PAYOUT_CAP", 4.0)
    neutral_net_coefficient: float = _float("NEUTRAL_NET_COEFFICIENT", 1.94)
    min_pool_bnb: float = _float("MIN_POOL_BNB", 0.10)
    min_side_pool_bnb: float = _float("MIN_SIDE_POOL_BNB", 0.03)
    payout_bucket_edges: tuple[float, ...] = _csv_floats(
        "PAYOUT_BUCKET_EDGES", (1.50, 2.00, 2.50, 3.00)
    )
    payout_bucket_initial_up: tuple[float, ...] = _csv_floats(
        "PAYOUT_BUCKET_INITIAL_UP", (0.90, 0.82, 0.72, 0.65, 0.60)
    )
    payout_bucket_initial_down: tuple[float, ...] = _csv_floats(
        "PAYOUT_BUCKET_INITIAL_DOWN", (0.90, 0.84, 0.78, 0.74, 0.70)
    )
    payout_bucket_max_up: tuple[float, ...] = _csv_floats(
        "PAYOUT_BUCKET_MAX_UP", (0.95, 0.90, 0.82, 0.72, 0.60)
    )
    payout_bucket_max_down: tuple[float, ...] = _csv_floats(
        "PAYOUT_BUCKET_MAX_DOWN", (0.95, 0.92, 0.86, 0.78, 0.70)
    )
    payout_calibration_lookback: int = _int("PAYOUT_CALIBRATION_LOOKBACK", 500)
    payout_bucket_min_samples: int = _int("PAYOUT_BUCKET_MIN_SAMPLES", 15)
    payout_calibration_quantile: float = _float("PAYOUT_CALIBRATION_QUANTILE", 0.25)
    payout_correction_min: float = _float("PAYOUT_CORRECTION_MIN", 0.45)
    payout_correction_max: float = _float("PAYOUT_CORRECTION_MAX", 0.95)

    binance_enabled: bool = _bool("BINANCE_ENABLED", True)
    binance_symbol: str = os.getenv("BINANCE_SYMBOL", "BNBUSDT")
    binance_base_urls: tuple[str, ...] = _csv_strings(
        "BINANCE_BASE_URLS",
        (
            "https://data-api.binance.vision",
            "https://api.binance.com",
            "https://api1.binance.com",
        ),
    )
    binance_timeout_seconds: float = _float("BINANCE_TIMEOUT_SECONDS", 6.0)

    weight_price: float = _float("WEIGHT_PRICE", 0.40)
    weight_binance: float = _float("WEIGHT_BINANCE", 0.25)
    weight_crowd: float = _float("WEIGHT_CROWD", 0.35)
    weight_m9: float = _float("WEIGHT_M9", 0.0)
    weight_pattern: float = _float("WEIGHT_PATTERN", 0.0)
    m9_history_limit: int = _int("M9_HISTORY_LIMIT", 1200)
    pattern_min_count: int = _int("PATTERN_MIN_COUNT", 20)
    pattern_max_length: int = _int("PATTERN_MAX_LENGTH", 5)

    # Dynamic shadow filter.
    trade_filter_enabled: bool = _bool("TRADE_FILTER_ENABLED", True)
    min_trade_ev: float = _float("MIN_TRADE_EV", -0.06)
    require_payout_bucket_ready: bool = _bool("REQUIRE_PAYOUT_BUCKET_READY", True)
    shadow_filter_enabled: bool = _bool("SHADOW_FILTER_ENABLED", True)
    shadow_stake: float = _float("SHADOW_STAKE", 10.0)
    shadow_recent_window: int = _int("SHADOW_RECENT_WINDOW", 8)
    shadow_recent_min_pnl: float = _float("SHADOW_RECENT_MIN_PNL", -30.0)
    shadow_require_confirmation: bool = _bool("SHADOW_REQUIRE_CONFIRMATION", False)
    shadow_source_lookback: int = _int("SHADOW_SOURCE_LOOKBACK", 20)
    shadow_side_lookback: int = _int("SHADOW_SIDE_LOOKBACK", 20)
    shadow_min_samples: int = _int("SHADOW_MIN_SAMPLES", 20)
    shadow_side_min_samples: int = _int("SHADOW_SIDE_MIN_SAMPLES", 10)
    shadow_min_profit_factor: float = _float("SHADOW_MIN_PROFIT_FACTOR", 1.10)
    shadow_min_win_rate: float = _float("SHADOW_MIN_WIN_RATE", 0.50)
    shadow_negative_min_win_rate: float = _float("SHADOW_NEGATIVE_MIN_WIN_RATE", 0.52)
    shadow_disable_loss_streak: int = _int("SHADOW_DISABLE_LOSS_STREAK", 0)
    shadow_disable_last10_pnl: float = _float("SHADOW_DISABLE_LAST10_PNL", -20.0)
    shadow_disable_last10_win_rate: float = _float("SHADOW_DISABLE_LAST10_WIN_RATE", 0.40)
    shadow_strong_ev_warmup: float = _float("SHADOW_STRONG_EV_WARMUP", 0.05)
    # Quality confirmation for each source + direction pair.
    quality_window: int = _int("QUALITY_WINDOW", 30)
    quality_min_samples: int = _int("QUALITY_MIN_SAMPLES", 10)
    quality_min_win_rate: float = _float("QUALITY_MIN_WIN_RATE", 0.45)
    quality_min_profit_factor: float = _float("QUALITY_MIN_PROFIT_FACTOR", 0.85)

    # Drawdown guard: after each completed block of N consecutive real losses,
    # skip one newly created betting decision.
    cooldown_loss_streak_trigger: int = _int("COOLDOWN_LOSS_STREAK_TRIGGER", 3)
    cooldown_rounds: int = _int("COOLDOWN_ROUNDS", 1)
    negative_fallback_shadow_enabled: bool = _bool(
        "NEGATIVE_FALLBACK_SHADOW_ENABLED", True
    )
    history_api_max_limit: int = _int("HISTORY_API_MAX_LIMIT", 100000)


SETTINGS = Settings()
