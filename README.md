# AWS FileScan

A web UI for uploading files to S3. A Lambda function then simulates a virus scan and moves each file to a `clean` or `quarantine` bucket. Everything runs against a local AWS emulator in Docker.

- What it does: [docs/BRIEF.MD](docs/BRIEF.MD)
- How it's being built: [docs/PLAN.MD](docs/PLAN.MD)
- What the emulator can and can't do: [docs/EMULATOR.MD](docs/EMULATOR.MD)

> **Note:** the brief specifies Java/Micronaut lambdas. The emulator only runs Python and Node.js functions, so the lambdas are written in Python. Maven still drives the build.

## Project layout

| Path | Contents |
|---|---|
| `lambda/filegateway` | Python Lambda: pre-signed upload URLs, scan status, file listings |
| `lambda/filescan` | Python Lambda: SQS consumer that "scans" and moves uploaded files |
| `web` | Static UI (`src/main/web`) and, from Phase 6, the local dev proxy |
| `terraform` | All AWS resources, targeting the emulator |
| `scripts/emulator-env.ps1` | Connects a PowerShell session to the emulator |

## Prerequisites

- Docker, with the `aws-emulator` container running on `127.0.0.1:9297`
- JDK 17+ and Maven 3.9+
- Terraform 1.6+
- AWS CLI v2
- Python 3.12 (for the dev/test virtualenv)

## Setup (once)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

## Every session

The emulator issues new keys each time it starts. Load them into your shell first, and re-run this after any emulator restart:

```powershell
. .\scripts\emulator-env.ps1     # note the leading dot
```

## Build & test

```powershell
mvn package               # runs pytest, builds lambda zips and web/target/dist
mvn package -DskipTests   # same, without tests
```

Outputs:
- `lambda/filegateway/target/filegateway.zip`
- `lambda/filescan/target/filescan.zip`
- `web/target/dist/`

## Deploy to the emulator

```powershell
mvn -Pdeploy install      # build, test, then terraform init + apply
```

## Rules for Lambda code

- Use only the standard library and `boto3`. **Never** bundle third-party packages in a function zip: the emulator supplies its own `boto3`, and functions have no network access.
- Code must run on both Python 3.12 (the declared runtime) and 3.14 (what the emulator actually runs).
