#!/bin/bash
# Launch HandsFree. Run scripts/setup.sh once first if not already done.
cd "$(dirname "$0")/.."
exec python3 main.py "$@"
