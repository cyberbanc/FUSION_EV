from __future__ import annotations

import math
import time
from typing import Any, Optional

import httpx

from .config import settings


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


class BinanceClient:
    def __init__(self) -> None:
        self.base_urls = list(settings.binance_base_urls)
        self.symbol = settings.binance_symbol
        self.last_error: Optional[str] = None
        self.last_base_url: Optional[str] = None

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        errors: list[str] = []
        for base in self.base_urls:
            try:
                with httpx.Client(timeout=settings.binance_timeout_seconds) as client:
                    response = client.get(base.rstrip("/") + path, params=params)
                    response.raise_for_status()
                    self.last_base_url = base
                    return response.json()
            except Exception as exc:
                errors.append(f"{base}: {type(exc).__name__}: {exc}")
        self.last_error = " | ".join(errors)
        raise RuntimeError("All Binance market-data endpoints failed: " + self.last_error)

    @staticmethod
    def _price_at_or_before(trades: list[dict[str, Any]], timestamp_ms: int) -> Optional[float]:
        candidates = [t for t in trades if int(t.get("T", 0)) <= timestamp_ms]
        if not candidates:
            return None
        return _safe_float(candidates[-1].get("p"), 0.0) or None

    def snapshot(self) -> dict[str, Any]:
        if not settings.binance_enabled:
            return {"available": False, "reason": "BINANCE_ENABLED=false"}
        try:
            ticker = self._get("/api/v3/ticker/price", {"symbol": self.symbol})
            depth = self._get("/api/v3/depth", {"symbol": self.symbol, "limit": 100})
            trades = self._get("/api/v3/aggTrades", {"symbol": self.symbol, "limit": 1000})
            klines_1m = self._get(
                "/api/v3/klines", {"symbol": self.symbol, "interval": "1m", "limit": 60}
            )
            klines_5m = self._get(
                "/api/v3/klines", {"symbol": self.symbol, "interval": "5m", "limit": 60}
            )
            price = _safe_float(ticker.get("price"))
            trades = sorted(trades, key=lambda item: int(item.get("T", 0)))
            last_ms = int(trades[-1].get("T", int(time.time() * 1000))) if trades else int(time.time() * 1000)
            returns_bp: dict[str, Optional[float]] = {}
            for seconds in (15, 30, 60):
                old = self._price_at_or_before(trades, last_ms - seconds * 1000)
                returns_bp[str(seconds)] = ((price / old - 1.0) * 10000.0) if old and price else None

            buy_notional = 0.0
            sell_notional = 0.0
            cutoff = last_ms - 60000
            for trade in trades:
                if int(trade.get("T", 0)) < cutoff:
                    continue
                notional = _safe_float(trade.get("p")) * _safe_float(trade.get("q"))
                if bool(trade.get("m")):
                    sell_notional += notional
                else:
                    buy_notional += notional
            taker_total = buy_notional + sell_notional
            taker_imbalance = (buy_notional - sell_notional) / taker_total if taker_total > 0 else 0.0

            bids = depth.get("bids", [])[:20]
            asks = depth.get("asks", [])[:20]
            bid_notional = sum(_safe_float(p) * _safe_float(q) for p, q in bids)
            ask_notional = sum(_safe_float(p) * _safe_float(q) for p, q in asks)
            book_total = bid_notional + ask_notional
            book_imbalance = (bid_notional - ask_notional) / book_total if book_total > 0 else 0.0

            closes_1m = [_safe_float(row[4]) for row in klines_1m if len(row) > 4]
            closes_5m = [_safe_float(row[4]) for row in klines_5m if len(row) > 4]
            ema9_1m, ema21_1m = _ema(closes_1m, 9), _ema(closes_1m, 21)
            ema9_5m, ema21_5m = _ema(closes_5m, 9), _ema(closes_5m, 21)
            ema_spread_1m_bp = (
                (ema9_1m / ema21_1m - 1.0) * 10000.0 if ema9_1m and ema21_1m else None
            )
            ema_spread_5m_bp = (
                (ema9_5m / ema21_5m - 1.0) * 10000.0 if ema9_5m and ema21_5m else None
            )
            return {
                "available": True,
                "symbol": self.symbol,
                "price": price,
                "returns_bp": returns_bp,
                "taker_buy_notional_60s": buy_notional,
                "taker_sell_notional_60s": sell_notional,
                "taker_imbalance_60s": taker_imbalance,
                "book_bid_notional_top20": bid_notional,
                "book_ask_notional_top20": ask_notional,
                "book_imbalance_top20": book_imbalance,
                "ema_spread_1m_bp": ema_spread_1m_bp,
                "ema_spread_5m_bp": ema_spread_5m_bp,
                "base_url": self.last_base_url,
                "updated_at_ms": last_ms,
            }
        except Exception as exc:
            return {
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "last_error": self.last_error,
            }
