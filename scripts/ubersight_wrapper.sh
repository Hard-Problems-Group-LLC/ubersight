#!/usr/bin/env bash
# Keep the Ubersight side-window dashboard running while the script is being edited.

set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
ubersight="${script_dir}/ubersight.py"
python="${repo_root}/.venv/bin/python"
if [[ ! -x "${python}" ]]; then
  python="$(command -v python3)"
fi

trap 'printf "\n[ubersight-wrapper] stopping.\n"; exit 130' INT TERM

while true; do
  "${python}" "${ubersight}" "$@"
  status=$?
  printf '[ubersight-wrapper] Ubersight exited with status %s; restarting in 1s. Press Ctrl-C to stop.\n' "${status}" >&2
  sleep 1
done
