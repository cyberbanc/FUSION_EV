# M9 Fusion EV 1.3.6.2 — Adaptive EV + Signal Cache

Paper-бот PancakeSwap Prediction BNB 5m.

## Торговая логика

- `selected_ev < -0.06` → NO TRADE;
- `-0.06 <= selected_ev < 0` → ставка $5;
- `0 <= selected_ev < 0.05` → ставка $10;
- `selected_ev >= 0.05` → ставка $15.

Дополнительно применяются shadow-фильтр, quality-фильтр и один cooldown-раунд после каждого блока из трёх подтверждённых реальных проигрышей.

## Исправление 1.3.6.2

`/signal` больше не вызывает `tick()` и не выполняет RPC-запросы. Background worker является единственным исполнителем торгового цикла и после каждого расчёта обновляет in-memory cache. Dashboard/Tilda получает готовый ответ из кэша практически мгновенно.

Это устраняет:

- `Failed to fetch` на Tilda;
- конкуренцию Tilda и worker за `_TICK_LOCK`;
- повторные тяжёлые обращения к BSC/Chainlink/PancakeSwap;
- задержки `/signal` дольше браузерного timeout.

## API

- `/healthz` — лёгкий Railway healthcheck;
- `/health` — полная диагностика;
- `/signal` — мгновенный read-only кэш;
- `/status?history=recent&limit=30`;
- `/history`;
- `/history/export.csv`;
- `/model/performance`;
- `/shadow/performance`.

## Совместимость

PostgreSQL, текущий банк, PnL и вся прежняя история сохраняются. Новая миграция базы для этого hotfix не требуется.

## Проверка

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
