# AWS Emulator – Capability Findings

Results of PLAN.MD step 0.3, probed on 2026-11-16 against the `aws-emulator` container (v1.0.385, released 2026-11-14). Every probe created temporary `probe-*` resources and removed them afterwards.

## Connection

| Item | Value |
|---|---|
| Endpoint | `https://127.0.0.1:9297` (HTTPS, own CA) |
| Region / account | `us-east-1` / `000000000000` |
| Auth | SigV4 required. Keys are regenerated each emulator run; use `. .\scripts\emulator-env.ps1` to load them |
| CA / Java truststore | `ca.pem`, `truststore.p12` in `/home/emulator/.aws/emulator/9297/` (copied to `.emulator/`) |
| Health | `GET /__admin/health` returns `ok` |
| Telemetry | On by default; disable with `EMULATOR_TELEMETRY=off` or `-t off` |

### Pre-existing resources
The emulator already contained three buckets, all created 2026-11-16 13:24 and not by this project's code: `clean`, `quarenteen` (sic) and `upload`. They were empty, with no notifications, and were deleted on 2026-11-16 at the user's request.

## Service support (control plane, called from the host)

| Capability | Result | Notes |
|---|---|---|
| S3 create/list/put/get | ✅ | Path-style requests work |
| S3 pre-signed GET (signed on the host by the AWS CLI) | ✅ | Host `127.0.0.1:9297`; works with the CA trusted |
| S3 → SQS `ObjectCreated` notification | ✅ | An `s3:TestEvent` message is sent first, so consumers must ignore it |
| SQS | ✅ | |
| DynamoDB create/delete table | ✅ | `delete-table` completes asynchronously |
| IAM roles / inline policies | ✅ | **IAM is enforced**, e.g. an event source mapping is rejected if the role lacks `sqs:ReceiveMessage` |
| CloudWatch Logs | ✅ | Function output appears in `/aws/lambda/<fn>` |
| Lambda `python3.12` | ✅ | Create, invoke and logs all work |
| Lambda `java21` | ❌ | `InvalidRuntimeException: … this emulator … runs the Python and node runtimes` |
| Lambda `provided.al2023` | ❌ | Same error |
| SQS → Lambda event source mapping | ✅ | `ReportBatchItemFailures` is accepted |
| Lambda Function URL | ✅ | `https://127.0.0.1:9297/_aws/lambda-url/<id>/`. Needs a `lambda:InvokeFunctionUrl` permission, otherwise 403 |
| API Gateway v2 (HTTP API, Lambda proxy) | ✅ | Call `https://127.0.0.1:9297/_aws/execute-api/<apiId>/<path>`; the returned `ApiEndpoint` hostname doesn't resolve locally |

## Terraform (AWS provider v6.73) quirks

| Quirk | Handling |
|---|---|
| DynamoDB tables report `on_demand_throughput {-1, -1}` | `lifecycle { ignore_changes = [on_demand_throughput] }`, otherwise every plan shows a change |
| Creating `aws_sqs_queue` / `aws_sqs_queue_policy` takes ~25 s each | None needed; the provider waits for attributes to settle |
| Queue URLs come back as `https://sqs.us-east-1.amazonaws.com/000000000000/<name>` | Works with the CLI and SDKs when the endpoint override is set |

## Inside a running Python function

Functions do **not** run in a normal Lambda container:

| Aspect | Finding |
|---|---|
| Interpreter | Python **3.14 (alpha)**, although `AWS_EXECUTION_ENV=AWS_Lambda_python3.12`. Write code compatible with both 3.12 and 3.14 |
| Network | **None.** DNS fails for every host, including `127.0.0.1`/`localhost`. Functions can't make outbound HTTP calls |
| Credentials | No `AWS_ACCESS_KEY_*` env vars. `AWS_ENDPOINT_URL` is not set |
| `boto3` | A **runtime-supplied stand-in** (`/var/task/boto3`) exposing `client`, `resource`, `Session`. Calls are routed straight to the emulator and checked against the function's IAM role. **Don't bundle real boto3/botocore in the deployment zip** |
| S3 operations bound | `GetObject, HeadObject, PutObject, CopyObject, DeleteObject, ListObjectsV2, Get/PutBucketNotificationConfiguration, Get/PutBucketTagging, ListObjectVersions, DeleteObjects`. **No `ListBuckets`** |
| `generate_presigned_url` | ✅ Works locally (no network), but URLs are always signed for host `s3.us-east-1.amazonaws.com`, path-style; `endpoint_url` is ignored |
| DynamoDB | ✅ `put_item`, `get_item`, `scan`, `query`, and `resource('dynamodb').Table(...).put_item` |
| SQS | Calls are bound (`list_queues` succeeded) |

### Using function-generated pre-signed URLs from the browser

| Attempt | Result |
|---|---|
| URL as generated (`https://s3.us-east-1.amazonaws.com/...`) | ❌ host doesn't resolve |
| Same URL with host rewritten to `127.0.0.1:9297` | ❌ `SignatureDoesNotMatch` |
| Rewritten URL sent with header `Host: s3.us-east-1.amazonaws.com` | ✅ HTTP 200, object stored |

A browser can't set the `Host` header. So the web UI is served by a **small local dev proxy** that:
- forwards `/s3/*` to `https://127.0.0.1:9297/*` with `Host: s3.us-east-1.amazonaws.com`;
- forwards `/api/*` to the filegateway Function URL.

The proxy trusts the emulator CA, so the browser only ever talks to `http://localhost`. That also removes the need for CORS and for browser CA trust.

## Impact on the brief
- Lambdas are written in **Python** rather than Java/Micronaut, as agreed with the user on 2026-11-16. Maven remains the build orchestrator, and JDK 17 is used only to run Maven.
