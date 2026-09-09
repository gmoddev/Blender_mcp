# Security Scan Remediation Ledger

Source evidence: [2026-09-09 repository scan](SECURITY_SCAN_2026-09-09.md), scan ID
`4c01fbde-3fea-430a-b777-d3fcc91b574e`, revision
`21e8048ec2e28f974e2d06d937bfd7d18182a52b`. `NEED LIVE VALIDATION` means static/unit controls exist
but a relevant Blender, OS, network, or filesystem claim has not been exercised.

| # | Finding / root cause | Foundation | Implementation commit | Regression evidence | Live validation | Disposition |
|---|---|---|---|---|---|---|
| 1 | Hunyuan can upload any Blender-readable local file: caller path flows to an unbounded file read and Tencent POST without root, type, size, enablement, or consent policy. | 0E, 0F, 0G | Pending | Planned: `test_hunyuan_local_file_requires_authorized_root_and_consent` | Required with a mock provider and disposable files | **OPEN** |
| 2 | Safe Mode is a no-op: dispatcher trusted unconditional authorization and all raw execution aliases shared normal authority. | 0C | `71e3258` | `test_all_raw_execution_routes_are_denied_in_safe_mode`; `test_raw_execution_stays_denied_without_explicit_raw_mode`; policy matrix | Verify UI state and all four sinks inside Blender | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 3 | Any loopback process could reach raw Python: accept-to-dispatch had no peer identity. | 0A, 0B, 0C | `71e3258` | Unauthenticated dispatch, wrong/expired/replayed/cross-instance proof, remote-host, weak-token, rotation/restart, and revocation tests | Required against a live add-on and separate local process; inspect OS storage and ACLs | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 4 | Hunyuan ZIP URL permits SSRF: arbitrary secondary URL reaches a blind request with no DNS/address/redirect policy. | 0F | Pending | Planned: loopback/private/link-local/DNS-change/redirect tests | Required with controlled HTTP/DNS fixtures | **OPEN** |
| 5 | Pending timeouts can execute later; lost responses were blindly replayed; request identity was not end-to-end. | 0A, 0D | `71e3258` (correlation/no-replay); `e5ad7b9` (atomic queue/ledger/reconciliation) | Stable request ID, tombstone-before-dequeue, running-after-timeout/late completion, terminal immutability, cancel, duplicate replay/conflict, no-early-eviction, byte bounds, batch admission, timer failure, shutdown, response-field preservation | Required for live queued/running/reconnect/commit-before-response/reload cases and OS fault injection | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** — shared queue and headless render path implemented; direct provider timer callbacks and process-restart recovery remain outside the ledger |
| 6 | Debug logging persisted arbitrary params, code, prompts, paths, nested secrets, results, and exception text. | 0H | `71e3258` (primary-path slice) | Parameter/result/exception canaries, allowlisted metadata, and message-channel canary test | Capture every Blender/bridge/provider/job log with canaries and inspect ACLs | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 7 | Frames, reads, and client threads were unbounded; receive used repeated immutable concatenation. | 0A, 0I | `71e3258` | Zero/truncated/malformed/non-finite/deep/exact-limit/limit+1/Unicode/absolute-deadline tests and active-client-cap test | Slow partial header/body and client-flood test plus four-client peak-memory profile against Blender | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 8 | External downloads/archives have incomplete deadlines, byte/member/expansion limits, and cleanup; expensive work can block the main thread. | 0F, 0I | Pending | Planned bounded HTTP/archive/cleanup/cancellation suite | Required with stalled/oversized/malformed content and UI responsiveness | **OPEN** |
| 9 | Provider secrets are ordinary Scene properties and async render copies retain the scene/temp file. | 0G | Pending | Planned normal-save/temp-render binary canary and cleanup tests | Required in supported Blender versions plus OS storage inspection | **OPEN** — the control-plane credential is outside Scene, but original provider sinks remain |
| 10 | Caller paths are normalized rather than authorized; force flags and prefix comparisons allow workspace escape/overwrite. | 0E | Pending | Planned traversal, sibling-prefix, symlink, junction/reparse, overwrite, and bypass-flag tests | Required on Windows plus supported POSIX platform | **OPEN** |
| 11 | Caller regex executes through Python backtracking over scene names on Blender's main thread. | 0I | Pending | Planned deterministic exact/prefix/suffix/glob selector tests and regex removal assertions | Required with large/adversarial disposable scenes and responsiveness measurement | **OPEN** |

## Updating This Ledger

Replace `Pending` with the implementing commit hash after a coherent slice is committed. A finding
may move to `FIXED` only when every reachable source-to-sink path is traced, negative regression
tests pass, documentation matches effective behavior, and all relevant live-validation rows are
complete. Disabling a path is acceptable only when that is the intentional permanent control.
