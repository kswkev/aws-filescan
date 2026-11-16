#!/usr/bin/env bash
#
# Builds the web UI and starts the local dev proxy (default http://127.0.0.1:8080).
# bash equivalent of scripts/start-web.ps1 (Linux, macOS, WSL, Git Bash).
#
#   1. Loads the emulator env (refreshes .emulator/ca.pem, used by the proxy for TLS).
#   2. Builds web/target/dist (mvn package -pl web -DskipTests).
#   3. Runs web/src/main/python/devproxy.py, which reads the filegateway Function URL
#      from `terraform output` (deploy first with scripts/deploy.sh).
#
# Usage:  scripts/start-web.sh [--port N]      (--port 0 = any free port). Stop with Ctrl+C.
set -euo pipefail

usage() { sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; }

port=8080
while [ $# -gt 0 ]; do
    case "$1" in
        --port | -Port) port="${2:?--port needs a value}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Arguments are already parsed; clear them so `source` does not pass them on to
# emulator-env.sh (which would read them as the container name).
set --
# shellcheck source=emulator-env.sh
source "$script_dir/emulator-env.sh"

cd "$script_dir/.."
mvn -q package -pl web -DskipTests

# The proxy needs only the standard library, so any Python 3.12+ works.
python=""
for candidate in .venv/bin/python .venv/Scripts/python.exe python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        python="$candidate"
        break
    fi
done
if [ -z "$python" ]; then
    echo "ERROR: no Python found (create .venv or install Python 3.12+)" >&2
    exit 1
fi

exec "$python" web/src/main/python/devproxy.py --port "$port"
