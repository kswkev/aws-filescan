#!/usr/bin/env bash
#
# Builds, tests and deploys the whole stack to the local aws-emulator in one command.
# bash equivalent of scripts/deploy.ps1 (Linux, macOS, WSL, Git Bash).
#
#   1. Loads the emulator's current keys/CA (scripts/emulator-env.sh).
#   2. Runs `mvn -Pdeploy install`: pytest, lambda zips, web UI, then
#      `terraform init` + `terraform apply -auto-approve`.
#   3. Prints the Terraform outputs.
#
# Usage:  scripts/deploy.sh [--skip-tests]
set -euo pipefail

usage() { sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; }

mvn_args=(-Pdeploy install)
for arg in "$@"; do
    case "$arg" in
        --skip-tests | -SkipTests) mvn_args+=(-DskipTests) ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Clear our arguments first: `source` without arguments would pass them on to
# emulator-env.sh, which would read --skip-tests as the container name.
set --
# shellcheck source=emulator-env.sh
source "$script_dir/emulator-env.sh"

cd "$script_dir/.."
mvn "${mvn_args[@]}"
(cd terraform && terraform output)
