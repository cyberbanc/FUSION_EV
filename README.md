# M9 Fusion EV 1.3.5 — Dynamic Shadow + Fixed $10

Paper-бот PancakeSwap Prediction BNB 5m.

## Главное изменение

Версия 1.3.5 сохраняет динамический shadow-фильтр версии 1.3.4, но полностью отключает Fibonacci.

Каждая разрешённая paper-сделка выполняется фиксированной ставкой:

```text
$10
```

- WIN: следующая разрешённая сделка снова $10;
- LOSS: следующая разрешённая сделка снова $10;
- SKIP / shadow-сигнал: ставка не выполняется;
- чётность `betting_epoch` не влияет на размер ставки.

## Shadow-фильтр

Каждый сигнал виртуально закрывается даже при пропуске реальной paper-ставки. Отдельно отслеживаются:

- источник сигнала;
- направление UP/DOWN;
- последние 15 результатов;
- виртуальный PnL при $10;
- Profit Factor;
- точность и серия проигрышей.

Сделка допускается при `selected_ev >= -0.05`, если последние 15 shadow-сигналов для того же источника и направления не дали PnL `<= -$20`.

## Совместимость с существующей базой

Старый банк, PnL и история сохраняются. Fibonacci-поля в существующей PostgreSQL не удаляются, но версия 1.3.5 их не читает и не меняет. Все новые решения имеют фиксированную ставку $10.

## API

- `/health`
- `/signal`
- `/status?history=recent&limit=30`
- `/history?limit=1000`
- `/history/export.csv`
- `/model/performance`
- `/shadow/performance`

## Проверка проекта

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Полные шаги установки находятся в `INSTALL_FUSION_EV_1.3.5.txt`.


## HOTFIX 1.3.6.1
- Исправлены литеральные фигурные скобки JSONB DEFAULT в app/db.py.
- Устранён startup crash psycopg2.sql.SQL.format()/IndexError.
- Railway healthcheck переведён на лёгкий /healthz, подробная диагностика остаётся на /health.
- Логика сигналов и ставок $5/$10/$15 не изменена.


## 1.3.6.3 — BSC POA hotfix

Для каждого BSC RPC-провайдера Web3.py v7 теперь автоматически внедряет
`ExtraDataToPOAMiddleware` на нулевом слое. Это устраняет падение worker с
ошибкой `The field extraData is ... bytes, but should be 32`.

Торговая логика, adaptive EV stakes, shadow/quality фильтры, cooldown, банк и
PostgreSQL-история не изменены. Код Tilda менять не требуется.

## 1.3.6.4 — DB get_decision hotfix

Добавлена отсутствовавшая функция `app.db.get_decision(betting_epoch)`. Она
используется worker, `/signal` и защитой от повторного создания решения. Hotfix
устраняет `AttributeError` после успешного подключения к BSC RPC. Стратегия,
ставки, фильтры, банк и PostgreSQL-история не изменены.
