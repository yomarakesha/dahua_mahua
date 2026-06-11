# Прямое подключение main-потоков к камерам (Вариант A)

**Дата:** 2026-06-11 · **Статус:** утверждено пользователем · **Основание:** замер
§3.1 (`netcheck-result.md`): main через NVR — 7815 потерянных RTP-пакетов на
1.1 Mbps, тот же поток напрямую с камеры — 0; sub через NVR — 0 потерь.
Вердикт и данные: `docs/audit-plan.md` §9.

## Цель

Убрать потери на main-потоках, забирая их напрямую с камер, не трогая
sub-потоки (они через NVR чистые) и не ломая существующие пути MediaMTX.

## Решения (утверждены)

1. **Схема потоков:** main — напрямую с камеры, sub — через NVR как сейчас.
2. **Заполнение IP:** автоматически при создании NVR (best-effort) + кнопка
   «Обновить IP камер» на NVR + ручная правка поля в форме камеры.

## Дизайн

### Данные
- `Camera.ip: str | None` (новая nullable-колонка `cameras.ip`, String(64)).
- Правило: **IP задан → main идёт `rtsp://{cam.ip}:554/...channel=1&subtype=0`;
  IP пуст (NULL) → main через NVR как раньше.** Это и есть фоллбэк — отдельных
  флагов нет. Сброс IP в форме = откат камеры на NVR-путь.
- Креды/порт камеры = креды/554 NVR (проверено digest-auth на живой камере).
  Отдельные креды камеры — YAGNI, добавим при реальной необходимости.
- Миграция: alembic-ревизия 0002 (Postgres); для SQLite — идемпотентный
  `ALTER TABLE cameras ADD COLUMN ip VARCHAR(64)` в `_ensure_schema`
  (`create_all` не добавляет колонки в существующие таблицы).

### Ядро
`path_sync._build_path_config`: при `quality == main` и `camera.ip` —
источник строится от IP камеры (канал всегда 1 — у IP-камеры свой первый
канал), иначе без изменений. `reconcile()` сам перепатчит существующие
пути по diff поля `source`.

### Импорт IP — `app/services/camera_import.py`
- `fetch_camera_ips(ip, username, password) -> dict[int, str]` — GET
  `configManager.cgi?action=getConfig&name=RemoteDevice` (httpx, digest),
  парсинг строк `table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_<slot>.<Key>=...`,
  канал = slot + 1, пропуск пустых/выключенных слотов (`192.168.0.0`, Enable=false).
- `apply_camera_ips(session, nvr) -> int` — проставляет `Camera.ip` по каналам,
  возвращает число обновлённых.
- Вызовы: (а) `create_nvr` после создания камер, best-effort (недоступность
  NVR не валит создание); (б) `POST /api/nvrs/{id}/import-camera-ips` (admin) —
  импорт + reconcile, в ответе число обновлённых камер.

### API / UI
- `CameraRead.ip`, `CameraUpdate.ip` (пустая строка нормализуется в NULL).
- PATCH /cameras/{id} с `ip` триггерит reconcile (меняется source у `_main`).
- UI: поле IP в форме камеры; кнопка «Обновить IP камер» на NVR; в списке
  камер бейдж `direct` / `via NVR`.

## Тестирование
- Юнит (pytest, новый для backend): парсер RemoteDevice на фикстуре с
  реального NVR (включая пустые слоты), выбор источника в
  `_build_path_config` (ip задан/нет × main/sub).
- Живой тест: импорт на `nvr-192-168-20-58` (28 камер `192.168.23.11–.38`),
  проверка через MediaMTX API, что `_main`-пути смотрят на `192.168.23.x`,
  замер RTP-потерь на 4–8 прямых main (ожидание: 0), regression сетки (sub).

## Риски
- У камеры выключен RTSP → её `_main`-путь не поднимется. Откат: стереть IP
  этой камеры (вернётся через NVR). Watchdog/sourceOnDemand ограничат ретраи.
- Сеть: для других NVR в иных подсетях нужен вторичный IP сервера в той
  подсети (админ-команда, как 2026-06-11). Линк сервера 100 Mbps:
  28 main × ~1.1 Mbps ≈ 31 Mbps — ок, гигабит желателен на вырост.
