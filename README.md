# M9 FUSION EV 1.3.3 — Negative fallback filter

Paper-бот для PancakeSwap Prediction BNB. Решение фиксируется примерно в T−40. Версия 1.3.3 полностью сохраняет архитектуру 1.3.2 и добавляет отдельную блокировку убыточной категории `NEGATIVE_EV_PROBABILITY_FALLBACK`.

## Главное изменение 1.3.3

```text
selection_reason = NEGATIVE_EV_PROBABILITY_FALLBACK
NEGATIVE_FALLBACK_ENABLED=false
    → NO_TRADE, даже если selected_ev > MIN_TRADE_EV
```

Другие категории не блокируются этим правилом:

```text
WEAK_EV_CROWD_BINANCE_FALLBACK
WEAK_EV_PROBABILITY_FALLBACK
BEST_CORRECTED_EV
EV_REVERSAL_BLOCKED_PROBABILITY_FALLBACK
```

Они продолжают использовать обычный EV-фильтр и проверку payout bucket.

## Основные Railway Variables

```text
TRADE_FILTER_ENABLED=true
MIN_TRADE_EV=-0.10
NEGATIVE_FALLBACK_ENABLED=false
REQUIRE_PAYOUT_BUCKET_READY=true
CONSENSUS_OVERRIDE_ENABLED=false
```

Полный набор находится в `RAILWAY_VARIABLES.txt`.

## Логика сделки

```text
1. Если выбранный payout bucket не готов → NO_TRADE.
2. Если NEGATIVE_EV_PROBABILITY_FALLBACK отключён → NO_TRADE.
3. Если selected_ev > MIN_TRADE_EV → ставка BASE_STAKE.
4. Если отдельно включён Crowd+Binance override и выполнены его условия → ставка override.
5. Иначе → NO_TRADE.
```

По умолчанию Crowd+Binance override выключен.

## Новые диагностические поля

В `features_json` и `/signal` сохраняются:

```text
negative_fallback_enabled
negative_fallback_blocked
normal_ev_pass
selection_reason
trade_rule
no_trade_reason
```

Пример заблокированного сигнала:

```json
{
  "selection_reason": "NEGATIVE_EV_PROBABILITY_FALLBACK",
  "normal_ev_pass": true,
  "negative_fallback_enabled": false,
  "negative_fallback_blocked": true,
  "trade_executed": false,
  "stake": 0.0,
  "no_trade_reason": "NEGATIVE_FALLBACK_DISABLED",
  "trade_rule": "NO_TRADE"
}
```

Новые решения маркируются `strategy_version=1.3.3`.

## API

```text
/health
/signal
/status?limit=30
/status?history=all&limit=100000
/history/export.csv
/payout/calibration
/model/performance
/strategy/performance
```

## История и база

PostgreSQL удалять или пересоздавать не нужно. Новых колонок нет: новые поля сохраняются в существующем `features_json`. Старая история полностью сохраняется.

## Обновление GitHub и Railway

1. Распакуйте ZIP.
2. Полностью замените файлы текущего GitHub-репозитория содержимым папки `FUSION_EV-1.3.3`.
3. PostgreSQL в Railway не удаляйте.
4. Сверьте Variables с `RAILWAY_VARIABLES.txt`.
5. Оставьте одну replica.
6. Выполните Redeploy.
7. Проверьте `/health`: версия `1.3.3`, `negative_fallback_enabled=false`, `consensus_override_enabled=false`.
8. В следующем `NEGATIVE_EV_PROBABILITY_FALLBACK` проверьте `/signal`: `negative_fallback_blocked=true` и `trade_executed=false`.

## Режим

Только PAPER. Приватный ключ не используется. Реальные транзакции не отправляются.
