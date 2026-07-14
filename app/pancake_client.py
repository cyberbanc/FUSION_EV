from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, TypeVar

from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:  # pragma: no cover
    ExtraDataToPOAMiddleware = None

from .abi import AGGREGATOR_V3_ABI, PREDICTION_ABI
from .config import settings
from .models import FusionSnapshot, RoundData

T = TypeVar("T")


class PancakeClient:
    def __init__(self) -> None:
        if not settings.bsc_rpc_urls:
            raise RuntimeError("BSC_RPC_URL or BSC_RPC_URLS is missing")
        self.contract_address = Web3.to_checksum_address(settings.prediction_contract)
        self.rpc_urls = list(settings.bsc_rpc_urls)
        self._providers: list[tuple[Web3, Any]] = []
        self._index = 0
        self._lock = threading.Lock()
        self._unavailable_until: dict[str, float] = {}
        self._last_errors: dict[str, str] = {}
        self._oracle_address: Optional[str] = None
        self._oracle_decimals: Optional[int] = None
        for url in self.rpc_urls:
            web3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": settings.rpc_timeout_seconds}))
            if ExtraDataToPOAMiddleware is not None:
                try:
                    web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                except Exception:
                    pass
            contract = web3.eth.contract(address=self.contract_address, abi=PREDICTION_ABI)
            self._providers.append((web3, contract))

    def _call(self, function: Callable[[Web3, Any], T]) -> T:
        errors: list[str] = []
        now = time.time()
        with self._lock:
            start = self._index
            order = [(start + i) % len(self._providers) for i in range(len(self._providers))]
        for idx in order:
            url = self.rpc_urls[idx]
            if self._unavailable_until.get(url, 0) > now:
                continue
            web3, contract = self._providers[idx]
            try:
                result = function(web3, contract)
                with self._lock:
                    self._index = idx
                return result
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                errors.append(f"{url}: {message}")
                self._last_errors[url] = message
                self._unavailable_until[url] = now + settings.rpc_cooldown_seconds
        raise RuntimeError("All BSC RPC endpoints failed: " + " | ".join(errors))

    @property
    def active_rpc_url(self) -> str:
        return self.rpc_urls[self._index]

    def rpc_status(self) -> dict[str, Any]:
        now = time.time()
        return {
            "active_rpc_url": self.active_rpc_url,
            "rpc_count": len(self.rpc_urls),
            "cooldown": {
                url: max(0.0, round(until - now, 1))
                for url, until in self._unavailable_until.items()
                if until > now
            },
            "last_errors": self._last_errors,
        }

    def is_connected(self) -> bool:
        try:
            return bool(self._call(lambda web3, _contract: web3.is_connected()))
        except Exception:
            return False

    def chain_timestamp(self) -> int:
        return int(self._call(lambda web3, _contract: web3.eth.get_block("latest")["timestamp"]))

    def current_epoch(self) -> int:
        return int(self._call(lambda _web3, contract: contract.functions.currentEpoch().call()))

    def oracle_address(self) -> str:
        if self._oracle_address:
            return self._oracle_address
        address = self._call(lambda _web3, contract: contract.functions.oracle().call())
        self._oracle_address = Web3.to_checksum_address(address)
        return self._oracle_address

    def chainlink_price(self) -> dict[str, Any]:
        oracle = self.oracle_address()

        def read(web3: Web3, _contract: Any) -> tuple[int, int, int, int, int, int]:
            feed = web3.eth.contract(address=oracle, abi=AGGREGATOR_V3_ABI)
            decimals = self._oracle_decimals
            if decimals is None:
                decimals = int(feed.functions.decimals().call())
                self._oracle_decimals = decimals
            round_id, answer, started_at, updated_at, answered_in_round = (
                feed.functions.latestRoundData().call()
            )
            return decimals, int(round_id), int(answer), int(started_at), int(updated_at), int(answered_in_round)

        decimals, round_id, answer, started_at, updated_at, answered_in_round = self._call(read)
        if answer <= 0:
            raise RuntimeError("Chainlink returned non-positive price")
        return {
            "price": answer / (10 ** decimals),
            "round_id": round_id,
            "started_at": started_at,
            "updated_at": updated_at,
            "answered_in_round": answered_in_round,
            "decimals": decimals,
            "oracle_address": oracle,
        }

    @staticmethod
    def _coefficient(total: int, side: int) -> Optional[float]:
        return (total / side) if total > 0 and side > 0 else None

    def round(self, epoch: int) -> RoundData:
        r = self._call(lambda _web3, contract: contract.functions.rounds(int(epoch)).call())
        total = int(r[8])
        bull = int(r[9])
        bear = int(r[10])
        reward_base = int(r[11])
        reward_amount = int(r[12])
        lock_raw = int(r[4])
        close_raw = int(r[5])
        oracle_called = bool(r[13])
        scale = 10 ** settings.price_decimals
        lock_price = lock_raw / scale if lock_raw > 0 else None
        close_price = close_raw / scale if close_raw > 0 else None
        winner: Optional[str] = None
        gross: Optional[float] = None
        net: Optional[float] = None
        move: Optional[float] = None
        if oracle_called and lock_price is not None and close_price is not None:
            if close_price > lock_price:
                winner = "UP"
                gross = self._coefficient(total, bull)
            elif close_price < lock_price:
                winner = "DOWN"
                gross = self._coefficient(total, bear)
            else:
                winner = "DRAW"
            move = abs(close_price - lock_price)
            if winner in {"UP", "DOWN"}:
                if reward_amount > 0 and reward_base > 0:
                    net = reward_amount / reward_base
                elif gross is not None:
                    net = gross * (1.0 - settings.treasury_fee)
        return RoundData(
            epoch=int(r[0]),
            start_timestamp=int(r[1]),
            lock_timestamp=int(r[2]),
            close_timestamp=int(r[3]),
            lock_price=lock_price,
            close_price=close_price,
            total_amount_bnb=total / 1e18,
            bull_amount_bnb=bull / 1e18,
            bear_amount_bnb=bear / 1e18,
            reward_base_bnb=reward_base / 1e18,
            reward_amount_bnb=reward_amount / 1e18,
            oracle_called=oracle_called,
            actual_winner=winner,
            winner_coeff_gross=gross,
            winner_coeff_net=net,
            move_points=move,
        )

    def closed_rounds(self, lookback: int) -> list[RoundData]:
        current = self.current_epoch()
        rows: list[RoundData] = []
        start = max(1, current - max(1, lookback) - 2)
        for epoch in range(start, current):
            try:
                row = self.round(epoch)
            except Exception:
                continue
            if row.oracle_called and row.actual_winner in {"UP", "DOWN", "DRAW"}:
                rows.append(row)
        return rows

    def snapshot(self) -> FusionSnapshot:
        current = self.current_epoch()
        betting = self.round(current)
        live_epoch = current - 1
        live = self.round(live_epoch)
        now = self.chain_timestamp()
        oracle = self.chainlink_price()
        price = float(oracle["price"])
        seconds_to_lock = int(betting.lock_timestamp - now)
        total = betting.bull_amount_bnb + betting.bear_amount_bnb
        bull_share = betting.bull_amount_bnb / total * 100 if total > 0 else 50.0
        bear_share = betting.bear_amount_bnb / total * 100 if total > 0 else 50.0
        gross_up = total / betting.bull_amount_bnb if betting.bull_amount_bnb > 0 else None
        gross_down = total / betting.bear_amount_bnb if betting.bear_amount_bnb > 0 else None
        net_up = gross_up * (1 - settings.treasury_fee) if gross_up else None
        net_down = gross_down * (1 - settings.treasury_fee) if gross_down else None
        move_signed: Optional[float] = None
        move_points: Optional[float] = None
        provisional: Optional[str] = None
        if live.lock_price is not None:
            move_signed = price - live.lock_price
            move_points = abs(move_signed)
            provisional = "UP" if move_signed > 0 else "DOWN" if move_signed < 0 else "DRAW"
        return FusionSnapshot(
            chain_timestamp=now,
            current_epoch=current,
            betting_epoch=current,
            live_epoch=live_epoch,
            seconds_to_lock=seconds_to_lock,
            decision_window=settings.min_decision_seconds <= seconds_to_lock <= settings.prelock_seconds,
            safe_to_decide=seconds_to_lock >= settings.min_decision_seconds,
            chainlink_price=price,
            oracle_updated_at=int(oracle["updated_at"]),
            oracle_age_seconds=max(0, now - int(oracle["updated_at"])),
            oracle_round_id=int(oracle["round_id"]),
            live_lock_price=live.lock_price,
            live_move_signed=move_signed,
            live_move_points=move_points,
            provisional_winner=provisional,
            betting_total_bnb=total,
            betting_bull_bnb=betting.bull_amount_bnb,
            betting_bear_bnb=betting.bear_amount_bnb,
            betting_bull_share_pct=bull_share,
            betting_bear_share_pct=bear_share,
            current_gross_coeff_up=gross_up,
            current_gross_coeff_down=gross_down,
            current_net_coeff_up=net_up,
            current_net_coeff_down=net_down,
            live_round=live.to_dict(),
            betting_round=betting.to_dict(),
            rpc_status=self.rpc_status(),
        )


_CLIENT: Optional[PancakeClient] = None


def from_env() -> PancakeClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = PancakeClient()
    return _CLIENT
