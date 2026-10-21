# Socksicle

[English](README.md) | [Русский](README_ru.md) | [简体中文](README_zh.md)

Socksicle — десктопный прокси-клиент для Windows и Linux с интерфейсом в стиле Material 3. Приложение настраивает и управляет отдельными ядрами ([sing-box](https://github.com/SPlayer/sing-box) и [Xray-core](https://github.com/XTLS/Xray-core)), направляя трафик через локальный SOCKS5-прокси или системный TUN-адаптер.

> **Статус:** Ранняя разработка (`0.1.1`). Возможны ошибки и несовместимые изменения.

| Главная | Настройки | О программе |
|:---:|:---:|:---:|
| <img src="assets/readme/main.png" width="260" alt="Главное окно"> | <img src="assets/readme/settings.png" width="260" alt="Экран настроек"> | <img src="assets/readme/about.png" width="260" alt="О программе"> |

## Возможности

- **Протоколы:** Shadowsocks, VLESS, VMess, Hysteria 2, Trojan и TUIC.
- **Ядра (движки):** Автоматический выбор между `sing-box` и `Xray` либо ручной выбор для каждого профиля.
- **Режимы маршрутизации:** Локальный SOCKS5-прокси (по умолчанию `127.0.0.1:1080`) или общесистемный TUN.
- **Подписки:** Импорт по HTTPS-ссылкам (обычный текст, Base64 или SIP008 JSON) с автообновлением.
- **Безопасность:** Встроенный Kill Switch (Брандмауэр Windows / Linux nftables) и безопасная загрузка подписок с защитой от SSRF.
- **Проверка задержки:** Измерение TCP-рукопожатия и HTTP-задержки в реальном времени.
- **Интерфейс:** Современный Material 3 с тёмной/светлой темами и сворачиванием в системный трей.

## Поддержка протоколов

| Протокол | Ядро | Примечания |
|---|---|---|
| **Shadowsocks** | sing-box, Xray | Плагины SIP003 парсятся, но пока не передаются в ядра |
| **VLESS** | sing-box, Xray | Для транспорта XHTTP требуется Xray |
| **VMess** | sing-box, Xray | TCP, WebSocket, gRPC |
| **Hysteria 2** | sing-box | Если включена обфускация, требуются тип и пароль obfs |
| **Trojan** | sing-box | Стандартный TLS-транспорт |
| **TUIC** | sing-box | TUIC v5 |

Поддерживаемые форматы ссылок:
```text
ss://  vless://  vmess://  hysteria2://  hy2://  trojan://  trojan-go://  tuic://  tws3://
```

## Установка

Готовые сборки доступны на странице [GitHub Releases](https://github.com/Socksicle/desktop/releases).

### Linux

- **Быстрая установка одной командой (рекомендуется):**
  ```bash
  curl -fsSL https://iwtsd.online/socksicle/get | bash
  ```

- **Ручная установка:**
  Скачайте AppImage или `.tar.xz` архив со страницы релизов:
  ```bash
  # Запуск AppImage:
  chmod +x Socksicle-<version>-x86_64.AppImage
  ./Socksicle-<version>-x86_64.AppImage

  # Либо распаковка архива tar.xz:
  tar -xf Socksicle-<version>-Linux-x86_64.tar.xz
  ./Socksicle/socksicle
  ```

### Windows

- **Установщик:** Запустите `Socksicle-Setup-<version>-x64.exe`.
- **Портативная версия:** Распакуйте `Socksicle-<version>-Windows-Portable-x64.zip` и запустите `Socksicle.exe`.

### Варианты сборок и форматы архивов

Каждый релиз публикуется в трёх редакциях в зависимости от встроенных ядер:

| Редакция | Встроенные ядра | Для чего подходит |
|---|---|---|
| **Стандарт** *(по умолчанию)* | `sing-box` (+ `wintun.dll` на Windows) | Для большинства пользователей. Готово к работе сразу «из коробки». |
| **С Xray** (`With-Xray`) | `sing-box` + `Xray-core` (+ базы geoip/geosite) | Полная поддержка всех протоколов, включая VLESS XHTTP и функции Xray. |
| **Без ядер** (`Coreless`) | *Без ядер* | Минимальный размер (~20 МБ). Использует ядра из системного `PATH` или папки `bin/`. |

#### Форматы файлов:
- **Windows:**
  - `Socksicle-Setup-*.exe` — инсталлятор с ярлыками и деинсталлятором.
  - `Socksicle-*-Windows-Portable-*.zip` — портативная папка, не требующая установки.
- **Linux:**
  - `Socksicle-*.AppImage` — единый самодостаточный исполняемый файл для любого дистрибутива.
  - `Socksicle-*.tar.xz` — архив с бинарником, библиотеками и `.desktop`-ярлыком.

## Быстрый старт

1. **Добавьте сервер:** Нажмите кнопку **«Добавить»** и вставьте ссылку узла (`vless://`, `ss://` и др.) или URL подписки.
2. **Выберите режим:**
   - **SOCKS5 (по умолчанию):** Настройте браузер или программы на `127.0.0.1:1080`.
   - **TUN:** Весь интернет-трафик системы направляется через прокси.
3. **Подключитесь:** Нажмите кнопку питания по центру.

> **Требования для режима TUN:**
> - **Windows:** Требуются права Администратора и библиотека `wintun.dll` рядом с `sing-box.exe`.
> - **Linux:** Требуется `pkexec` (Polkit) для настройки сетевых интерфейсов.

## Запуск из исходного кода

Требования: Python 3.10+ и бинарники ядер.

```bash
# Клонирование репозитория
git clone https://github.com/socksicle/desktop.git
cd desktop

# Создание виртуального окружения
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\Activate.ps1

# Установка зависимостей
pip install -e ".[dev]"

# Запуск
python main.py
```

### Бинарники ядер

При запуске из исходного кода скачайте ядра и поместите их в каталог `bin/` либо добавьте в системный `PATH`:

```text
bin/
├── singbox/
│   ├── sing-box          # Linux
│   ├── sing-box.exe      # Windows (требуется sing-box >= 1.14)
│   └── wintun.dll        # Windows (требуется для TUN)
└── xray/
    ├── xray              # Linux
    └── xray.exe          # Windows
```

Флаги командной строки:
- `--minimized`: Запуск в свёрнутом виде в системный трей.
- `--debug`: Включить подробные отладочные логи ядер.

## Конфигурация и логи

| ОС | Каталог настроек | Каталог логов |
|---|---|---|
| **Windows** | `%APPDATA%\socksicle\` | `%LOCALAPPDATA%\socksicle\logs\` |
| **Linux** | `~/.config/socksicle/` | `~/.config/socksicle/logs/` |

Настройки, серверы и подписки хранятся в формате JSON. Чувствительные поля (пароли, UUID, приватные ключи) перед записью на диск шифруются алгоритмом AES-256-GCM.

## Известные ограничения

- **TUN и Kill Switch:** Функции находятся в стадии бета-тестирования. При аварийном завершении приложения маршруты или правила файрвола может потребоваться сбросить повторным открытием Socksicle.
- **Xray DoT:** Xray не поддерживает DNS-over-TLS (`tls://`). Используйте DNS-over-HTTPS (`https://`) или переключитесь на sing-box.
- **macOS:** Не поддерживается.

## Обратная связь

- Проблемы и баг-репорты: [GitHub Issues](https://github.com/Socksicle/desktop/issues)
- Новости и обновления: Telegram [@iwtsddev](https://t.me/iwtsddev)

## Лицензия

[MIT](LICENSE). Ядра `sing-box` и `Xray-core` являются сторонними проектами и распространяются по собственным лицензиям.
