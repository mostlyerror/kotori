#!/usr/bin/env bash
# Launcher for parallel portfolio tracker explorations.
# Installs deps on first run, returns to menu after exit.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

NAMES=("kotori" "the-watcher" "the-cockpit" "the-pipeline" "the-briefing-room" "the-inbox")
TITLES=(
  "★ 🐦 kotori          — Portfolio companion for iron condor traders. (RECOMMENDED)"
  "The Watcher        — Autonomous daemon, read-only TUI. Trust the machine."
  "The Cockpit        — No daemon, all in-process. Human decides everything."
  "The Pipeline       — Kanban board. Positions flow through IC lifecycle lanes."
  "The Briefing Room  — AI narrative is the interface. Data is secondary."
  "The Inbox          — Alert inbox. Only shows what needs attention."
)
ENTRY_POINTS=("kotori" "portfolio" "portfolio" "pipeline" "portfolio" "portfolio")

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

find_free_port() {
  python3 - <<'EOF'
import socket
s = socket.socket()
s.bind(('', 0))
print(s.getsockname()[1])
s.close()
EOF
}

ensure_venv_dir() {
  local name="$1"
  local dir="$2"
  local venv="$dir/.venv"
  if [[ ! -f "$venv/bin/python" ]]; then
    echo -e "${DIM}  Installing deps for $name...${RESET}"
    python3.13 -m venv "$venv" 2>/dev/null
    "$venv/bin/pip" install -e "$dir" -q
    echo -e "${GREEN}  ✓ Ready${RESET}"
  fi
}

launch() {
  local name="$1"
  local entry="$2"
  local dir

  if [[ "$name" == "kotori" ]]; then
    dir="$ROOT"
  else
    dir="$ROOT/.worktrees/$name"
  fi

  local venv="$dir/.venv"
  local port
  port=$(find_free_port)

  echo ""
  ensure_venv_dir "$name" "$dir"
  echo -e "${CYAN}  Launching $name on port $port...${RESET}"
  echo -e "${DIM}  (press q inside the app to return here)${RESET}"
  echo ""

  cd "$dir"
  PORTFOLIO_PORT="$port" "$venv/bin/$entry" || true
  cd "$ROOT"
}

while true; do
  clear
  echo ""
  echo -e "  ${BOLD}🐦 Kotori — Portfolio Companion${RESET}"
  echo -e "  ${DIM}Pick an approach to try. Deps install automatically on first run.${RESET}"
  echo ""

  for i in "${!NAMES[@]}"; do
    local_num=$((i + 1))
    if [[ "${NAMES[$i]}" == "kotori" ]]; then
      venv_path="$ROOT/.venv/bin/python"
    else
      venv_path="$ROOT/.worktrees/${NAMES[$i]}/.venv/bin/python"
    fi
    if [[ -f "$venv_path" ]]; then
      status="${GREEN}●${RESET}"
    else
      status="${DIM}○${RESET}"
    fi
    echo -e "  ${BOLD}$local_num)${RESET} $status  ${TITLES[$i]}"
  done

  echo ""
  echo -e "  ${DIM}● installed  ○ will install on first run${RESET}"
  echo ""
  echo -ne "  ${BOLD}Choice [1-6, q to quit]:${RESET} "
  read -r choice

  case "$choice" in
    1|2|3|4|5|6)
      idx=$((choice - 1))
      launch "${NAMES[$idx]}" "${ENTRY_POINTS[$idx]}"
      ;;
    q|Q)
      echo ""
      echo -e "  ${DIM}Bye.${RESET}"
      echo ""
      exit 0
      ;;
    *)
      echo -e "  ${RED}Invalid choice.${RESET}"
      sleep 1
      ;;
  esac
done
