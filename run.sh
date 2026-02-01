#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install -r requirements.txt
timestamp=$(date +"%Y%m%d-%H%M%S")
log_file="logs/run-${timestamp}.log"
export ALSA_LOG_LEVEL=error
python main.py >"${log_file}" 2>&1
