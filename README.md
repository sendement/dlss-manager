# dlss-manager

Сканирует установленные Steam-игры, находит DLSS-компоненты (Super
Resolution / Frame Generation / Ray Reconstruction / Neural Rendering —
он же DLSS 5), собирает найденные версии в локальную библиотеку и позволяет
накатывать/откатывать их между играми — с бэкапом оригинала и полным
откатом при необходимости.

Работает с обычным Windows-Steam и с играми под Proton на Linux (структура
`steamapps/common/...` одна и та же).

## Установка

**Arch/CachyOS (пакет):**

```bash
sudo pacman -U dlss-manager-*.pkg.tar.zst   # из GitHub Releases
# или собрать самому:
cd packaging/arch && makepkg -si
```

Ставит и `.desktop`-файл с иконкой — приложение появится в меню как «DLSS Manager».

**Из исходников (любая платформа, через uv):**

```bash
uv sync
```

## CLI

```bash
uv run dlss-manager scan                    # просканировать Steam-библиотеки
uv run dlss-manager list                     # игры + найденные компоненты
uv run dlss-manager library                  # версии DLL в локальной библиотеке
uv run dlss-manager import path/to/nvngx_dlss.dll
uv run dlss-manager library-remove <library_id>
uv run dlss-manager apply <app_id> super_resolution <library_id>
uv run dlss-manager history                  # активные изменения
uv run dlss-manager rollback <change_id>     # откатить одно изменение
uv run dlss-manager rollback-all             # откатить вообще всё
uv run dlss-manager clean-logs [--apply]     # логи/файлы враппера (dry-run по умолчанию)
uv run dlss-manager clean-library            # убрать мусор из локальной библиотеки

uv run dlss-manager optiscaler-sources                    # доступные источники (официальный + форки)
uv run dlss-manager optiscaler-releases [--source KEY]     # релизы с GitHub
uv run dlss-manager optiscaler-targets <app_id>            # куда его можно поставить
uv run dlss-manager optiscaler-install <app_id> [--source KEY] [--target PATH] [--proxy dxgi.dll] [--version TAG]
uv run dlss-manager optiscaler-list                        # активные установки
uv run dlss-manager optiscaler-check                       # сверить версии с последним релизом (на каждый source свой)
uv run dlss-manager optiscaler-update <install_id>
uv run dlss-manager optiscaler-uninstall <install_id>

uv run dlss-manager dlssg-sm86-releases                   # релизы sdli1995/dlssg_for_sm86
uv run dlss-manager dlssg-sm86-targets <app_id>
uv run dlss-manager dlssg-sm86-install <app_id> [--target PATH] [--proxy version.dll] [--runtime 310.9] [--version TAG]
uv run dlss-manager dlssg-sm86-list
uv run dlss-manager dlssg-sm86-check
uv run dlss-manager dlssg-sm86-update <install_id>
uv run dlss-manager dlssg-sm86-uninstall <install_id>
```

### Источники OptiScaler

- `official` (по умолчанию) — [optiscaler/OptiScaler](https://github.com/optiscaler/OptiScaler).
- `dagherbou-dlssnr` — сторонний форк апстрима, добавляет DLSS Neural
  Rendering (DLSS 5 — реальная технология NVIDIA, анонсирована на GTC 2026,
  но официальной интеграции "в любую игру" NVIDIA не публикует) поверх
  DLSS/FSR/XeSS, без MFG-unlock. Это база, на которой сделан `klebermotta-mfg`.
- `klebermotta-mfg` — форк форка `dagherbou-dlssnr`, добавляет ещё MFG-unlock
  (3x/4x/6x Frame Generation на RTX 40 через патч `nvngx_dlssg.dll` в памяти —
  не использовать в мультиплеере, риск бана).

Оба сторонних форка требуют файл `nvngx_dlssnr.dll` (~165 МБ) — его нет в
архивах, авторы прямо пишут, что распространять его не будут (это файл
NVIDIA). **Это приложение не скачивает его ни при каких условиях** — нужно
взять из игры, которая официально поддерживает DLSS 5 (например, NBA 2K27),
или добавить самостоятельно из любого источника, которому доверяете сами.
Если файл уже лежит в локальной библиотеке (импортирован вручную или найден
сканом другой игры), при установке `dagherbou-dlssnr`/`klebermotta-mfg` он
автоматически подставится в папку игры — но только если там ещё нет своего
(игры с нативной поддержкой DLSS 5 не трогаются).

Были найдены и сознательно НЕ подключены ещё три репозитория с похожими
названиями (rakanki911/DLSS5-Swapper, perseval-BLR/DLSS5-NeuralScreen,
faisalkindi/DLSS5oneclick) — все три created за последние 2-3 недели, с
аномально накрученными звёздами относительно возраста аккаунта автора,
раздают только готовые .exe без исходников, у одного README прямо учит
обходить предупреждение SmartScreen. Похоже на скам/малварь-кампанию вокруг
хайпа "DLSS 5" — не запускать.

### dlssg_for_sm86 (DLSS Frame Generation для RTX 20/30)

[sdli1995/dlssg_for_sm86](https://github.com/sdli1995/dlssg_for_sm86) — отдельный
от OptiScaler инструмент, не форк. Разблокирует DLSS-G на RTX 20/30 (SM75/SM86)
через proxy-DLL вокруг немодифицированного рантайма NVIDIA. Аккаунт автора
с 2017 года, релизные DLL подписаны (self-signed, отпечаток указан в README
проекта), но, в отличие от OptiScaler, GitHub не публикует хэш для скачанного
архива — слабее по проверяемости. Не использовать в мультиплеере — риск бана.

Ставится так же, как OptiScaler (proxy DLL + ini рядом с исполняемым файлом
игры), но с двумя рантаймами на выбор (310.9 — до 6X, 310.1 — до 4X) и своим
набором имён прокси (`version.dll` по умолчанию, либо d3d12/dbghelp/dinput8/dxgi/winmm).

## GUI

```bash
uv run dlss-manager gui
```

Вкладка «Библиотека» — все DLL, что попали в библиотеку (сканом или вручную),
с размером, хэшем, источником и удалением. Добавить свои файлы можно тремя
способами: перетащить `.dll` на окно (работает на любой вкладке), кнопкой
«Импортировать DLL...» (на вкладке «Библиотека» или в тулбаре, оба варианта
поддерживают выбор нескольких файлов сразу) или через меню «Файл →
Импортировать DLL...».

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
- Ничего никуда не скачивается: версии DLSS в библиотеку попадают либо через
  сканирование уже установленных игр, либо через ручной импорт файла.
- `optiscaler.py` — качает релизы [OptiScaler](https://github.com/optiscaler/OptiScaler)
  прямо с GitHub Releases (с проверкой sha256 из их же API), распаковывает `.7z`
  через системный `7z`/`7za` (py7zr не умеет в BCJ2-фильтр, которым сжаты их
  архивы) и ставит по той же логике, что и их `setup_windows.bat`/`setup_linux.sh`
  (переименование `OptiScaler.dll` в выбранный proxy-DLL). Установка/обновление/
  удаление отслеживаются в своей таблице БД — свой манифест вместо их bash-анинсталлера,
  чтобы это укладывалось в общую историю изменений приложения.
- `dlssg_sm86.py` — аналогичная логика для dlssg_for_sm86, но качает не
  Release-ассет, а автогенерируемый GitHub-архив исходников по тегу (у этого
  проекта релизы без вложений — файлы лежат прямо в дереве репозитория),
  поэтому распаковка через `tarfile`, а не системный `7z`.
