# AWS FileScan – Implementation Plan

Derived from [BRIEF.MD](BRIEF.MD). Each step is a small piece of work that can be checked off when its **Done when** criteria are met.

## Architecture summary

```
 Browser (web module)
   │ 1. request pre-signed URL           ┌──────────────────────┐
   ├────────────────────────────────────▶│ filegateway (Lambda) │
   │ 2. PUT file to pre-signed URL       └──────────────────────┘
   ▼
 S3 "upload" ──(ObjectCreated)──▶ SQS "upload-events" ──▶ filescan (Lambda)
                                                            │ random clean / quarantine
                                     ┌──────────────────────┼─────────────────────┐
                                     ▼                      ▼                     ▼
                                S3 "clean"          S3 "quarantine"      DynamoDB "file-scan-results"
   │ 3. poll status / list files                                                  ▲
   └──────────────────────────────▶ filegateway (Lambda) ─────────────────────────┘
```

## Assumptions & open questions

Confirm or correct these before starting Phase 1:

1. **Emulator**: the `aws-emulator` Docker container (image `aws-emulator`, v1.0.385), already running locally. Known facts:
   - Endpoint: `https://127.0.0.1:9297`. It uses HTTPS with its own CA (`ca.pem`), and there is a Java truststore (`truststore.p12`).
   - Region: `us-east-1`. Health check: `GET /__admin/health` returns `ok`.
   - Request authentication is **on**: calls must be SigV4-signed with keys the emulator generates **each run**. They are written to `/home/emulator/.aws/emulator/9297/emulator_credentials` inside the container. All files are in `/home/emulator/.aws/emulator/9297/`.
   - **Not yet verified:** which services it supports, in particular Lambda (Java 21 runtime), SQS→Lambda event source mappings, S3→SQS notifications, and API Gateway. Step 0.3 checks this.
2. **HTTP entry point**: The browser reaches the lambdas through **API Gateway (REST/HTTP API)** in the emulator. Lambda Function URLs are an alternative.
3. **Listing/status API**: The brief only names the *filegateway* (pre-signed URL) and *filescan* lambdas. The UI also needs to list clean/quarantined files and check status. The plan adds these as extra endpoints on **filegateway** rather than creating a third lambda.
4. **File links**: Links to files in the clean/quarantine buckets are served as **pre-signed GET URLs** from filegateway. This avoids making the buckets public.
5. **Web stack**: The web module is a static HTML/JS (or simple framework) app built via Maven (e.g. `frontend-maven-plugin`) and served from an S3 static-website bucket in the emulator (if supported), or locally via a dev server.
6. **Java/Micronaut versions**: Java 21 and Micronaut 4.x, using the `micronaut-function-aws` runtime.
7. **Object keys**: Uploaded objects are keyed `<uuid>/<original-filename>` so that duplicate filenames don't collide, and the UI can track a specific upload by its id.

---

## Phase 0 – Local environment

### 0.1 Install and verify tooling
- Docker Desktop, JDK 21, Maven 3.9+, Terraform 1.6+, AWS CLI v2. The AWS CLI is not currently installed on the host.
- **Done when:** `java -version`, `mvn -v`, `terraform -v`, `aws --version` and `docker ps` all succeed.

### 0.2 Connect to the AWS emulator
- The emulator is already running as the `aws-emulator` container on `127.0.0.1:9297`. Health check: `curl -k https://127.0.0.1:9297/__admin/health` returns `ok`.
- Add a script, e.g. `scripts/emulator-env.ps1`, that runs `docker cp` to copy `config`, `ca.pem`, `truststore.p12` and `emulator_credentials` from `/home/emulator/.aws/emulator/9297/` into a git-ignored folder such as `.emulator/`.
  - The script then sets `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CA_BUNDLE`, `AWS_ENDPOINT_URL=https://127.0.0.1:9297` and `AWS_REGION=us-east-1`.
  - Re-run it whenever the container restarts, because the keys change each run. Never commit these files.
- **Done when:** `aws s3 ls` succeeds against the emulator with the copied keys and CA.

### 0.3 Verify emulator service support
- Make a list/create call for each service the project needs: S3, SQS, DynamoDB, Lambda (including creating a `java21` function), IAM, CloudWatch Logs and API Gateway (v1 and/or v2).
- Also check that S3 bucket notifications to SQS and SQS→Lambda event source mappings actually fire.
- Check how a Lambda running in the emulator reaches the emulator endpoint (hostname/port and TLS trust from inside the function).
- Record the findings in `docs/EMULATOR.MD`.
- If something is missing, choose a fallback before Phase 2. For example: no API Gateway → use Lambda Function URLs or a small local HTTP adapter; no event source mapping → run filescan as a poller.
- **Done when:** every service and integration in the architecture is confirmed supported, or has an agreed fallback.

---

## Phase 1 – Maven project skeleton

### 1.1 Create the root (parent) POM
- `pom.xml` with `<packaging>pom</packaging>`, groupId e.g. `com.filescan`, and modules `lambda`, `web`, `terraform`.
- Centralise versions (Java, Micronaut BOM, AWS SDK v2 BOM, JUnit) in `<dependencyManagement>` / `<pluginManagement>`.
- **Done when:** `mvn validate` succeeds from the root.

### 1.2 Create the `lambda` aggregator module
- `lambda/pom.xml` (packaging `pom`) with submodules `filegateway`, `filescan`, and optionally `common`.
- **Done when:** `mvn validate` lists all lambda submodules.

### 1.3 Create the `lambda/common` submodule (optional but recommended)
- Shared code: bucket/table name constants, configuration keys, the DynamoDB record model (`FileScanRecord`), and the `ScanResult` enum (`CLEAN`, `QUARANTINE`).
- **Done when:** the module compiles and is a dependency of both lambdas.

### 1.4 Scaffold the `lambda/filegateway` and `lambda/filescan` submodules
- Micronaut function modules using `micronaut-function-aws` / `micronaut-function-aws-api-proxy` (filegateway) and `micronaut-function-aws` (filescan).
- Configure the shade/assembly plugin to produce a deployable fat JAR for each lambda.
- **Done when:** `mvn package` produces `target/*-all.jar` (or equivalent) for each lambda.

### 1.5 Create the `terraform` module
- `terraform/pom.xml` (packaging `pom`). Optionally bind `exec-maven-plugin` to run `terraform init/plan/apply` in a dedicated profile, e.g. `-Pdeploy`.
- Directory layout: `terraform/src/main/terraform/` (or `terraform/` directly) with `providers.tf`, `variables.tf`, `outputs.tf`, plus one file per concern.
- **Done when:** `terraform init` succeeds in the module directory.

### 1.6 Create the `web` module
- `web/pom.xml` to build the static site, e.g. using `frontend-maven-plugin` if npm tooling is used.
- **Done when:** `mvn package` produces a `web/target/dist` (or similar) folder containing `index.html`.

### 1.7 Repo hygiene
- `.gitignore`: `target/`, `.terraform/`, `*.tfstate*`, `.idea/`, `node_modules/`, `.emulator/`.
- Root `README.md` with build and run instructions (fill this in as the phases complete).
- **Done when:** `git status` shows no build artefacts after a full build.

---

## Phase 2 – Terraform: core AWS resources

### 2.1 Provider configured for the emulator
- `aws` provider with `region = "us-east-1"` and `endpoints { s3, sqs, lambda, dynamodb, apigateway, apigatewayv2, iam, sts, logs }` all pointing at `https://127.0.0.1:9297`.
- Also set `s3_use_path_style = true`, `skip_credentials_validation` and `skip_requesting_account_id`.
- Credentials come from the emulator's per-run keys, using `shared_credentials_files` or the env vars from step 0.2. Do not hard-code them.
- TLS: trust the emulator CA via `AWS_CA_BUNDLE` / `custom_ca_bundle` pointing at the copied `ca.pem`. Do not disable TLS verification.
- **Done when:** `terraform plan` runs against the emulator with no auth or TLS errors.

### 2.2 S3 buckets
- Create the buckets `upload`, `clean` and `quarantine`.
- Add a CORS rule on `upload` allowing `PUT` from the web origin (required for browser uploads via pre-signed URL).
- Add a CORS rule on `clean` / `quarantine` allowing `GET`, if files are opened directly from the browser.
- **Done when:** `terraform apply` creates all three buckets, and `aws s3 ls` against the emulator shows them.

### 2.3 SQS queue (+ DLQ)
- Create the queue `upload-events` and the dead-letter queue `upload-events-dlq` with a redrive policy (e.g. `maxReceiveCount = 3`).
- Add a queue policy allowing `s3.amazonaws.com` to `sqs:SendMessage` from the `upload` bucket ARN.
- **Done when:** both queues exist.

### 2.4 S3 → SQS event notification
- Add an `aws_s3_bucket_notification` on `upload` for `s3:ObjectCreated:*` targeting `upload-events`.
- **Done when:** uploading a file manually with `aws s3 cp` puts a message on the queue (check with `aws sqs receive-message`).

### 2.5 DynamoDB table
- Create the table `file-scan-results` with partition key `fileId` (S).
- Attributes written by filescan: `fileName`, `bucket` (`clean`/`quarantine`), `status`, `objectKey`, `scannedAt`, `size`.
- Optional GSI on `status` for listing by result.
- **Done when:** the table exists and `aws dynamodb describe-table` shows the key schema.

### 2.6 IAM roles & policies
- `filegateway-role`: `s3:PutObject` on `upload/*`; `s3:GetObject` and `s3:ListBucket` on `clean` and `quarantine`; `dynamodb:GetItem`, `Query` and `Scan` on the table; CloudWatch Logs.
- `filescan-role`: `sqs:ReceiveMessage`, `DeleteMessage` and `GetQueueAttributes` on the queue; `s3:GetObject` and `DeleteObject` on `upload/*`; `s3:PutObject` on `clean/*` and `quarantine/*`; `dynamodb:PutItem`; CloudWatch Logs.
- **Done when:** both roles are created. Keep the policies least-privilege for real-AWS parity, whether or not the emulator enforces IAM (record which in `docs/EMULATOR.MD`).

### 2.7 Outputs
- Output the bucket names, queue URL, table name, API base URL, and web URL.
- **Done when:** `terraform output` prints all of them.

---

## Phase 3 – `filegateway` lambda (pre-signed URLs & listing API)

### 3.1 `POST /uploads` – request upload URL
- Input: `{ "fileName": "...", "contentType": "..." }`.
- Generate a `fileId` (UUID) and the object key `<fileId>/<fileName>`.
- Use `S3Presigner` (AWS SDK v2) to create a pre-signed `PUT` URL for the `upload` bucket, valid for e.g. 5 minutes.
- Output: `{ "fileId", "uploadUrl", "expiresAt" }`.
- **Done when:** a unit test verifies the response shape and key format.

### 3.2 `GET /files/{fileId}` – status of one upload
- Read DynamoDB by `fileId`.
- If no record exists yet, return `{ status: "PENDING" }`.
- Otherwise return `{ status: CLEAN|QUARANTINE, fileName, bucket, downloadUrl }`, where `downloadUrl` is a pre-signed `GET` URL.
- **Done when:** unit tests cover both the pending and the scanned cases.

### 3.3 `GET /files?status=clean|quarantine` – list files
- Scan or query DynamoDB (or list the S3 bucket) and return entries with pre-signed download URLs.
- **Done when:** a unit test confirms that both lists are returned correctly.

### 3.4 Configuration for the emulator
- Endpoint override for S3/DynamoDB is set via env var (e.g. `AWS_ENDPOINT_URL`), with S3 path-style access enabled.
- The AWS SDK must trust the emulator CA. Use the provided `truststore.p12` (`-Djavax.net.ssl.trustStore=...`) or a custom SDK HTTP client.
- Pre-signed URLs must use a host the **browser** can reach (`https://127.0.0.1:9297`), not whatever hostname the lambda uses internally. Pre-signed URLs are SigV4-signed, so the presigner must sign with the emulator's current keys.
- The browser must trust the emulator CA. Import `ca.pem` into the OS/browser trust store; otherwise the upload `PUT` and download links fail with certificate errors.
- **Done when:** the generated URL works from the host machine, both with `curl` and from the browser.

### 3.5 CORS on API responses
- Return `Access-Control-Allow-Origin` (etc.) for the web origin, either via Micronaut CORS config or API Gateway.
- **Done when:** a browser `fetch` from the web origin succeeds.

### 3.6 Tests
- Use JUnit 5 + Micronaut Test with mocked AWS SDK clients for unit tests, and the running emulator for integration tests (run them in a separate Maven profile, since they need the emulator up and its current keys).
- **Done when:** `mvn test -pl lambda/filegateway` passes.

---

## Phase 4 – `filescan` lambda (simulated scan)

### 4.1 SQS event handler
- Micronaut function handling `SQSEvent`.
- Each message body is an S3 event notification. Parse the `bucket` and URL-decoded `key`.
- Ignore S3 `s3:TestEvent` messages.
- **Done when:** a unit test parses a sample S3→SQS payload correctly.

### 4.2 Random scan decision
- Inject a `ScanDecider` interface whose default implementation uses `Random`/`SecureRandom` for a 50/50 split. Inject it so tests can be deterministic.
- **Done when:** unit tests can force both `CLEAN` and `QUARANTINE`.

### 4.3 Move the object
- `CopyObject` from `upload/<key>` to `clean/<key>` or `quarantine/<key>`, then `DeleteObject` from `upload`.
- **Done when:** an integration test confirms that the object exists only in the target bucket.

### 4.4 Record the result in DynamoDB
- `PutItem` with `fileId` (the first segment of the key), `fileName`, `bucket`, `status`, `objectKey`, `scannedAt` (ISO-8601) and `size`.
- **Done when:** the item is readable after processing.

### 4.5 Error handling & idempotency
- Return `SQSBatchResponse` with `batchItemFailures` so that only the failed messages are retried.
- Handle "object already moved" gracefully, since a message may be delivered more than once.
- **Done when:** tests cover a failing message within a batch.

### 4.6 Tests
- **Done when:** `mvn test -pl lambda/filescan` passes.

---

## Phase 5 – Terraform: deploy lambdas & API

### 5.1 Lambda functions
- `aws_lambda_function` for `filegateway` and `filescan`, using runtime `java21`, the fat JAR from `lambda/*/target`, the Micronaut handler class, and roughly 512 MB memory with a 30 s timeout.
- Env vars: bucket names, table name, `AWS_ENDPOINT_URL` (the emulator endpoint as seen from inside a running function, per step 0.3), the external endpoint used for pre-signed URLs (`https://127.0.0.1:9297`), and the truststore location/password if the function needs it for TLS.
- **Done when:** `aws lambda list-functions` shows both functions.

### 5.2 SQS → filescan event source mapping
- `aws_lambda_event_source_mapping` from `upload-events` to `filescan` with `function_response_types = ["ReportBatchItemFailures"]`.
- **Done when:** uploading via the CLI results in the file appearing in `clean` or `quarantine`, plus a new DynamoDB row.

### 5.3 API Gateway for filegateway
- Use an HTTP API (or REST API) with routes `POST /uploads`, `GET /files/{fileId}` and `GET /files`, all proxying to filegateway, with CORS enabled.
- **Done when:** `curl` against the API URL returns a pre-signed URL.

### 5.4 Build → deploy wiring
- Use a script or Maven profile that runs `mvn package` and then `terraform apply`.
- Use `source_code_hash = filebase64sha256(jar)` so that code changes trigger a redeploy.
- **Done when:** a single command builds and deploys the whole stack.

---

## Phase 6 – Web UI

### 6.1 Upload page
- File picker and upload button.
- Flow: `POST /uploads` → `PUT` file to `uploadUrl` with the correct `Content-Type` → show "Scanning…".
- **Done when:** a file selected in the browser lands in the `upload` bucket.

### 6.2 Status polling
- Poll `GET /files/{fileId}` every ~2 s until the status is not `PENDING`, with a timeout of ~60 s.
- Then display **Clean** (green) or **Quarantined** (red), with a link to the file (`downloadUrl`).
- **Done when:** the user sees the result and can open the file through the link.

### 6.3 File lists
- Show two tables/panels, **Clean files** and **Quarantined files**, populated from `GET /files?status=...`. Each shows file name, scan time and a link.
- Refresh after each upload completes; optionally add a manual refresh button.
- **Done when:** both lists show every processed file.

### 6.4 Configuration
- Set the API base URL from a config file, e.g. `config.js` generated from the Terraform output, rather than hard-coding it.
- **Done when:** the UI works after a fresh `terraform apply` without code edits.

### 6.5 Hosting
- Either deploy `web/target/dist` to an S3 static-website bucket via Terraform, or serve it locally with a simple dev server.
- **Done when:** the UI is reachable at a documented URL.

---

## Phase 7 – End-to-end verification & docs

### 7.1 End-to-end test
- Start the emulator, build, and deploy.
- Upload several files through the UI. Verify that each ends up in exactly one of `clean`/`quarantine`, that `upload` is empty, that DynamoDB has one row per file, and that the UI shows the correct status and working links.
- Optionally automate this as a JUnit/Testcontainers test or a shell script.

### 7.2 Failure checks
- Check that a malformed SQS message lands in the DLQ after retries.
- Check that an expired pre-signed URL is rejected, and that the UI shows a sensible error.

### 7.3 Documentation
- Complete `README.md` with:
  - prerequisites;
  - the steps: emulator running → run `scripts/emulator-env.ps1` → build → deploy → open the UI;
  - the teardown command (`terraform destroy`);
  - troubleshooting notes: CA trust in the browser and JVM, keys changing after the emulator restarts, and internal vs external hostnames in pre-signed URLs.

---

## Suggested order of delivery

1. Phases 0–1: environment and skeleton.
2. Phase 2: infrastructure, verified manually with the AWS CLI.
3. Phase 4 and steps 5.1–5.2: the scan pipeline working end-to-end from the CLI.
4. Phase 3 and step 5.3: the API.
5. Phase 6: the UI.
6. Phase 7: hardening and docs.
