# Socksicle

[English](README.md) | [Русский](README_ru.md) | [简体中文](README_zh.md)

Socksicle 是一款适用于 Windows 和 Linux 的 Material 3 设计风格桌面代理客户端。它支持配置和管理独立的代理内核（[sing-box](https://github.com/SPlayer/sing-box) 与 [Xray-core](https://github.com/XTLS/Xray-core)），可通过本地 SOCKS5 代理或全局 TUN 虚拟网卡路由网络流量。

> **状态：** 早期开发阶段（`0.1.1`）。可能会遇到问题或产生非兼容性变更。

| 主界面 | 设置 | 关于 |
|:---:|:---:|:---:|
| <img src="assets/readme/main.png" width="260" alt="主界面"> | <img src="assets/readme/settings.png" width="260" alt="设置界面"> | <img src="assets/readme/about.png" width="260" alt="关于界面"> |

## 功能特性

- **协议支持：** Shadowsocks、VLESS、VMess、Hysteria 2、Trojan 以及 TUIC。
- **内核切换：** 支持在 `sing-box` 和 `Xray` 之间自动切换，亦可为每个节点单独指定。
- **路由模式：** 本地 SOCKS5 代理（默认 `127.0.0.1:1080`）或全局 TUN 模式。
- **订阅管理：** 支持从 HTTPS 链接导入（纯文本、Base64 或 SIP008 JSON 格式）并支持自动更新。
- **安全防护：** 内置 Kill Switch 防泄漏开关（Windows 防火墙 / Linux nftables）以及防 SSRF 订阅拉取防护。
- **延迟测试：** 实时 TCP 握手和 HTTP 延迟检测。
- **现代外观：** Material 3 界面规范，支持深色/浅色主题及系统托盘最小化。

## 协议支持列表

| 协议 | 内核 | 备注 |
|---|---|---|
| **Shadowsocks** | sing-box, Xray | SIP003 插件已解析，暂未向内核传递 |
| **VLESS** | sing-box, Xray | XHTTP 传输协议需要使用 Xray |
| **VMess** | sing-box, Xray | 支持 TCP、WebSocket、gRPC |
| **Hysteria 2** | sing-box | 开启混淆时必须提供 obfs 类型及密码 |
| **Trojan** | sing-box | 标准 TLS 传输 |
| **TUIC** | sing-box | TUIC v5 |

支持的链接格式：
```text
ss://  vless://  vmess://  hysteria2://  hy2://  trojan://  trojan-go://  tuic://  tws3://
```

## 安装使用

可前往 [GitHub Releases](https://github.com/Socksicle/desktop/releases) 下载预编译安装包。

### Linux

- **一键脚本安装（推荐）：**
  ```bash
  curl -fsSL https://iwtsd.online/socksicle/get | bash
  ```

- **手动下载：**
  从发布页面下载 AppImage 或 `.tar.xz` 压缩包：
  ```bash
  # 运行 AppImage：
  chmod +x Socksicle-<version>-x86_64.AppImage
  ./Socksicle-<version>-x86_64.AppImage

  # 或解压 tar.xz 归档：
  tar -xf Socksicle-<version>-Linux-x86_64.tar.xz
  ./Socksicle/socksicle
  ```

### Windows

- **安装版：** 运行 `Socksicle-Setup-<version>-x64.exe`。
- **便携版：** 解压 `Socksicle-<version>-Windows-Portable-x64.zip` 后运行 `Socksicle.exe`。

### 发行版本类型与格式说明

每次发布提供三种不同内核打包的版本：

| 版本分类 | 内置内核 | 适用场景 |
|---|---|---|
| **标准版** *(默认)* | `sing-box`（Windows 下附带 `wintun.dll`） | 适合绝大多数用户，开箱即用，资源开销较小。 |
| **含 Xray 完整版** (`With-Xray`) | `sing-box` + `Xray-core`（附带 geoip/geosite 路由数据） | 完整协议支持，适合需要 VLESS XHTTP 或 Xray 特性的用户。 |
| **无内核纯净版** (`Coreless`) | *不含内核* | 体积小巧（约 20 MB）。使用系统 `PATH` 中的内核或手动放入 `bin/` 目录。 |

#### 文件格式说明：
- **Windows：**
  - `Socksicle-Setup-*.exe` — 安装包程序，包含快捷方式与卸载程序。
  - `Socksicle-*-Windows-Portable-*.zip` — 免安装便携绿色压缩包。
- **Linux：**
  - `Socksicle-*.AppImage` — 单文件免安装包，兼容主流现代 Linux 发行版。
  - `Socksicle-*.tar.xz` — 包含主程序、依赖库和桌面启动项的绿色压缩包。

## 快速上手

1. **添加节点：** 点击 **“添加”** 按钮，粘贴节点链接（如 `vless://`、`ss://`）或导入订阅地址。
2. **选择模式：**
   - **SOCKS5（默认）：** 在浏览器或应用程序中配置代理服务器为 `127.0.0.1:1080`。
   - **TUN：** 全局网络接管，将整个系统的流量导向代理。
3. **建立连接：** 点击中央电源开关图标。

> **TUN 模式系统要求：**
> - **Windows：** 需要管理员权限，且 `sing-box.exe` 旁边需存在 `wintun.dll`。
> - **Linux：** 需要 `pkexec`（Polkit）授权以配置网络路由和规则。

## 从源码运行

运行环境：Python 3.10+ 及内核可执行文件。

```bash
# 克隆代码仓库
git clone https://github.com/socksicle/desktop.git
cd desktop

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate       # Windows 系统：.venv\Scripts\Activate.ps1

# 安装依赖
pip install -e ".[dev]"

# 启动程序
python main.py
```

### 内核放置路径

从源码运行程序时，请下载对应平台的内核并放入 `bin/` 目录或系统 `PATH` 路径中：

```text
bin/
├── singbox/
│   ├── sing-box          # Linux
│   ├── sing-box.exe      # Windows（要求 sing-box >= 1.14）
│   └── wintun.dll        # Windows（TUN 模式必需）
└── xray/
    ├── xray              # Linux
    └── xray.exe          # Windows
```

命令行参数：
- `--minimized`：启动后直接最小化到系统托盘。
- `--debug`：输出内核的详细调试日志。

## 配置与日志目录

| 操作系统 | 配置目录 | 日志目录 |
|---|---|---|
| **Windows** | `%APPDATA%\socksicle\` | `%LOCALAPPDATA%\socksicle\logs\` |
| **Linux** | `~/.config/socksicle/` | `~/.config/socksicle/logs/` |

所有设置、节点和订阅均以 JSON 格式存储。敏感字段（密码、UUID、私钥等）在写入磁盘前均经过 AES-256-GCM 高度加密。

## 已知限制

- **TUN 与 Kill Switch：** 目前处于公测阶段。如遇程序异常崩溃，可能需要重新启动一次 Socksicle 以恢复默认系统路由及防火墙规则。
- **Xray DoT：** Xray 核心不支持 DNS-over-TLS（`tls://`）。如需加密 DNS，请使用 DNS-over-HTTPS（`https://`）或切换为 sing-box 核心。
- **macOS：** 暂不支持。

## 社区与交流

- 问题反馈与建议：[GitHub Issues](https://github.com/Socksicle/desktop/issues)
- 最新动态推送：Telegram [@iwtsddev](https://t.me/iwtsddev)

## 开源协议

本项目采用 [MIT](LICENSE) 协议开源。`sing-box` 与 `Xray-core` 为第三方独立项目，受各自开源协议约束。
