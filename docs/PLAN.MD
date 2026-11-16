# AWS FileScan – Implementation Plan

Derived from [BRIEF.MD](BRIEF.MD). Each step is a small piece of work that can be checked off when its **Done when** criteria are met. Emulator behaviour referenced throughout is documented in [EMULATOR.MD](EMULATOR.MD).

## Architecture summary

```
 Browser ──http://localhost:8080──▶ web dev proxy (web module)
                                     │  /            → static UI files
                                     │  /api/*       → filegateway Function URL
                                     │  /s3/*        → emulator S3 (Host: s3.us-east-1.amazonaws.com)
                                     ▼
                          ┌──────────────────────────┐
   1. POST /api/uploads ─▶│ filegateway (Lambda, Py) │── pre-signed PUT URL
   2. PUT  /s3/upload/... └──────────────────────────┘
                                     │
 S3 "upload" ──(ObjectCreated)──▶ SQS "upload-events" ──▶ filescan (Lambda, Py)
                                                            │ random clean / quarantine
                                     ┌──────────────────────┼─────────────────────┐
                                     ▼                      ▼                     ▼
                                S3 "clean"          S3 "quarantine"      DynamoDB "file-scan-results"
   3. GET /api/files[/{id}] ─▶ filegateway ─────────────────────────────────────────┘
```

## Decisions & assumptions

1. **Emulator**: the local `aws-emulator` container (v1.0.385) at `https://127.0.0.1:9297`. Connection details and capabilities are in [EMULATOR.MD](EMULATOR.MD).
2. **Lambda language (deviation from the brief)**: the emulator only runs Python and Node.js functions; `java21` and `provided.al2023` fail at invoke. As agreed on 2026-11-16, the lambdas are written in **Python** (`python3.12` runtime) instead of Java/Micronaut. Inside the emulator the interpreter is actually Python 3.14 (alpha), so code must work on both 3.12 and 3.14.
3. **Dependencies in functions**: the emulator gives functions its own stand-in `boto3`, and functions have no network access. Function code therefore uses **only the standard library plus `boto3`**, and the deployment zip must **not** bundle boto3/botocore or any other packages.
4. **Build**: still a **Maven** multi-module project, as the brief requires. Maven packages the Python code into zips, runs `pytest`, and can drive Terraform. JDK 17, which is already installed, is only needed to run Maven.
5. **HTTP entry point**: a **Lambda Function URL** on filegateway, with routing done inside the function. It's verified working and simpler than API Gateway. API Gateway v2 also works in the emulator (via `/_aws/execute-api/<id>/...`) and remains an alternative.
6. **Browser access via a dev proxy**: pre-signed URLs generated inside a function are always signed for `s3.us-east-1.amazonaws.com`. The browser can't reach that host, and can't set the `Host` header needed to make the emulator accept the URL. A small local proxy in the web module:
   - serves the UI;
   - forwards `/s3/*` to the emulator with that `Host` header;
   - forwards `/api/*` to the Function URL.

   As a result the browser never needs CORS or the emulator CA.
7. **Listing/status API**: the brief names only *filegateway* (pre-signed URL) and *filescan*. The UI also needs status and listing endpoints, so these are added to **filegateway** rather than creating a third lambda.
8. **File links**: pre-signed GET URLs from filegateway, rewritten to go through the proxy. The buckets stay private.
9. **Object keys**: `<fileId>/<original-filename>`, where `fileId` is a UUID, so duplicate names don't collide and the UI can track each upload.

---

## Phase 0 – Local environment ✅

### 0.1 Install and verify tooling ✅
- Installed and verified: Docker 29.9.2, JDK 17.0.6, Maven 3.9.4, Terraform 1.17.1 (installed via winget), AWS CLI 2.40.3, and Python 3.14 on the host (for tests and the dev proxy).
- Open terminals need restarting to pick up the updated PATH.

### 0.2 Connect to the AWS emulator ✅
- `scripts/emulator-env.ps1` (dot-source it: `. .\scripts\emulator-env.ps1`):
  - copies the per-run keys, CA and truststore into the git-ignored `.emulator/` folder;
  - writes a host-side AWS config;
  - sets `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CA_BUNDLE`, `AWS_ENDPOINT_URL` and `AWS_REGION`;
  - runs the health check.
- Re-run it after every emulator restart, because the keys change.
- Verified: `aws sts get-caller-identity` and `aws s3 ls` work with TLS verification on.

### 0.3 Verify emulator service support ✅
- All findings are in [EMULATOR.MD](EMULATOR.MD). Summary:
  - S3, SQS, DynamoDB, IAM (enforced), Logs, Python Lambda, S3→SQS notifications, SQS→Lambda mappings, Function URLs and API Gateway v2 all work.
  - Java runtimes do not work.
  - Pre-signed URLs need the proxy described in decision 6.
- The emulator contained the buckets `clean`, `quarenteen` and `upload`. They were empty and have been deleted (see step 2.2).

---

## Phase 1 – Project skeleton ✅

Completed 2026-11-16:
- `mvn package` runs pytest and builds both function zips and `web/target/dist`.
- Builds also work from inside a single module, because `.mvn/` anchors the repo root.
- `-DskipTests` skips pytest.
- `terraform init`, `validate` and `fmt -check` pass, with the AWS provider at v6.73.0.
- `terraform plan` already reaches the emulator with no auth or TLS errors.
- The test virtualenv uses Python 3.12.1, with pytest 9.2, boto3 1.44 and moto 5.2.
- The lambdas and web page are placeholders until Phases 3, 4 and 6.

### 1.1 Root (parent) POM
- `pom.xml` with `<packaging>pom</packaging>`, groupId `com.filescan`, and modules `lambda`, `web`, `terraform`.
- `<pluginManagement>` for `maven-assembly-plugin` and `exec-maven-plugin`.
- **Done when:** `mvn validate` succeeds from the root.

### 1.2 Python dev environment
- `requirements-dev.txt` at the root containing `pytest`, `boto3` and `moto[s3,dynamodb,sqs]`. These are for host-side tests only and are never deployed.
- Create a project virtualenv `.venv/` (git-ignored). The Maven `exec` plugin runs `pytest` from `.venv`.
- **Done when:** `.venv\Scripts\python -m pytest --version` works.

### 1.3 `lambda` aggregator module
- `lambda/pom.xml` (packaging `pom`) with submodules `filegateway` and `filescan`, plus `common` if shared code emerges.
- Each submodule has this layout:
  - `src/main/python/<pkg>/handler.py` – the code;
  - `src/test/python/` – tests;
  - `pom.xml` – zips `src/main/python` into `target/<name>.zip` with `maven-assembly-plugin`, and runs `pytest` in the `test` phase with `exec-maven-plugin`.
- If a `common` package is used, it is copied into each function's zip at package time, since functions can't install dependencies.
- **Done when:** `mvn package` produces `lambda/filegateway/target/filegateway.zip` and `lambda/filescan/target/filescan.zip`, each containing only the project's own `.py` files.

### 1.4 `terraform` module
- `terraform/pom.xml` (packaging `pom`). A `-Pdeploy` profile runs `terraform init` and `terraform apply` with `exec-maven-plugin`.
- Files in `terraform/`: `providers.tf`, `variables.tf`, `outputs.tf`, plus one file per concern (`s3.tf`, `sqs.tf`, `dynamodb.tf`, `iam.tf`, `lambda.tf`).
- **Done when:** `terraform init` succeeds.

### 1.5 `web` module
- Folder structure:
  - `web/src/main/web/` – static `index.html`, `app.js`, `styles.css`;
  - `web/src/main/python/devproxy.py` – the proxy (step 6.5).
- `web/pom.xml` copies the static files into `web/target/dist`.
- **Done when:** `mvn package` produces `web/target/dist/index.html`.

### 1.6 Repo hygiene
- Extend `.gitignore` with `.venv/`, `__pycache__/` and `.pytest_cache/`.
- Root `README.md` with build and run instructions (filled in as phases complete).
- **Done when:** `git status` is clean after a full build.

---

## Phase 2 – Terraform: core AWS resources

### 2.1 Provider configured for the emulator
- `aws` provider with `region = "us-east-1"`, `endpoints { s3, sqs, lambda, dynamodb, iam, sts, logs }` set to `https://127.0.0.1:9297`, and `s3_use_path_style = true`.
- Also set `skip_credentials_validation`, `skip_requesting_account_id` and `skip_metadata_api_check`.
- Credentials and CA come from the env vars set by `emulator-env.ps1`. Never hard-code keys, and keep TLS verification on.
- **Done when:** `terraform plan` runs with no auth or TLS errors.

### 2.2 S3 buckets
- The pre-existing `upload`, `clean` and `quarenteen` buckets were empty and were deleted on 2026-11-16, so Terraform creates all buckets from scratch.
- Manage `upload`, `clean` and `quarantine` in Terraform. No CORS rules are needed, because all browser traffic goes through the same-origin proxy.
- **Done when:** `terraform apply` leaves exactly those three buckets.

### 2.3 SQS queue (+ DLQ)
- Create `upload-events`, plus `upload-events-dlq` with a redrive policy (`maxReceiveCount = 3`).
- Add a queue policy allowing `s3.amazonaws.com` to `sqs:SendMessage`, conditioned on the `upload` bucket ARN.
- **Done when:** both queues exist.

### 2.4 S3 → SQS notification
- Add an `aws_s3_bucket_notification` on `upload` for `s3:ObjectCreated:*` targeting `upload-events`.
- **Done when:** `aws s3 cp` into `upload` produces an `ObjectCreated:Put` message. Expect an initial `s3:TestEvent` message too.

### 2.5 DynamoDB table
- Create `file-scan-results` (PAY_PER_REQUEST) with partition key `fileId` (S).
- Attributes: `fileName`, `bucket`, `status` (`CLEAN`/`QUARANTINE`), `objectKey`, `scannedAt`, `size`.
- Add a GSI `status-index` (PK `status`, SK `scannedAt`) for listing by result.
- **Done when:** `aws dynamodb describe-table` shows the key schema and GSI.

### 2.6 IAM roles & policies
IAM **is enforced** by the emulator, including inside functions, so these policies must be correct:
- **`filegateway-role`**:
  - `s3:PutObject` on `upload/*` (needed to sign upload URLs);
  - `s3:GetObject` on `clean/*` and `quarantine/*`;
  - `dynamodb:GetItem` and `dynamodb:Query` on the table and its index;
  - CloudWatch Logs.
- **`filescan-role`**:
  - `sqs:ReceiveMessage`, `sqs:DeleteMessage` and `sqs:GetQueueAttributes` on `upload-events`;
  - `s3:GetObject` and `s3:DeleteObject` on `upload/*`;
  - `s3:PutObject` on `clean/*` and `quarantine/*`;
  - `dynamodb:PutItem`;
  - CloudWatch Logs.
- **Done when:** both roles exist with their inline policies.

### 2.7 Outputs
- Output the bucket names, queue URL, table name and filegateway Function URL.
- **Done when:** `terraform output` prints them all.

---

## Phase 3 – `filegateway` lambda (Python)

Single handler. It routes on the Function URL event's `requestContext.http.method` and `rawPath`, and returns `{statusCode, headers, body}` with JSON bodies.

### 3.1 `POST /uploads` – request upload URL
- Input: `{ "fileName", "contentType" }`. Validate the name and strip any path components.
- Generate `fileId = uuid4()` and `key = f"{fileId}/{fileName}"`.
- Call `s3.generate_presigned_url("put_object", Params={Bucket: upload, Key, ContentType}, ExpiresIn=300)`.
- Return `{ fileId, uploadUrl, expiresAt }`. The URL is rewritten as in step 3.4.
- **Done when:** unit tests cover the response shape, key format and name sanitising.

### 3.2 `GET /files/{fileId}` – status of one upload
- Call `get_item` by `fileId`.
  - If no item exists, return `{ status: "PENDING" }`.
  - Otherwise return `{ status, fileName, bucket, scannedAt, downloadUrl }`, where `downloadUrl` is a pre-signed GET URL.
- **Done when:** unit tests cover the pending and scanned cases.

### 3.3 `GET /files?status=CLEAN|QUARANTINE` – list files
- Query `status-index`, newest first, and add a `downloadUrl` to each entry.
- **Done when:** a unit test returns both lists correctly.

### 3.4 Public URL rewriting
- Env var `PUBLIC_S3_BASE`: when set (locally `/s3`), replace the `https://s3.us-east-1.amazonaws.com` prefix of every pre-signed URL with it. When it's unset (real AWS), return the URLs unchanged.
- **Done when:** a unit test covers both modes.

### 3.5 Tests
- Run `pytest` with `moto` (`mock_aws`) on the host's real boto3. Don't depend on anything the emulator's boto3 stand-in lacks: no `list_buckets`, and only the S3 operations listed in EMULATOR.MD.
- **Done when:** `mvn test -pl lambda/filegateway` passes.

---

## Phase 4 – `filescan` lambda (Python)

### 4.1 SQS event handler
- `handler(event, context)` loops over `event["Records"]`, `json.loads` each `body`, and reads each S3 record's `bucket.name` and `object.key`. Keys must be URL-decoded with `urllib.parse.unquote_plus`.
- Skip `s3:TestEvent` messages.
- **Done when:** unit tests parse a real captured S3→SQS payload, including the test event.

### 4.2 Random scan decision
- `decide()` is injectable or patchable and uses `random.choice(["CLEAN", "QUARANTINE"])`.
- **Done when:** tests can force either result.

### 4.3 Move the object
- `copy_object` to the `clean` or `quarantine` bucket under the same key, then `delete_object` from `upload`. Both operations are supported by the emulator's boto3.
- **Done when:** a test confirms the object exists only in the target bucket.

### 4.4 Record the result
- `put_item` with `fileId` (the first key segment), `fileName`, `bucket`, `status`, `objectKey`, `scannedAt` (UTC ISO-8601) and `size`.
- **Done when:** the item is readable after processing.

### 4.5 Error handling & idempotency
- Return `{"batchItemFailures": [{"itemIdentifier": messageId}, ...]}` so only failed messages are retried.
- If the source object is already gone but the DynamoDB record exists, treat it as success, since messages can be delivered twice.
- **Done when:** tests cover a failing message within a batch and a duplicate delivery.

### 4.6 Tests
- **Done when:** `mvn test -pl lambda/filescan` passes.

---

## Phase 5 – Terraform: deploy lambdas

### 5.1 Lambda functions
- `aws_lambda_function` for `filegateway` (`filegateway.handler.handler`) and `filescan` (`filescan.handler.handler`):
  - runtime `python3.12`, 30 s timeout;
  - `filename` set to the zip from `lambda/*/target`, with `source_code_hash = filebase64sha256(zip)`.
- Env vars: `UPLOAD_BUCKET`, `CLEAN_BUCKET`, `QUARANTINE_BUCKET`, `TABLE_NAME`, and `PUBLIC_S3_BASE=/s3` (filegateway only).
- **Done when:** both functions are `Active`, and a test `aws lambda invoke` returns successfully.

### 5.2 SQS → filescan event source mapping
- `aws_lambda_event_source_mapping` from `upload-events` to `filescan` with `function_response_types = ["ReportBatchItemFailures"]`.
- **Done when:** `aws s3 cp` into `upload` results in the file moving to `clean` or `quarantine` and a new DynamoDB row appearing.

### 5.3 Function URL for filegateway
- Add an `aws_lambda_function_url` with `authorization_type = "NONE"`.
- Add an `aws_lambda_permission` for `lambda:InvokeFunctionUrl` with `function_url_auth_type = "NONE"`. Without it, calls return 403.
- **Done when:** `curl --cacert .emulator/ca.pem -X POST <url>uploads -d '{"fileName":"a.txt","contentType":"text/plain"}'` returns an upload URL.

### 5.4 Build → deploy wiring
- `mvn -Pdeploy install` (or `scripts/deploy.ps1`): load the emulator env, package, run tests, then `terraform apply`.
- **Done when:** one command builds and deploys the whole stack.

---

## Phase 6 – Web UI

### 6.1 Upload page
- File picker and upload button.
- Flow: `POST /api/uploads` → `PUT` the file to `uploadUrl` (a `/s3/...` path) with the same `Content-Type` → show "Scanning…".
- **Done when:** a file chosen in the browser lands in `upload`.

### 6.2 Status polling
- Poll `GET /api/files/{fileId}` every ~2 s, with a ~60 s timeout, until the status is not `PENDING`.
- Then show **Clean** (green) or **Quarantined** (red), with a link to `downloadUrl`.
- **Done when:** the result shows and the link opens the file.

### 6.3 File lists
- Two panels, **Clean files** and **Quarantined files**, populated from `GET /api/files?status=...`. Each shows the name, scan time and a link.
- Refresh the panels after each scan completes, and provide a manual refresh button.
- **Done when:** both lists show every processed file.

### 6.4 Configuration
- The UI uses only relative paths (`/api`, `/s3`). The proxy reads the Function URL from `terraform output -raw filegateway_url`, or from an env var/argument.
- **Done when:** the UI works after a fresh `terraform apply` with no code edits.

### 6.5 Dev proxy (`devproxy.py`, standard library only)
- Serves `web/target/dist` at `http://localhost:8080`.
- `/api/*` → the filegateway Function URL.
- `/s3/*` → `https://127.0.0.1:9297/*` with the header `Host: s3.us-east-1.amazonaws.com`, passing the method, query string, body and `Content-Type` through unchanged.
- Uses `.emulator/ca.pem` for TLS verification.
- **Done when:** `python web/src/main/python/devproxy.py` serves the UI, and an upload plus a download both work through it.

---

## Phase 7 – End-to-end verification & docs

### 7.1 End-to-end test
- Upload several files through the UI. Verify that:
  - each file ends up in exactly one of `clean`/`quarantine`;
  - `upload` is empty;
  - DynamoDB has one row per file;
  - the UI shows the correct status and working links.
- Automate this as `scripts/e2e.ps1` or a pytest marked `integration`.

### 7.2 Failure checks
- A malformed SQS message reaches the DLQ after 3 attempts.
- An expired pre-signed URL is rejected, and the UI shows a clear error.

### 7.3 Documentation
- Complete `README.md` with:
  - prerequisites;
  - the steps: emulator running → `. .\scripts\emulator-env.ps1` → `mvn -Pdeploy install` → `python web/src/main/python/devproxy.py` → open `http://localhost:8080`;
  - the teardown command (`terraform destroy`);
  - troubleshooting notes: keys change after an emulator restart (re-run the env script), the proxy is required for pre-signed URLs, and no third-party packages may go in function zips.

---

## Suggested order of delivery

1. Phase 1: skeleton.
2. Phase 2: infrastructure, verified with the AWS CLI.
3. Phase 4 and steps 5.1–5.2: the scan pipeline working end-to-end from the CLI.
4. Phase 3 and step 5.3: the API.
5. Phase 6: the UI and proxy.
6. Phase 7: hardening and docs.
