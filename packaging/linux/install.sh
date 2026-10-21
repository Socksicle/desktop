#!/usr/bin/env bash
set -euo pipefail

REPO="Socksicle/desktop"
BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${HOME}/.local/share/socksicle"
VERSION_FILE="${DATA_DIR}/version"
VARIANT_FILE="${DATA_DIR}/variant"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
TMP_DIR="$(mktemp -d)"
BAR_ACTIVE=0

cleanup() {
  rm -rf "$TMP_DIR"
  if (( BAR_ACTIVE == 1 )); then
    printf '\r\e[2K\n' >&2
  fi
  printf '%s' "${SHOW:-}" >&2
}
trap cleanup EXIT

WANTED_VERSION=""
WANT_UNINSTALL=0
WANT_UPDATE=0
ALLOW_INSECURE=0

usage() {
  cat <<'EOF'
Usage:
  install.sh [options]

Options:
  --version=vX.Y.Z   Install a specific release instead of the latest
  --bindir=PATH      Install target directory (default: ~/.local/bin)
  --update           Update existing Socksicle installation
  --uninstall        Remove installed files
  --insecure         Skip TLS verification (NOT recommended)
  --help             Show this help
EOF
  exit 0
}

unknown() { printf 'Unknown option: %s\n' "$1" >&2; usage; }

for arg in "$@"; do
  case "$arg" in
    --version=*) WANTED_VERSION="${arg#--version=}" ;;
    --version) printf 'Usage: install.sh --version=v1.2.3\n' >&2; exit 2 ;;
    --update) WANT_UPDATE=1 ;;
    --uninstall) WANT_UNINSTALL=1 ;;
    --bindir=*) BIN_DIR="${arg#--bindir=}" ;;
    --insecure) ALLOW_INSECURE=1 ;;
    --help) usage ;;
    *) unknown "$arg" ;;
  esac
done

SPINNER_FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)

if [[ -t 1 && -t 2 ]]; then
  TTY=1
  TERM_COLS=$(tput cols 2>/dev/null || echo 80)
  C_RESET=$'\e[0m'
  C_BOLD=$'\e[1m'
  C_DIM=$'\e[2m'

  C_RED=$'\e[38;2;243;139;168m'
  C_GREEN=$'\e[38;2;166;227;161m'
  C_YELLOW=$'\e[38;2;249;226;175m'
  C_BLUE=$'\e[38;2;137;180;250m'
  C_MAGENTA=$'\e[38;2;203;166;247m'
  C_CYAN=$'\e[38;2;148;226;213m'
  C_TEXT=$'\e[38;2;205;214;244m'
  C_SUBTEXT=$'\e[38;2;166;173;200m'
  C_BORDER=$'\e[38;2;88;91;112m'

  BG_RED=$'\e[48;2;243;139;168m\e[38;2;17;17;27m\e[1m'
  BG_GREEN=$'\e[48;2;166;227;161m\e[38;2;17;17;27m\e[1m'
  BG_YELLOW=$'\e[48;2;249;226;175m\e[38;2;17;17;27m\e[1m'
  BG_BLUE=$'\e[48;2;137;180;250m\e[38;2;17;17;27m\e[1m'

  HIDE=$'\e[?25l'
  SHOW=$'\e[?25h'
else
  TTY=0
  TERM_COLS=80
  C_RESET=""; C_BOLD=""; C_DIM=""
  C_RED=""; C_GREEN=""; C_YELLOW=""
  C_BLUE=""; C_MAGENTA=""; C_CYAN=""
  C_TEXT=""; C_SUBTEXT=""; C_BORDER=""
  BG_RED=""; BG_GREEN=""; BG_YELLOW=""; BG_BLUE=""
  HIDE=""; SHOW=""
fi

[[ "$TERM_COLS" =~ ^[0-9]+$ ]] && (( TERM_COLS > 0 )) || TERM_COLS=80

# When the script is piped into bash, stdin holds the script itself; interactive
# menu input must come from the controlling terminal instead.
MENU_FD=0
if [[ ! -t 0 ]]; then
  if { exec 3</dev/tty; } 2>/dev/null; then
    MENU_FD=3
  else
    MENU_FD=-1
  fi
fi

printf '%s' "$HIDE" >&2

fail() {
  printf '\n  %s FAIL %s %s%s%s\n\n' "$BG_RED" "$C_RESET" "$C_RED" "$*" "$C_RESET" >&2
  exit 1
}
warn() { printf '  %s WARN %s %s%s%s\n' "$BG_YELLOW" "$C_RESET" "$C_YELLOW" "$1" "$C_RESET" >&2; }
ok()   { printf '  %s DONE %s %s%s%s\n' "$BG_GREEN" "$C_RESET" "$C_TEXT" "$1" "$C_RESET" >&2; }
step() { printf '  %s INFO %s %s%s%s\n' "$BG_BLUE" "$C_RESET" "$C_SUBTEXT" "$1" "$C_RESET" >&2; }

header() {
  printf '\n  %s%s▸ %s%s\n\n' "$C_BOLD" "$C_MAGENTA" "$1" "$C_RESET" >&2
}

banner() {
  if [[ $TTY -eq 1 ]]; then
    printf '\n' >&2
    printf '  \e[38;2;203;166;247m\e[1m   ____             _        _      _      \e[0m\n' >&2
    printf '  \e[38;2;186;179;248m\e[1m  / ___|  ___   ___| | _____(_) ___| | ___ \e[0m\n' >&2
    printf '  \e[38;2;169;192;249m\e[1m  \\___ \\ / _ \\ / __| |/ / __| |/ __| |/ _ \\\e[0m\n' >&2
    printf '  \e[38;2;152;205;250m\e[1m   ___) | (_) | (__|   <\\__ \\ | (__| |  __/\e[0m\n' >&2
    printf '  \e[38;2;137;220;235m\e[1m  |____/ \\___/ \\___|_|\\_\\___/_|\\___|_|\\___|\e[0m\n\n' >&2
  else
    cat <<'BANNER' >&2

   ____             _        _      _
  / ___|  ___   ___| | _____(_) ___| | ___
  \___ \ / _ \ / __| |/ / __| |/ __| |/ _ \
   ___) | (_) | (__|   <\__ \ | (__| |  __/
  |____/ \___/ \___|_|\_\___/_|\___|_|\___|

BANNER
  fi
}

CURL_OPTS=(-fL --connect-timeout 15 --max-time 300)
CURL_OPTS_SILENT=(-fsSL --connect-timeout 15 --max-time 30)
if [[ $ALLOW_INSECURE -eq 1 ]]; then
  CURL_OPTS+=(-k)
  CURL_OPTS_SILENT+=(-k)
  warn "TLS certificate verification is DISABLED (--insecure)"
fi

spinner() {
  local pid=$1 label=$2 i=0
  if [[ $TTY -eq 0 ]]; then
    wait "$pid"; return
  fi
  while kill -0 "$pid" 2>/dev/null; do
    printf '\r  %s%s%s %s%s%s' "$C_CYAN" "${SPINNER_FRAMES[$((i % ${#SPINNER_FRAMES[@]}))]}" "$C_RESET" "$C_TEXT" "$label" "$C_RESET" >&2
    i=$((i + 1))
    sleep 0.08
  done
  printf '\r\e[2K' >&2
}

human_size() {
  local b=$1
  if (( b >= 1073741824 )); then
    printf '%d.%d GiB' $(( b / 1073741824 )) $(( b % 1073741824 * 10 / 1073741824 ))
  elif (( b >= 1048576 )); then
    printf '%d.%d MiB' $(( b / 1048576 )) $(( b % 1048576 * 10 / 1048576 ))
  elif (( b >= 1024 )); then
    printf '%d.%d KiB' $(( b / 1024 )) $(( b % 1024 * 10 / 1024 ))
  else
    printf '%d B' "$b"
  fi
}

last_content_length() {
  awk 'tolower($1) == "content-length:" { sub(/\r$/, "", $2); len = $2 } END { print len + 0 }' "$1" 2>/dev/null || echo 0
}

# $1 downloaded bytes, $2 total bytes (0 if unknown), $3 bytes/s, $4 frame index
progress_render() {
  local cur=$1 total=$2 speed=$3 frame=$4
  local width pct filled empty bar_filled bar_empty sizes total_text speed_text
  if (( total > 0 )); then
    pct=$(( cur * 100 / total ))
    (( pct <= 100 )) || pct=100
    total_text="$(human_size "$total")"
    sizes="$(human_size "$cur")/${total_text}"
    speed_text="$(human_size "$speed")/s"
    # keep the line width stable while the downloaded-size text grows
    width=$(( TERM_COLS - 26 - ${#total_text} * 2 ))
    (( width >= 10 )) || width=10
    (( width <= 60 )) || width=60
    filled=$(( width * pct / 100 ))
    empty=$(( width - filled ))
    printf -v bar_filled '%*s' "$filled" ''
    printf -v bar_empty '%*s' "$empty" ''
    printf '\r\e[2K  %s%s%s%s%s  %s%3d%%%s  %s%s%s  %s%s%s' \
      "$C_CYAN" "${bar_filled// /█}" "$C_BORDER" "${bar_empty// /░}" "$C_RESET" \
      "$C_BOLD" "$pct" "$C_RESET" \
      "$C_SUBTEXT" "$sizes" "$C_RESET" \
      "$C_MAGENTA" "$speed_text" "$C_RESET" >&2
  else
    printf '\r\e[2K  %s%s%s  %s%s%s  %s%s/s%s' \
      "$C_CYAN" "${SPINNER_FRAMES[$((frame % ${#SPINNER_FRAMES[@]}))]}" "$C_RESET" \
      "$C_SUBTEXT" "$(human_size "$cur")" "$C_RESET" \
      "$C_MAGENTA" "$(human_size "$speed")" "$C_RESET" >&2
  fi
}

fetch() {
  local url=$1 dest=$2
  if [[ "$dest" == "-" ]]; then
    curl "${CURL_OPTS_SILENT[@]}" "$url"
    return
  fi

  step "Downloading ${C_BOLD}${C_TEXT}${url##*/}${C_RESET}"
  if [[ $TTY -eq 0 ]]; then
    curl "${CURL_OPTS_SILENT[@]}" -o "$dest" "$url" || fail "Failed to download ${url}"
    return
  fi

  local headers="${TMP_DIR}/download.headers" pid cur total start now elapsed speed frame=0
  : > "$headers"
  curl "${CURL_OPTS[@]}" -sS -D "$headers" -o "$dest" "$url" &
  pid=$!
  BAR_ACTIVE=1
  start=$(date +%s%N)
  while kill -0 "$pid" 2>/dev/null; do
    cur=$(stat -c%s "$dest" 2>/dev/null || printf 0)
    total=$(last_content_length "$headers")
    (( total >= cur )) || total=0
    now=$(date +%s%N)
    elapsed=$(( (now - start) / 1000000 ))
    (( elapsed > 0 )) || elapsed=1
    speed=$(( cur * 1000 / elapsed ))
    if (( cur > 0 || total > 0 )); then
      if (( frame == 0 )); then
        printf '\n' >&2
      fi
      progress_render "$cur" "$total" "$speed" "$frame"
      frame=$((frame + 1))
    fi
    sleep 0.1
  done
  printf '\r\e[2K' >&2
  BAR_ACTIVE=0
  wait "$pid" || fail "Failed to download ${url}"
}

api_get() {
  fetch "https://api.github.com${1}" - 2>/dev/null
}

get_installed_version() {
  if [[ -f "$VERSION_FILE" && -x "${BIN_DIR}/socksicle" ]]; then
    cat "$VERSION_FILE" 2>/dev/null || true
  fi
}

RELEASE_JSON=""

release_json() {
  if [[ -n "$RELEASE_JSON" ]]; then return 0; fi
  local path="/repos/${REPO}/releases/latest"
  if [[ -n "${WANTED_VERSION:-}" ]]; then
    local tag="$WANTED_VERSION"
    [[ "$tag" == v* ]] || tag="v${tag}"
    path="/repos/${REPO}/releases/tags/${tag}"
  fi

  local json_file="${TMP_DIR}/release.json"
  ( api_get "$path" > "$json_file" ) &
  spinner $! "Checking GitHub releases..."
  RELEASE_JSON=$(cat "$json_file" 2>/dev/null || true)

  if [[ -z "$RELEASE_JSON" && -z "${WANTED_VERSION:-}" ]]; then
    local tag
    tag=$(api_get "/repos/${REPO}/releases?per_page=5" 2>/dev/null \
      | grep -o '"tag_name": *"[^"]*"' | head -1 | cut -d'"' -f4) || tag=""
    if [[ -n "$tag" ]]; then
      ( api_get "/repos/${REPO}/releases/tags/${tag}" > "$json_file" ) &
      spinner $! "Fetching fallback release ${tag}..."
      RELEASE_JSON=$(cat "$json_file" 2>/dev/null || true)
    fi
  fi
  [[ -n "$RELEASE_JSON" ]] \
    || fail "Could not fetch release metadata. Check your connection or version tag."
}

resolve_version() {
  release_json
  grep -o '"tag_name": *"[^"]*"' <<<"$RELEASE_JSON" \
    | head -1 | cut -d'"' -f4
}

asset_url_by_name() {
  release_json
  local name=$1 url
  url=$(grep -o '"browser_download_url": *"[^"]*"' <<<"$RELEASE_JSON" \
        | cut -d'"' -f4 | grep -F "/${name}" | head -1) \
    || fail "Asset '${name}' was not found in the release"
  printf '%s\n' "$url"
}

menu() {
  local prompt=$1; shift
  local options=("$@") sel=1 key
  if [[ $TTY -eq 0 ]]; then
    printf '  %s\n' "$prompt" >&2
    local i=0
    for opt in "${options[@]}"; do
      i=$((i + 1)); printf '    %d) %s\n' "$i" "$opt" >&2
    done
    local answer
    while true; do
      read -r -u "$MENU_FD" -p "  Select option [1-${#options[@]}]: " answer || fail "No input detected"
      if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= ${#options[@]} )); then
        echo "$answer"
        return 0
      fi
      printf '  Invalid selection.\n' >&2
    done
  fi

  while true; do
    printf '  %s?%s %s%s%s %s(Use ↑/↓ arrows, Enter to confirm)%s\n\n' \
      "$C_MAGENTA" "$C_RESET" "$C_BOLD$C_TEXT" "$prompt" "$C_RESET" "$C_DIM" "$C_RESET" >&2
    local i=0
    for opt in "${options[@]}"; do
      i=$((i + 1))
      if [[ $i -eq $sel ]]; then
        printf '    %s❯%s %s%s%s\n' "$C_CYAN" "$C_RESET" "$C_BOLD$C_CYAN" "$opt" "$C_RESET" >&2
      else
        printf '      %s%s%s\n' "$C_SUBTEXT" "$opt" "$C_RESET" >&2
      fi
    done

    IFS= read -rsn1 -u "$MENU_FD" key
    case "$key" in
      $'\x1b')
        read -rsn2 -t 0.1 -u "$MENU_FD" key || true
        case "$key" in
          '[A') ((sel > 1)) && sel=$((sel - 1)) ;;
          '[B') ((sel < ${#options[@]})) && sel=$((sel + 1)) ;;
        esac
        ;;
      'k'|'K') ((sel > 1)) && sel=$((sel - 1)) ;;
      'j'|'J') ((sel < ${#options[@]})) && sel=$((sel + 1)) ;;
      '')
        printf '\r\e[%dA\e[J' "$(( ${#options[@]} + 2 ))" >&2
        printf '  %s✓%s %s%s:%s %s%s%s\n' \
          "$C_GREEN" "$C_RESET" "$C_BOLD$C_TEXT" "$prompt" "$C_RESET" "$C_CYAN" "${options[$((sel-1))]}" "$C_RESET" >&2
        echo "$sel"
        return 0
        ;;
    esac
    printf '\r\e[%dA\e[J' "$(( ${#options[@]} + 2 ))" >&2
  done
}

choose_variant() {
  local choice
  choice=$(menu "Select package variant" \
    "Standard (sing-box only)" \
    "With Xray (sing-box + Xray)")
  if [[ "$choice" == "2" ]]; then
    printf 'xray'
  else
    printf 'default'
  fi
}

[[ $(uname -m) == "x86_64" ]] \
  || fail "Only x86_64 builds are currently available (detected: $(uname -m))"

command -v curl >/dev/null 2>&1 || fail "curl is not installed. Please install it first."

do_uninstall() {
  header "Uninstalling Socksicle"
  local removed=0 f
  for f in "${BIN_DIR}/socksicle" \
           "${DESKTOP_DIR}/socksicle.desktop" \
           "${ICON_DIR}/socksicle.svg" \
           "$VERSION_FILE" \
           "$VARIANT_FILE"; do
    if [[ -e "$f" ]]; then
      rm -f -- "$f"
      removed=1
    fi
  done
  rmdir "$DATA_DIR" 2>/dev/null || true

  if [[ $removed -eq 1 ]]; then
    ok "Removed Socksicle and desktop shortcuts"
  else
    warn "Socksicle was not found on your system"
  fi
  printf '\n' >&2
}

do_install_appimage() {
  local mode="${1:-install}" variant="${2:-}"

  if [[ -z "$variant" ]]; then
    variant="$(cat "$VARIANT_FILE" 2>/dev/null || true)"
  fi
  [[ "$variant" == "xray" ]] || variant="default"

  if [[ "$mode" == "update" ]]; then
    header "Updating Socksicle"
  else
    header "Installing Socksicle"
  fi

  local ver current_ver url target appimage asset_name
  current_ver="$(get_installed_version)"
  ver="${WANTED_VERSION:-$(resolve_version)}"

  if [[ "$mode" == "update" && -n "$current_ver" ]]; then
    if [[ "$current_ver" == "$ver" ]]; then
      ok "Socksicle is already up to date (${C_BOLD}${C_CYAN}${ver}${C_RESET})"
      printf '\n' >&2
      return 0
    fi
    step "Updating from ${C_BOLD}${C_YELLOW}${current_ver}${C_RESET} to ${C_BOLD}${C_GREEN}${ver}${C_RESET}"
  else
    step "Target version: ${C_BOLD}${C_CYAN}${ver}${C_RESET}"
  fi

  if [[ "$variant" == "xray" ]]; then
    asset_name="Socksicle-${ver#v}-With-Xray-x86_64.AppImage"
  else
    asset_name="Socksicle-${ver#v}-x86_64.AppImage"
  fi
  url=$(asset_url_by_name "$asset_name")
  appimage="${TMP_DIR}/Socksicle.AppImage"
  fetch "$url" "$appimage"
  [[ -s "$appimage" ]] || fail "Downloaded file is empty"
  ok "Downloaded ${url##*/}"

  local sums="${TMP_DIR}/SHA256SUMS"
  ( curl "${CURL_OPTS_SILENT[@]}" -o "$sums" "${url%/[^/]*}/SHA256SUMS" 2>/dev/null ) &
  spinner $! "Checking SHA256SUMS..." || true

  if [[ -s "$sums" ]] && grep -q "$(basename "$url")" "$sums"; then
    local expected actual
    expected=$(grep -F "$(basename "$url")" "$sums" | awk '{print $1}')
    actual=$(sha256sum "$appimage" | awk '{print $1}')
    if [[ "$expected" == "$actual" ]]; then
      ok "SHA256 checksum verified"
    else
      fail "SHA256 checksum mismatch! Aborted."
    fi
  else
    warn "SHA256SUMS not found in release — skipping integrity check"
  fi

  mkdir -p "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR" "$DATA_DIR"
  target="${BIN_DIR}/socksicle"
  mv -f "$appimage" "$target"
  chmod +x "$target"
  echo "$ver" > "$VERSION_FILE"
  echo "$variant" > "$VARIANT_FILE"
  ok "Installed binary to ${target/#$HOME/\~}"

  cat > "${DESKTOP_DIR}/socksicle.desktop" <<EOF
[Desktop Entry]
Name=Socksicle
Comment=Cross-platform proxy client with Material 3 UI
Exec=${target}
Icon=${ICON_DIR}/socksicle.svg
Type=Application
Categories=Network;Security;X-Proxy;
Terminal=false
EOF

  if [[ ! -s "${ICON_DIR}/socksicle.svg" || "$mode" == "install" ]]; then
    ( curl "${CURL_OPTS_SILENT[@]}" -o "${ICON_DIR}/socksicle.svg" \
         "https://raw.githubusercontent.com/${REPO}/main/icon.svg" 2>/dev/null ) &
    spinner $! "Fetching application icon..." || true

    if [[ -s "${ICON_DIR}/socksicle.svg" ]]; then
      ok "Desktop entry and icon installed"
    else
      warn "Failed to download application icon"
    fi
  fi

  command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

  printf '\n  %s✓ Socksicle %s is ready!%s\n' "$C_BOLD$C_GREEN" "$ver" "$C_RESET" >&2
  printf '  %sRun command:%s socksicle\n\n' "$C_SUBTEXT" "$C_RESET" >&2

  if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
    warn "Directory ${BIN_DIR/#$HOME/\$HOME} is not in your PATH."
    printf '  %sAdd it by running:%s\n' "$C_SUBTEXT" "$C_RESET" >&2
    printf '  %secho '\''export PATH="%s:$PATH"'\'' >> ~/.bashrc && source ~/.bashrc%s\n\n' "$C_CYAN" "${BIN_DIR/#$HOME/\$HOME}" "$C_RESET" >&2
  fi
}

banner

if [[ ${WANT_UNINSTALL} -eq 1 ]]; then
  do_uninstall
  exit 0
fi

if [[ ${WANT_UPDATE} -eq 1 ]]; then
  do_install_appimage "update"
  exit 0
fi

INSTALLED_VER="$(get_installed_version)"

if [[ $MENU_FD -eq -1 ]]; then
  if [[ -n "$INSTALLED_VER" ]]; then
    do_install_appimage "update"
  else
    do_install_appimage "install"
  fi
  exit 0
fi

if [[ -n "$INSTALLED_VER" ]]; then
  step "Detected installed version: ${C_BOLD}${C_CYAN}${INSTALLED_VER}${C_RESET}"
  printf '\n' >&2
  choice=$(menu "Select an action" \
    "Update Socksicle" \
    "Reinstall AppImage" \
    "Uninstall Socksicle" \
    "Exit")
  case "$choice" in
    1) do_install_appimage "update" ;;
    2) do_install_appimage "install" "$(choose_variant)" ;;
    3) do_uninstall ;;
    4) exit 0 ;;
  esac
else
  choice=$(menu "Select an action" \
    "Install Socksicle to ~/.local/bin" \
    "Exit")
  case "$choice" in
    1) do_install_appimage "install" "$(choose_variant)" ;;
    2) exit 0 ;;
  esac
fi
