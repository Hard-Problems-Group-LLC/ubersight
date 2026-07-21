#!/usr/bin/env bash
# Install the host packages required to build Ubersight's managed Python.
#
# Pyenv can complete a CPython build without optional native modules when their
# development headers are absent.  In particular, Ubersight needs ncurses
# headers so its managed runtime includes the standard-library `_curses`
# extension.  Keep this package mapping here, rather than in install.sh, so a
# normal user can prepare the host explicitly before a pyenv build.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/install_prerequisites.sh [--dry-run] [--json]

Install the supported host packages needed to build Ubersight's managed
Python runtime, including the native ncurses headers required by curses.

Options:
  -h, --help  Show this help and exit.
  --dry-run   Print the package-manager commands without running them.
  --json      Print the selected package-manager plan as JSON and exit.
              This option never installs packages.

The installer selects apt-get, dnf, yum, or Homebrew from PATH. It uses sudo
for apt-get, dnf, and yum when the caller is not already root.
EOF
}

# Print a concise error and stop the prerequisite installation.
fail() {
  printf 'install_prerequisites.sh: %s\n' "$*" >&2
  exit 1
}

# Return the first supported package manager available on PATH.
detect_package_manager() {
  local manager
  for manager in apt-get dnf yum brew; do
    if command -v "$manager" >/dev/null 2>&1; then
      printf '%s\n' "$manager"
      return 0
    fi
  done
  return 1
}

# Render one shell-safe command without running it.
print_command() {
  local argument
  printf '[install_prerequisites.sh] would run:'
  for argument in "$@"; do
    printf ' %q' "$argument"
  done
  printf '\n'
}

# Render the selected package plan for scripts and regression tests.
print_json_plan() {
  local manager="$1"
  shift

  local package
  local first="1"
  printf '{"package_manager":"%s","packages":[' "$manager"
  for package in "$@"; do
    if [[ "$first" == "0" ]]; then
      printf ','
    fi
    printf '"%s"' "$package"
    first="0"
  done
  printf ']}\n'
}

# Parse options, select the platform packages, and install or display them.
main() {
  local dry_run="0"
  local json_output="0"
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        return 0
        ;;
      --dry-run)
        dry_run="1"
        ;;
      --json)
        json_output="1"
        dry_run="1"
        ;;
      *)
        fail "unknown option: $1"
        ;;
    esac
    shift
  done

  local manager
  manager="$(detect_package_manager)" || {
    fail "no supported package manager found (apt-get, dnf, yum, or brew)"
  }

  local -a packages=()
  local -a update_command=()
  local -a install_command=()
  case "$manager" in
    apt-get)
      packages=(
        python3 python3-pip python3-venv git curl ca-certificates
        build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev
        libsqlite3-dev libffi-dev liblzma-dev libncursesw5-dev tk-dev xz-utils
      )
      update_command=(apt-get update)
      install_command=(apt-get install -y "${packages[@]}")
      ;;
    dnf|yum)
      packages=(
        python3 python3-pip git curl ca-certificates gcc make patch
        zlib-devel bzip2 bzip2-devel readline-devel sqlite sqlite-devel
        openssl-devel libffi-devel ncurses-devel xz xz-devel tk-devel findutils
      )
      install_command=("$manager" install -y "${packages[@]}")
      ;;
    brew)
      packages=(python git curl ncurses)
      update_command=(brew update)
      install_command=(brew install "${packages[@]}")
      ;;
    *)
      fail "unsupported package manager: $manager"
      ;;
  esac

  if [[ "$json_output" == "1" ]]; then
    print_json_plan "$manager" "${packages[@]}"
    return 0
  fi

  local -a privilege_prefix=()
  if [[ "$manager" != "brew" && "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || {
      fail "sudo is required to install packages with $manager"
    }
    privilege_prefix=(sudo)
  fi

  if [[ "$dry_run" == "1" ]]; then
    printf '[install_prerequisites.sh] Package manager: %s\n' "$manager"
    if [[ "${#update_command[@]}" -gt 0 ]]; then
      print_command "${privilege_prefix[@]}" "${update_command[@]}"
    fi
    print_command "${privilege_prefix[@]}" "${install_command[@]}"
    return 0
  fi

  printf '[install_prerequisites.sh] Installing prerequisites with %s\n' "$manager"
  if [[ "${#update_command[@]}" -gt 0 ]]; then
    "${privilege_prefix[@]}" "${update_command[@]}"
  fi
  "${privilege_prefix[@]}" "${install_command[@]}"
  printf '[install_prerequisites.sh] Prerequisites installed. Rerun ./install.sh; it will rebuild any configured pyenv runtime that lacks curses support.\n'
}

main "$@"
