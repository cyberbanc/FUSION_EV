from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _float_tuple(name: str, default: str, expected: int) -> tuple[float, ...]:
    values: list[float] = []
    for item in _list(name, default):
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            continue
    fallback = tuple(float(item) for item in default.split(","))
    return tuple(values) if len(values) == expected else fallback


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "").strip()
    prediction_contract: str = os.getenv(
        "PREDICTION_CONTRACT", "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA"
    ).strip()
    bsc_rpc_urls: tuple[str, ...] = tuple(
        _list("BSC_RPC_URLS", os.getenv("BSC_RPC_URL", ""))
    )
    rpc_timeout_seconds: float = _float("RPC_TIMEOUT_SECONDS", 12.0)
    rpc_cooldown_seconds: int = _int("RPC_COOLDOWN_SECONDS", 30)
    price_decimals: int = _int("PRICE_DECIMALS", 8)

    worker_enabled: bool = _bool("WORKER_ENABLED", True)
    poll_seconds: float = _float("POLL_SECONDS", 3.0)
    prelock_seconds: int = _int("PRELOCK_SECONDS", 40)
    min_decision_seconds: int = _int("MIN_DECISION_SECONDS", 8)
    snapshot_start_seconds: int = _int("SNAPSHOT_START_SECONDS", 100)
    snapshot_bucket_seconds: int = _int("SNAPSHOT_BUCKET_SECONDS", 5)
    sync_closed_seconds: int = _int("SYNC_CLOSED_SECONDS", 30)
    bootstrap_lookback: int = _int("BOOTSTRAP_LOOKBACK", 160)
    sync_recent_lookback: int = _int("SYNC_RECENT_LOOKBACK", 16)

    start_bank: float = _float("START_BANK", 500.0)
    treasury_fee: float = _float("TREASURY_FEE", 0.03)
    base_stake: float = _float("BASE_STAKE", 10.0)
    medium_stake: float = _float("MEDIUM_STAKE", 15.0)
    high_stake: float = _float("HIGH_STAKE", 20.0)

    # Variable stakes remain disabled by default during the independent v1.2 test.
    variable_stake_enabled: bool = _bool("VARIABLE_STAKE_ENABLED", False)
    ev_medium_threshold: float = _float("EV_MEDIUM_THRESHOLD", 0.30)
    ev_high_threshold: float = _float("EV_HIGH_THRESHOLD", 0.50)
    medium_agreement: float = _float("MEDIUM_AGREEMENT", 0.60)
    high_agreement: float = _float("HIGH_AGREEMENT", 0.70)
    min_trades_medium_stake: int = _int(
        "MIN_TRADES_MEDIUM_STAKE", _int("MIN_TRADES_FOR_VARIABLE_STAKE", 200)
    )
    min_trades_high_stake: int = _int("MIN_TRADES_HIGH_STAKE", 300)
    probability_shrink: float = _float("PROBABILITY_SHRINK", 0.75)

    # EV is trusted only when it is materially positive. A payout-driven choice
    # against the more probable side needs an even larger edge and agreement.
    strong_ev_threshold: float = _float("STRONG_EV_THRESHOLD", 0.30)
    ev_reversal_min_ev: float = _float("EV_REVERSAL_MIN_EV", 0.35)
    ev_reversal_min_agreement: float = _float(
        "EV_REVERSAL_MIN_AGREEMENT", 0.30
    )
    crowd_binance_fallback_enabled: bool = _bool(
        "CROWD_BINANCE_FALLBACK_ENABLED", True
    )
    fallback_component_margin: float = _float("FALLBACK_COMPONENT_MARGIN", 0.005)

    # v1.3 removes the mandatory bet-every-round rule. A decision is still
    # recorded every round for diagnostics and payout learning, but stake is
    # zero unless the selected side has positive corrected EV.
    trade_filter_enabled: bool = _bool("TRADE_FILTER_ENABLED", True)
    min_trade_ev: float = _float("MIN_TRADE_EV", 0.0)
    require_payout_bucket_ready: bool = _bool(
        "REQUIRE_PAYOUT_BUCKET_READY", True
    )

    # Static pool estimate before the adaptive bucket correction.
    payout_haircut: float = _float("PAYOUT_HAIRCUT", 0.85)
    payout_cap: float = _float("PAYOUT_CAP", 4.0)
    neutral_net_coefficient: float = _float("NEUTRAL_NET_COEFFICIENT", 1.94)
    min_pool_bnb: float = _float("MIN_POOL_BNB", 0.10)
    min_side_pool_bnb: float = _float("MIN_SIDE_POOL_BNB", 0.03)

    # v1.2 learns payout retention independently by side and raw coefficient
    # band: <1.50, 1.50-2.00, 2.00-2.50, 2.50-3.00, >=3.00.
    payout_bucket_edges: tuple[float, ...] = _float_tuple(
        "PAYOUT_BUCKET_EDGES", "1.50,2.00,2.50,3.00", 4
    )
    payout_bucket_initial_up: tuple[float, ...] = _float_tuple(
        "PAYOUT_BUCKET_INITIAL_UP", "0.90,0.82,0.72,0.65,0.60", 5
    )
    payout_bucket_initial_down: tuple[float, ...] = _float_tuple(
        "PAYOUT_BUCKET_INITIAL_DOWN", "0.90,0.84,0.78,0.74,0.70", 5
    )
    payout_bucket_max_up: tuple[float, ...] = _float_tuple(
        "PAYOUT_BUCKET_MAX_UP", "0.95,0.90,0.82,0.72,0.60", 5
    )
    payout_bucket_max_down: tuple[float, ...] = _float_tuple(
        "PAYOUT_BUCKET_MAX_DOWN", "0.95,0.92,0.86,0.78,0.70", 5
    )
    payout_calibration_lookback: int = _int("PAYOUT_CALIBRATION_LOOKBACK", 500)
    payout_bucket_min_samples: int = _int("PAYOUT_BUCKET_MIN_SAMPLES", 15)
    payout_calibration_quantile: float = _float("PAYOUT_CALIBRATION_QUANTILE", 0.25)
    payout_correction_min: float = _float("PAYOUT_CORRECTION_MIN", 0.45)
    payout_correction_max: float = _float("PAYOUT_CORRECTION_MAX", 0.95)

    binance_enabled: bool = _bool("BINANCE_ENABLED", True)
    binance_symbol: str = os.getenv("BINANCE_SYMBOL", "BNBUSDT").strip().upper()
    binance_base_urls: tuple[str, ...] = tuple(
        _list(
            "BINANCE_BASE_URLS",
            "https://data-api.binance.vision,https://api.binance.com,https://api1.binance.com",
        )
    )
    binance_timeout_seconds: float = _float("BINANCE_TIMEOUT_SECONDS", 6.0)

    weight_price: float = _float("WEIGHT_PRICE", 0.40)
    weight_binance: float = _float("WEIGHT_BINANCE", 0.25)
    weight_crowd: float = _float("WEIGHT_CROWD", 0.35)
    weight_m9: float = _float("WEIGHT_M9", 0.0)
    weight_pattern: float = _float("WEIGHT_PATTERN", 0.0)
    adaptive_weight_lookback: int = _int("ADAPTIVE_WEIGHT_LOOKBACK", 100)
    pattern_min_count: int = _int("PATTERN_MIN_COUNT", 20)
    pattern_max_length: int = _int("PATTERN_MAX_LENGTH", 5)
    m9_history_limit: int = _int("M9_HISTORY_LIMIT", 1200)

    history_api_max_limit: int = _int("HISTORY_API_MAX_LIMIT", 100000)


settings = Settings()
