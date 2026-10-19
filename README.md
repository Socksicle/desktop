# Socksicle

**A cross-platform desktop proxy client for Windows and Linux with a Material 3 interface, subscription management, and sing-box / Xray backends.**

Socksicle is built with Python 3.10+ and PySide6 (Qt 6). It acts as an orchestrator and graphical interface for external proxy cores: it parses configurations, prepares runtime configs, manages core lifecycles, and provides a centralized place for server management, latency testing, routing modes, and diagnostics.

![Status: early development](https://img.shields.io/badge/status-active%20development-orange)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-4c8bf5)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![License](https://img.shields.io/badge/license-MIT-green)

> [!WARNING]
> **Socksicle is in active development (`0.1-rc.1`).** It is not production-ready. Expect rough edges, ongoing refactoring, and potential breaking changes between commits. Review the [Known Issues and Limitations](#known-issues-and-limitations) section before using it.

<details>
<summary>📸 <b>Screenshots</b> (click to expand)</summary>

| Main Screen | Settings Hub | About Screen |
|:---:|:---:|:---:|
| <img src="assets/readme/main.png" width="260" alt="Main screen with the hero power button, active server card, and list"> | <img src="assets/readme/settings.png" width="260" alt="Settings screen with categorized hub rows and options"> | <img src="assets/readme/about.png" width="260" alt="About screen with core version and device telemetry"> |

</details>

---

## Contents

- [Overview](#overview)
- [Protocol Support Matrix](#protocol-support-matrix)
- [Key Features](#key-features)
- [Requirements](#requirements)
- [Installation and Setup](#installation-and-setup)
- [Proxy Engines](#proxy-engines)
- [Configuration and Paths](#configuration-and-paths)
- [Security and Privacy](#security-and-privacy)
- [Known Issues and Limitations](#known-issues-and-limitations)
- [Development and Testing](#development-and-testing)
- [License](#license)
- [Links](#links)

---

## Overview

Socksicle does not implement proxy network protocols directly. Instead, it delegates connection handling to standalone core binaries (`sing-box` or `xray`). The application prepares secure, validated configuration files, spawns the engine process locally, monitors its health, and routes traffic either via a local SOCKS5 proxy (`127.0.0.1:<port>`) or a virtual TUN interface.

---

## Protocol Support Matrix

The table below reflects the actual status in the codebase:

| Protocol | Status | Supported Engines | Capabilities & Transports |
|---|---|:---:|---|
| **Shadowsocks** | ✅ Fully Tested | `sing-box`, `xray` | SIP002 URIs, legacy formats, standard AEAD ciphers. *(SIP003 plugins are parsed from links but not yet passed to generated engine configs — see [Known Issues](#known-issues-and-limitations))* |
| **Hysteria 2** | ✅ Fully Tested | `sing-box` | Obfs (`salamander`, `gecko`), multi-port hopping (`server_ports`), bandwidth hints, allow-insecure toggle |
| **VLESS** | ✅ Supported | `sing-box`, `xray` | TCP, TLS, REALITY (`xtls-rprx-vision`), uTLS fingerprints. Transports: WS, gRPC, HTTP (h2), HTTPUpgrade, QUIC via `sing-box`; WS, gRPC, **XHTTP** via `xray` (auto-routed) |
| **VMess** | 🟡 Implemented | `sing-box`, `xray` | Standard VMess outbounds, TCP / WS / gRPC transports (plus HTTP (h2), HTTPUpgrade, QUIC via `sing-box`); less real-world test coverage than SS/Hy2 |
| **Trojan** | ✅ Implemented | `sing-box` | TLS with uTLS, WS / gRPC / HTTP / HTTPUpgrade / QUIC transports |
| **TUIC** | ✅ Implemented | `sing-box` | TLS (h3 ALPN), congestion control (`bbr` default), UDP relay mode |

Supported manual link schemes:
```text
ss://  vless://  vmess://  hysteria2://  hy2://  trojan://  trojan-go://  tuic://  tws3://
```

Subscriptions accept plain URL lists, Base64-encoded link lists, or SIP008 JSON over **HTTPS only** (plain HTTP is intentionally blocked).

---

## Key Features

- **Material 3 Interface**: Inspired by modern Material You design, featuring animated hero power button states, dynamic tinting, customizable color palettes (Lavender, Sakura, Mint, AMOLED, and custom hex), and responsive scroll behavior.
- **Server and Subscription Hub**: Add servers manually or import remote HTTPS subscriptions with automatic background updates, group collapsing, per-group latency checks, and keyword filtering.
- **Dual Routing Engines**: Choose between `sing-box` (default; required for TUN, Hysteria 2, Trojan, and TUIC) and `xray` (SS/VLESS/VMess, including XHTTP transports). Engine selection in `auto` mode handles protocol and transport constraints automatically.
- **TUN Mode (Beta)**: Route full operating system traffic through a virtual network interface using sing-box without needing manual system proxy configuration.
- **Kill Switch (Beta)**: OS firewall enforcement (Windows Defender Firewall or Linux `iptables`) preventing traffic leaks outside the tunnel if the core crashes.
- **Multi-Method Latency Checks**:
  - **THP (Transport + HTTP Ping)**: Measures transport handshake plus first-byte HTTP response.
  - **HTTP HEAD / GET**: Measures round-trip HTTP request completion.
  - **TCP Connect**: Raw socket handshake latency.
  - Configurable timeouts and display options (numeric ms, 4-bar cellular-style signal indicator, or hidden).
- **Secure DNS Configuration**: Select between system resolver, encrypted presets (Cloudflare, Quad9, AdGuard, Google), or custom DoH / DoT endpoints.
- **Sharing and Export**: Instant QR code generation for any server, JSON profile export/import, and encrypted `tws3://` TwinSock sharing links.
- **Desktop Integration**: System tray menu (connect/disconnect/quit), autostart with the OS, start minimized, and localized interface (English and Russian).

---

## Requirements

- **Operating System**:
  - **Windows 10 / 11** (64-bit).
  - **Linux** (modern desktop distribution with Qt 6 support).
  - *macOS is not supported.*
- **Python**: Version `3.10`, `3.11`, or `3.12`.
- **Core Engine Binary**:
  - **sing-box**: Version `1.14.0` or newer (enforced by engine preflight; required for TUN mode, Hysteria 2, Trojan, and TUIC).
  - **xray**: Version `26.3.0` or newer recommended (optional, alternative backend for SS/VLESS/VMess; required for VLESS over XHTTP). The version is displayed on the About screen but not enforced at runtime.
- **System Privileges (TUN Mode only)**:
  - Windows: Administrator rights (with `wintun.dll` placed next to `sing-box.exe`).
  - Linux: granted once on first TUN start via a single Polkit prompt — `cap_net_admin,cap_net_bind_service+eip` on sing-box (inheritable, so sing-box's helper processes keep the privileges) and, when systemd-resolved is running, a Polkit rule (`data/polkit/20-socksicle-tun.rules`) that removes the repeated password prompts for link DNS setup. After that, TUN starts without asking for a password.

---

## Installation and Setup

### Prebuilt Binaries (Recommended)

Prebuilt standalone binaries are compiled via Nuitka into native machine code with bundled `sing-box` and dependencies. You can download them directly from [GitHub Releases](https://github.com/socksicle/desktop/releases):

- **Windows**:
  - **Installer**: `Socksicle-Setup-<version>-x64.exe` (Clean setup wizard with desktop shortcut and uninstaller).
  - **Portable**: `Socksicle-<version>-Windows-Portable-x64.zip` (Standalone directory, extract and run).
- **Linux**:
  - **AppImage**: `Socksicle-<version>-x86_64.AppImage` (Universal standalone package for all modern Linux distributions):
    ```bash
    chmod +x Socksicle-*-x86_64.AppImage
    ./Socksicle-*-x86_64.AppImage
    ```

---

### Running from Source

For development or custom setups, you can run Socksicle from source:

#### 1. Clone the repository

```bash
git clone https://github.com/socksicle/desktop.git
cd desktop
```

### 2. Set up a virtual environment

```bash
python -m venv .venv
```

Activate the environment:
- **Windows (PowerShell)**:
  ```powershell
  .venv\Scripts\Activate.ps1
  ```
- **Linux**:
  ```bash
  source .venv/bin/activate
  ```

### 3. Install dependencies

```bash
pip install -e ".[dev]"
```

### 4. Provide engine binaries

Proxy engine binaries are **not bundled** in the repository. You must supply at least one core (see [Proxy Engines](#proxy-engines)).

### 5. Launch the application

- With console output (recommended during setup):
  ```bash
  python main.py
  ```
- Without console window (Windows):
  ```bash
  python main.pyw
  ```
- Start minimized to tray:
  ```bash
  python main.py --minimized
  ```

---

## Proxy Engines

Socksicle searches for engine binaries in this order:

1. `<app_dir>/bin/<binary_name>`
2. `<config_dir>/bin/<binary_name>`
3. `<app_dir>/bin/<engine_subdir>/<binary_name>`
4. `<config_dir>/bin/<engine_subdir>/<binary_name>`
5. System `PATH`

Where `<binary_name>` is `sing-box.exe` / `xray.exe` on Windows, or `sing-box` / `xray` on Linux.

Recommended local directory structure:

```text
desktop/
├── bin/
│   ├── sing-box/
│   │   ├── sing-box.exe       # Windows
│   │   ├── wintun.dll         # Required on Windows for TUN mode
│   │   └── sing-box           # Linux (chmod +x)
│   └── xray/
│       ├── xray.exe           # Windows
│       └── xray               # Linux (chmod +x)
├── main.py
└── ...
```

The About screen displays the detected engine path and verified version number.

---

## Configuration and Paths

User configuration, servers, and runtime markers are stored in the platform-standard user directory:

| OS | Configuration Path | Log Path |
|---|---|---|
| **Windows** | `%APPDATA%\socksicle\` | `%LOCALAPPDATA%\socksicle\logs\` |
| **Linux** | `~/.config/socksicle/` | `~/.config/socksicle/logs/` |

Directory structure:
```text
socksicle/
├── servers.json          # Manually added servers
├── subscriptions.json    # Subscriptions and imported nodes
├── settings.json         # User settings (theme, port, engine, DNS)
├── engine_state/         # Runtime PID markers for process tracking
└── avatars/              # Cached contributor avatars
```

To perform a clean reset, close the application and delete the configuration folder.

---

## Security and Privacy

- **TLS Verification**: Certificate validation is enabled by default. Insecure TLS can only be enabled explicitly per-server or via the dedicated Hysteria 2 setting. Subscription imports cannot silently force insecure TLS.
- **SSRF and DNS-Rebinding Protection**: When fetching subscriptions, hostnames are resolved once and verified against public IP ranges (rejecting private, loopback, link-local, and multicast addresses). Connections are pinned to the verified IP, and payload sizes are capped at 2 MB.
- **Restricted Temp Files**: Engine JSON configuration files containing credentials are created in the system temp directory with owner-only permissions (`0600` on Linux, explicit restricted ACLs via `icacls` on Windows) and removed when the engine stops.
- **Local Vault Reality**: Sensitive fields in `servers.json` (passwords, keys, UUIDs) are stored encrypted with AES-256-GCM. However, the encryption key is derived using a fixed application secret bundled in the code. This prevents casual shoulder-surfing of JSON files, but **does not protect against a local user, malware, or forensic disk inspection**.
- **Network Requests**: The app contains no third-party telemetry SDKs. However:
  - When connected, the Bento telemetry card queries `http://ip-api.com/json` **through the active proxy** to display the external IP, country, and ISP.
  - The About screen downloads contributor avatars from `avatars.githubusercontent.com`.

---

## Known Issues and Limitations

*The following list documents verified technical limitations and known issues in the current release:*

1. **Protocol Readiness**: Only **Shadowsocks** and **Hysteria 2** are fully tested in production. **VLESS, VMess, Trojan, and TUIC** are implemented (Trojan and TUIC run on `sing-box` only; VLESS XHTTP runs on `xray` only), with less real-world coverage than SS/Hy2.
2. **No Bundled Engines or In-App Downloader**: Engine binaries are not included in the repository or automatically fetched at runtime. You must download `sing-box` or `xray` manually and place them in the search path.
3. **Strict Engine Version Bounds**: `sing-box` must be version **1.14.0 or newer**. Older versions will fail preflight checks. For Windows TUN mode, `wintun.dll` matching the core's architecture must reside in the same folder as `sing-box.exe`.
4. **Single-Instance Concurrency**: A `QLockFile` in the user config directory (`single_instance.lock`) prevents a second instance from starting: it is refused early with an "Already Running" dialog and therefore cannot race on the `127.0.0.1:1080` bind, PID markers in `engine_state/`, or `settings.json` writes. Stale lock files left by a crashed instance are detected and cleared automatically.
5. **TUN Mode Crash Recovery**: If the application is terminated forcefully (e.g. killed via Task Manager or a power cut) while TUN mode is active, the virtual adapter or default routes may not be restored immediately. Launching Socksicle again triggers `cleanup_stale_core_processes()` to restore system networking.
6. **Kill Switch Firewall Persistence**: The Kill Switch modifies Windows Defender Firewall or Linux `iptables` rules. If the process is forcibly killed before cleanly disconnecting, outgoing non-proxy traffic may remain blocked until the firewall rules are reset or the machine rebooted.
7. **DNS-over-TLS (DoT) with Xray**: Xray-core does not support DoT (`tls://`) nameservers. Selecting DoT when using Xray raises a configuration error. Use DoH (`https://`) with Xray or switch to `sing-box`.
8. **Hysteria 2 Obfs Constraint**: In sing-box, specifying an obfs type without a password causes the core to reject the configuration. Use links containing both fields or leave obfs disabled.
9. **Test Suite Isolation**: Tests run in an isolated offscreen environment (`QT_QPA_PLATFORM=offscreen`) without real network access or real local port bindings to ensure determinism.
10. **No macOS Support**: macOS is not supported; platform-specific network routing, service management, and firewall rules are not implemented for Darwin.
11. **SIP003 Plugin Gap (SS)**: SIP003 plugins (`obfs-local`, `v2ray-plugin`) are accepted in `ss://` links and stored with the server entry, but the generated `sing-box` / `xray` engine configs do not yet include the plugin block. Shadowsocks servers that require a plugin will not connect.

---

## Development and Testing

### Running Tests

Tests are executed with `pytest` using an offscreen Qt platform (`QT_QPA_PLATFORM=offscreen`). Tests are isolated and do not touch real network interfaces or bind real local ports.

Run the test suite:

```bash
python -m pytest tests -q
```

Run targeted engine and parser tests:

```bash
python -m pytest tests/test_engines.py -v
python -m pytest tests/test_link_parser.py -v
python -m pytest tests/test_singbox_configs.py -v
python -m pytest tests/test_xray_configs.py -v
```

### Engineering Guidelines

Refer to [AGENTS.md](AGENTS.md) for repository coding rules, architectural constraints, and testing discipline:
- Keep changes minimal, complete, and testable.
- Maintain source modules under 400 lines where practical.
- Use explicit type annotations and native Qt layouts/signals.
- Never swallow exceptions with bare `except:`.

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Links

- **Repository**: [github.com/socksicle/desktop](https://github.com/socksicle/desktop)
- **Issue Tracker**: [github.com/socksicle/desktop/issues](https://github.com/socksicle/desktop/issues)
- **Telegram Updates**: [@iwtsddev](https://t.me/iwtsddev)

*Socksicle is an independent open-source project. `sing-box` and `Xray-core` are developed by their respective authors under their own open-source licenses.*
