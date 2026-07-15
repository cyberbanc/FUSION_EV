# M9 FUSION EV 1.3.1 — selective paper bot

Paper-бот для PancakeSwap Prediction BNB. Решение фиксируется примерно в T−40, но ставка больше не обязательна каждый раунд.

## Главное изменение 1.3.1

Бот продолжает анализировать и сохранять каждый раунд, однако делает ставку только когда выбранная сторона имеет положительный скорректированный EV и payout-bucket готов.

По умолчанию:

```text
TRADE_FILTER_ENABLED=true
MIN_TRADE_EV=0.00
REQUIRE_PAYOUT_BUCKET_READY=true
```

Логика исполнения:

```text
selected_ev > MIN_TRADE_EV
и выбранный payout bucket готов
    → BET, ставка $5
иначе
    → NO_TRADE, ставка $0
```

`MIN_TRADE_EV=0.00` означает строго положительный EV. При `selected_ev=0` ставка не делается.

## Что происходит при NO_TRADE

- решение и все компоненты сохраняются в PostgreSQL;
- `signal` внутри истории остаётся аналитическим направлением UP/DOWN;
- `/signal` возвращает публичный `signal: "NO_TRADE"`;
- отдельно возвращается `analysis_signal: "UP"` или `"DOWN"`;
- `stake=0`;
- после завершения раунда `outcome="SKIP"`;
- банк, wins, losses, draws и trades_count не изменяются;
- серия проигрышей не увеличивается и не сбрасывается;
- финальные коэффициенты всё равно сохраняются для дальнейшей payout-калибровки и оценки моделей.

## Почему выбран именно такой фильтр

На полной истории 1.2.0:

```text
selected_ev > 0:   26 ставок, результат +$55.42
selected_ev <= 0: 151 ставок, результат -$84.94
```

Это ретроспективный результат на уже просмотренной выборке и не гарантирует будущую прибыль. Версия 1.3.1 нужна для независимого paper-теста selective-режима.

## Остальная логика сохранена

- payout-калибровка отдельно для UP/DOWN и пяти диапазонов коэффициента;
- Price, Binance и Crowd участвуют в ансамбле;
- M9 и паттерны сохраняются для диагностики с нулевым весом по умолчанию;
- EV-разворот и Crowd+Binance fallback сохранены;
- variable stakes отключены;
- реальные транзакции отсутствуют.

## Ставки

```text
VARIABLE_STAKE_ENABLED=false
BASE_STAKE=10
```

Исполненная ставка всегда `$5`. Пропущенный раунд имеет `$0`.

## История и миграция

PostgreSQL удалять или пересоздавать не нужно. При запуске автоматически добавляются:

```text
trade_executed BOOLEAN NOT NULL DEFAULT TRUE
no_trade_reason TEXT
```

Все старые сделки получают `trade_executed=true`, поэтому прежняя статистика сохраняется без изменения.

Новые решения маркируются:

```text
strategy_version=1.3.1
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

Пример NO_TRADE в `/signal`:

```json
{
  "status": "NO_TRADE",
  "decision_locked": true,
  "trade_executed": false,
  "no_trade_reason": "EV_NOT_ABOVE_MINIMUM",
  "signal": "NO_TRADE",
  "analysis_signal": "UP",
  "stake": 0.0,
  "selected_ev": -0.12
}
```

`/strategy/performance` теперь показывает отдельно:

- `decisions_settled` — сколько раундов проанализировано;
- `trades` — сколько реальных paper-ставок исполнено;
- `skipped` — сколько раундов пропущено;
- `trade_rate` — долю раундов со ставкой;
- `wins`, `losses`, `win_rate`, `profit` — только по исполненным ставкам.

## Обновление

1. Полностью замените файлы GitHub.
2. PostgreSQL не удаляйте.
3. Добавьте три новые Railway Variables либо замените набор целиком из `RAILWAY_VARIABLES.txt`.
4. Оставьте одну replica.
5. Выполните Redeploy.
6. Проверьте `/health`, затем `/signal` в окне T−40 и `/strategy/performance` после закрытия нескольких раундов.

## Режим

Только PAPER. Приватный ключ не используется. Реальные транзакции не отправляются.
