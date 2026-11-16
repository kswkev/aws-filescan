# shellcheck shell=bash
#
# Connects the CURRENT shell to the local aws-emulator container.
# bash/zsh equivalent of scripts/emulator-env.ps1 (Linux, macOS, WSL, Git Bash).
#
# The emulator generates new SigV4 keys every time it starts and serves HTTPS with
# its own CA. This copies the per-run keys, CA certificate and Java truststore out of
# the container into .emulator/ (git-ignored), writes a host-side AWS config file and
# exports the variables the AWS CLI, SDKs and Terraform read.
#
# It must be SOURCED so the variables persist in your shell:
#
#     source scripts/emulator-env.sh [container-name] [port]
#
# Re-run it whenever the emulator container restarts.
# Note: never `exit`s and does not use `set -e`, so it cannot close your terminal.

# Path of this file; captured at top level (zsh's $0 is the function name inside functions).
_emu_src="${BASH_SOURCE[0]:-$0}"

# Directory -> absolute path that native tools understand. On Git Bash / MSYS the
# Windows builds of aws, terraform, docker and python need E:/... rather than /e/...
_emu_native_dir() {
    (cd "$1" && { pwd -W 2>/dev/null || pwd; })
}

_emu_main() {
    local container="${1:-aws-emulator}"
    local port="${2:-9297}"
    local script_dir repo_root out_dir endpoint src_dir running health f

    script_dir="$(cd "$(dirname "$_emu_src")" && pwd)"
    repo_root="$(_emu_native_dir "$script_dir/..")"
    out_dir="$repo_root/.emulator"
    endpoint="https://127.0.0.1:$port"
    src_dir="/home/emulator/.aws/emulator/$port"

    # 1. The container must be running. MSYS_NO_PATHCONV stops Git Bash rewriting
    #    container paths such as /home/... into Windows paths.
    running="$(MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker ps --filter "name=^/${container}\$" --format '{{.Names}}' 2>/dev/null || true)"
    if [ "$running" != "$container" ]; then
        echo "ERROR: container '$container' is not running. Start it first: docker start $container" >&2
        return 1
    fi

    # 2. Copy the per-run files out of the container.
    mkdir -p "$out_dir" || return 1
    for f in ca.pem truststore.p12 emulator_credentials; do
        if ! MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker cp "$container:$src_dir/$f" "$out_dir/$f" >/dev/null; then
            echo "ERROR: failed to copy $f from $container" >&2
            return 1
        fi
    done

    # 3. Host-side config (the emulator's own config points ca_bundle inside the container).
    cat > "$out_dir/config" <<EOF
[default]
region = us-east-1
endpoint_url = $endpoint
ca_bundle = $out_dir/ca.pem
s3 =
  signature_version = s3v4
  addressing_style = path
EOF

    # 4. Environment for the AWS CLI / SDKs / Terraform in this shell.
    export AWS_CONFIG_FILE="$out_dir/config"
    export AWS_SHARED_CREDENTIALS_FILE="$out_dir/emulator_credentials"
    export AWS_PROFILE=default
    export AWS_REGION=us-east-1
    export AWS_DEFAULT_REGION=us-east-1
    export AWS_ENDPOINT_URL="$endpoint"
    export AWS_CA_BUNDLE="$out_dir/ca.pem"
    export EMULATOR_TRUSTSTORE="$out_dir/truststore.p12"

    # 5. Health check, trusting the emulator CA explicitly (no -k).
    health="$(curl -s --cacert "$out_dir/ca.pem" "$endpoint/__admin/health" 2>/dev/null || true)"
    if [ "$health" != "ok" ]; then
        echo "ERROR: emulator health check failed at $endpoint (response: '$health')" >&2
        return 1
    fi

    echo "Connected to aws-emulator at $endpoint (health: ok). Files in $out_dir"
}

# Refuse to run as a separate process: the exported variables would be lost on exit.
if ! (return 0 2>/dev/null); then
    echo "Source this script so the variables persist:  source scripts/emulator-env.sh" >&2
    exit 1
fi

_emu_main "$@"
_emu_rc=$?
unset -f _emu_main _emu_native_dir
unset _emu_src
return $_emu_rc
