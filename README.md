# M9 FUSION EV 1.3.2 — Crowd + Binance consensus override

Paper-бот для PancakeSwap Prediction BNB. Решение фиксируется примерно в T−40. Бот сохраняет анализ каждого раунда и делает paper-ставку только при прохождении обычного EV-фильтра либо специального подтверждённого правила Crowd + Binance.

## Главное изменение 1.3.2

Версия 1.3.2 сохраняет фильтр версии 1.3.1, но добавляет отдельное исключение:

```text
selection_reason = WEAK_EV_CROWD_BINANCE_FALLBACK
Crowd и Binance согласны с итоговым signal
selected_expected_coeff >= 1.40
выбранный payout bucket готов
    → BET $10, даже если selected_ev <= MIN_TRADE_EV
```

Исключение обходится **только порог EV**. Оно не отключает проверку готовности payout-bucket и не применяется к `NEGATIVE_EV_PROBABILITY_FALLBACK` или обычному `WEAK_EV_PROBABILITY_FALLBACK`.

## Основные Railway Variables

```text
TRADE_FILTER_ENABLED=true
MIN_TRADE_EV=-0.10
REQUIRE_PAYOUT_BUCKET_READY=true
CONSENSUS_OVERRIDE_ENABLED=true
CONSENSUS_OVERRIDE_MIN_COEFF=1.40
CONSENSUS_OVERRIDE_STAKE_USD=10
```

Полный набор находится в `RAILWAY_VARIABLES.txt`.

## Логика сделки

```text
1. Если payout bucket не готов → NO_TRADE.
2. Если selected_ev > MIN_TRADE_EV → обычная ставка BASE_STAKE.
3. Иначе, если Crowd + Binance consensus override выполнен → ставка $10.
4. Иначе → NO_TRADE.
```

Для новой сделки в `features_json` сохраняются:

```text
selected_expected_coeff
normal_ev_pass
consensus_override_eligible
crowd_binance_override
trade_rule
consensus_override_enabled
consensus_override_min_coeff
consensus_override_stake_usd
```

Значения `trade_rule`:

```text
NORMAL_EV_FILTER
CROWD_BINANCE_OVERRIDE
NO_TRADE
```

Новые решения маркируются:

```text
strategy_version=1.3.2
```

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

`/signal` дополнительно показывает причину выбора и применение нового правила:

```json
{
  "selection_reason": "WEAK_EV_CROWD_BINANCE_FALLBACK",
  "crowd_binance_consensus": "DOWN",
  "selected_expected_coeff": 1.63,
  "normal_ev_pass": false,
  "consensus_override_eligible": true,
  "crowd_binance_override": true,
  "trade_rule": "CROWD_BINANCE_OVERRIDE",
  "trade_executed": true,
  "stake": 10.0
}
```

## История и база

PostgreSQL удалять или пересоздавать не нужно. Новых колонок не требуется: диагностические поля сохраняются внутри существующего `features_json`. Старая история остаётся на месте и отделяется по `strategy_version`.

## Обновление GitHub и Railway

1. Распакуйте ZIP.
2. Полностью замените файлы текущего GitHub-репозитория содержимым папки.
3. PostgreSQL в Railway не удаляйте.
4. В Railway замените или проверьте Variables по `RAILWAY_VARIABLES.txt`.
5. Оставьте одну replica.
6. Выполните Redeploy.
7. Проверьте `/health`: версия должна быть `1.3.2`.
8. В окне T−40 проверьте `/signal` и поля `trade_rule`, `selected_expected_coeff`, `crowd_binance_override`.

## Режим

Только PAPER. Приватный ключ не используется. Реальные транзакции не отправляются.
