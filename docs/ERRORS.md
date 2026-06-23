# API Error & Code Reference

Every API response is JSON with a consistent envelope. Use the machine `code` for
logic and show the human `message` (and `hint` when present) to users.

## Response envelope
```json
{ "success": true|false, "code": "<machine_code>", "message": "<human text>",
  "hint": "<optional actionable tip>", "request_id": "<id>", ... }
```
- Errors on `/v1/*` and `/api/*` always return JSON (never HTML), including 404/405/500.
- Every response carries an **`X-Request-ID`** header (quote it in support tickets) and
  **`X-RateLimit-Limit/Remaining/Reset`** headers; 429s add `Retry-After`.
- `verify`/`compare` success responses include an HMAC **`signature`** object.

## HTTP statuses
| Status | When |
|--------|------|
| 200 | Processed (check `success` — a denied verify is still 200 with `success:false`). |
| 400 | Bad request (missing/invalid fields). |
| 401 | Missing/invalid API key, or admin login required. |
| 403 | Authenticated key lacks the required role/scope. |
| 404 | No such endpoint or user (data-subject export). |
| 405 | Wrong HTTP method. |
| 429 | Rate limit hit, or monthly quota exceeded. |
| 500 | Unhandled server error (carries a `request_id`). |
| 503 | `/readyz` while the model is still warming. |

## Codes
| `code` | Meaning | What to do |
|--------|---------|-----------|
| `unauthorized` | No/invalid `X-API-Key` | Send a valid key. |
| `forbidden` | Role not permitted (e.g. `verify` key calling enrol) | Use an `admin` key for writes. |
| `admin_required` | First-party enrol/manage without admin session | Log in at `/admin` (direct URL). |
| `rate_limited` | Too many requests | Back off; respect `Retry-After`/`X-RateLimit-*`. |
| `quota_exceeded` | Tenant's monthly quota reached | Raise the quota (admin) or wait for reset. |
| `bad_request` | Validation failed | Fix the payload per `message`. |
| `not_found` | Endpoint/user not found | Check the path / user_id. |
| `missing_user_id` | `user_id` required but absent | Provide `user_id`. |
| `no_face` | No face detected | Move into frame, face camera, improve lighting. |
| `low_quality` | Face too small/unclear | Move closer, hold steady. |
| `multiple_faces` | More than one face | One person at a time. |
| `pose` | Too much head tilt/turn for enrol | Face the camera straight on. |
| `liveness` | Liveness failed / challenge expired | Use a live face + complete the head-turn; request a fresh token. |
| `duplicate` | Face already enrolled as another user | (enrol) Returns the conflicting `conflict_user_id`. |
| `inconsistent` | Capture doesn't match earlier ones | Use the same person for all captures. |
| `not_enrolled` | User has no template | Enrol them first. |
| `match` / `no_match` | Verify/identify outcome | `success` reflects grant/deny. |
| `enrolled` | Enrolment succeeded | — |

Recognition responses also include, where relevant: `score`, `threshold`, `margin`,
`quality` (`det_score`, `face_px`), and `candidates` (1:N). Sandbox keys (`fk_sandbox_*`)
return deterministic canned results with `"sandbox": true`.

See [INTEGRATION.md](INTEGRATION.md) for end-to-end examples and `openapi.yaml` for schemas.
