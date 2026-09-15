# dlss-manager

Сканирует установленные Steam-игры, находит DLSS-компоненты (Super
Resolution / Frame Generation / Ray Reconstruction), собирает найденные
версии в локальную библиотеку и позволяет накатывать/откатывать их между
играми — с бэкапом оригинала и полным откатом при необходимости.

Работает с обычным Windows-Steam и с играми под Proton на Linux (структура
`steamapps/common/...` одна и та же).

## Установка

```bash
uv sync
```

## CLI

```bash
uv run dlss-manager scan                    # просканировать Steam-библиотеки
uv run dlss-manager list                     # игры + найденные компоненты
uv run dlss-manager library                  # версии DLL в локальной библиотеке
uv run dlss-manager import path/to/nvngx_dlss.dll
uv run dlss-manager apply <app_id> super_resolution <library_id>
uv run dlss-manager history                  # активные изменения
uv run dlss-manager rollback <change_id>     # откатить одно изменение
uv run dlss-manager rollback-all             # откатить вообще всё
uv run dlss-manager clean-logs [--apply]     # логи/файлы враппера (dry-run по умолчанию)
uv run dlss-manager clean-library            # убрать мусор из локальной библиотеки
```

## GUI

```bash
uv run dlss-manager gui
```

## Как это устроено

- `steam.py` — парсит `libraryfolders.vdf` и `appmanifest_*.acf`, находит
  установленные игры на всех Steam-дисках.
- `scanner.py` / `pe_version.py` — ищет `nvngx_dlss*.dll` по имени файла и
  читает версию прямо из PE-ресурсов (без загрузки библиотеки — работает и
  на Linux).
- `library.py` — локальное хранилище DLL, дедуп по sha256.
- `manager.py` — сканирование, применение версии (с бэкапом), откат
  (одного изменения или всех разом), очистка логов известных DLSS-врапперов
  (Streamline, DLSSTweaks, OptiScaler, Special K) и мусора в библиотеке.
- Ничего никуда не скачивается: версии в библиотеку попадают либо через
  сканирование уже установленных игр, либо через ручной импорт файла.
