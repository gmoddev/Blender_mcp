# Security Scan Remediation Ledger

Source evidence: [2026-09-09 repository scan](SECURITY_SCAN_2026-09-09.md), scan ID
`4c01fbde-3fea-430a-b777-d3fcc91b574e`, revision
`21e8048ec2e28f974e2d06d937bfd7d18182a52b`. `NEED LIVE VALIDATION` means static/unit controls exist
but a relevant Blender, OS, network, or filesystem claim has not been exercised.

Focused diff review for the seventh Foundation 0E slice: scan ID
`76a75256-4b31-41ee-bdbe-51232a90b590`, working-tree base `b4aea50`, implementation commit
`802dd2a`. The completed review covered all four changed runtime files and reported no findings.

Focused diff review for the eighth Foundation 0E slice: scan ID
`b8c1ad68-1371-4feb-a301-7bb84c05be26`, working-tree base `729f09c`, implementation commit
`e3e1b14`. The completed review covered the changed motion-capture handler and reported no findings.

Focused diff review for the ninth Foundation 0E slice first identified the omitted simulation-play
cache route as a low-severity authorization gap in scan `c1058a03-0104-4ac7-a4b6-1dee5130088c`.
Implementation commit `db54c4d` added playback to the quarantine and completed the physics-cache
boundary. Corrected-snapshot scan `36a696b8-b80c-4e0e-8924-6037a56fac6b` covered the changed
physics handler and reported no findings.

Focused diff review for the tenth Foundation 0E slice: scan ID
`2355041d-5278-4262-b1ce-41988962554f`, working-tree base `3eb3c16`, implementation commit
`510090a`. The completed review covered the changed texture-bake handler and reported no findings.

Focused diff review for the eleventh Foundation 0E/0F slice: scan ID
`f45af561-30a4-42d4-94f4-cf19e50e74cb`, working-tree base `ca6a06b`, implementation commit
`394ca05`. The completed review covered the Hyper3D, Poly Haven, and Sketchfab runtime handlers
and reported no findings.

Focused diff review for the twelfth Foundation 0C slice: final scan ID
`775c1c7f-f677-40b2-8583-b8be236f99e8`, working-tree base `daeb14b`, implementation commit
`b334569`. The completed review covered server startup and capability-policy runtime files and
reported no findings. An earlier immutable snapshot was separately retained as scan
`20c72118-0466-4193-8090-4f1ba5ce974f`; the final scan covers the atomic per-decision refinement.

| # | Finding / root cause | Foundation | Implementation commit | Regression evidence | Live validation | Disposition |
|---|---|---|---|---|---|---|
| 1 | Hunyuan can upload any Blender-readable local file: caller path flows to an unbounded file read and Tencent POST without root, type, size, enablement, or consent policy. Hyper3D had the same local-image trust boundary. | 0E, 0F, 0G | `918073f` (Hunyuan quarantine); `394ca05` (Hyper3D quarantine) | Hunyuan and Hyper3D dispatcher/direct-call denials; file/network/temp/Blender-I/O canaries; credential-read, sink-removal, and hot-reload-residue assertions | Blender 5.2.1 passed Hyper3D local-file denial and sentinel preservation; controlled provider validation required before any re-enable | **CONTAINED / FEATURE QUARANTINED** — both reachable upload sinks are removed; restoration still requires authorized-file and consent controls |
| 2 | Safe Mode is a no-op: dispatcher trusted unconditional authorization and all raw execution aliases shared normal authority. | 0C | `71e3258` | `test_all_raw_execution_routes_are_denied_in_safe_mode`; `test_raw_execution_stays_denied_without_explicit_raw_mode`; policy matrix | Verify UI state and all four sinks inside Blender | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 3 | Any loopback process could reach raw Python: accept-to-dispatch had no peer identity. | 0A, 0B, 0C | `71e3258` | Unauthenticated dispatch, wrong/expired/replayed/cross-instance proof, remote-host, weak-token, rotation/restart, and revocation tests | Required against a live add-on and separate local process; inspect OS storage and ACLs | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 4 | Hunyuan ZIP URL permits SSRF: arbitrary secondary URL reaches a blind request with no DNS/address/redirect policy. | 0F | `918073f` (quarantine slice) | Dispatcher/direct-call denial across loopback, IPv6 loopback, userinfo, and protocol-relative forms; prohibited-I/O, sink-removal, and hot-reload-residue assertions | Blender restart required when deploying containment; controlled HTTP/DNS validation required before re-enable | **CONTAINED / FEATURE QUARANTINED** — arbitrary URL import is removed and must not return |
| 5 | Pending timeouts can execute later; lost responses were blindly replayed; request identity was not end-to-end. | 0A, 0D | `71e3258` (correlation/no-replay); `e5ad7b9` (atomic queue/ledger/reconciliation); `4ff7586` (main-thread startup/live harness) | Per-request tombstones, running-after-timeout, terminal immutability, duplicate/conflict detection, cancellation, bounded retention, and response-loss reconciliation pass | Blender 5.2.1 passed the single-ledger lifecycle matrix; two-bridge same-ID, typed numeric/string ID, cross-client query/cancel, and disconnect-while-pending fixtures remain | **PARTIALLY ADDRESSED / BLOCKED** — the state machine is strong, but global untyped request IDs do not isolate authenticated bridge instances and stop semantics remain incomplete |
| 6 | Debug logging persisted arbitrary params, code, prompts, paths, nested secrets, results, and exception text. | 0H | `71e3258` (primary-path slice) | Parameter/result/exception canaries, allowlisted metadata, and message-channel canary test | Capture every Blender/bridge/provider/job log with canaries and inspect ACLs | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 7 | Frames, reads, and client threads were unbounded; receive used repeated immutable concatenation. | 0A, 0I | `71e3258` | Zero/truncated/malformed/non-finite/deep/exact-limit/limit+1/Unicode/absolute-deadline tests and active-client-cap test | Slow partial header/body and client-flood test plus four-client peak-memory profile against Blender | **PARTIALLY ADDRESSED / NEED LIVE VALIDATION** |
| 8 | External downloads/archives have incomplete deadlines, byte/member/expansion limits, and cleanup; expensive work can block the main thread. | 0F, 0I | Pending shared implementation; provider containment `918073f` and `394ca05` | All four providers deny external actions through dispatcher and direct-call paths; retired request, tempfile, download, archive/import, and timer helpers are absent and purged on reload | Blender 5.2.1 passed provider denial without scene, world, or sentinel changes; bounded-download and malformed-content validation remains required before re-enable | **CONTAINED / FEATURE QUARANTINED** for current providers — shared network/download/archive/job foundations remain open before functionality can return |
| 9 | Provider secrets are ordinary Scene properties and async render copies retain the scene/temp file. | 0G | `6a0914c` (primary async-render containment); `802dd2a` (legacy headless-render containment); `918073f` and `394ca05` (provider containment) | Render copy/process paths fail before their sinks; all provider external actions are quarantined; Sketchfab and Hyper3D STATUS credential-read canaries prove keys are not inspected or reported | Blender 5.2.1 confirms registered headless and provider actions create no output or scene change; windowed capture and credential-store migration remain pending | **PARTIALLY ADDRESSED / BLOCKED** — reachable provider credential use and render-copy paths are contained, but secrets remain in Scene until a dedicated credential store and migration exist |
| 10 | Caller paths are normalized rather than authorized; force flags and prefix comparisons allow workspace escape/overwrite. | 0E | `fa393b9` (scene/export authority); `481f1a2` (sequencer media authority); `6a0914c` (render/capture containment); `802dd2a` (HDRI/headless containment); `e3e1b14` (motion-capture input authority); `db54c4d` (physics-cache containment); `510090a` (texture-bake output containment); `394ca05` (provider artifact containment) | Canonical-root, traversal/link, overwrite, extension, quarantine, final-sink, and sentinel controls cover the named primary paths | Blender 5.2.1 primary-path checks pass; external GLB texture input, OBJ `.mtl` sentinel, textured USD 5.2 mode, windowed capture, and supported POSIX fixtures remain | **PARTIALLY ADDRESSED / BLOCKED** — destination-path containment is not complete operation authority; exporter input families, OBJ sidecars, USD API drift, atomic publication, remaining sinks, mapped volumes, and cross-platform validation remain |
| 11 | Caller regex executes through Python backtracking over scene names on Blender's main thread. | 0I | Pending | Planned deterministic exact/prefix/suffix/glob selector tests and regex removal assertions | Required with large/adversarial disposable scenes and responsiveness measurement | **OPEN** |
| 12 | Capability authorization read `bpy.context.preferences` on socket client threads before main-thread queue admission. | 0C, THREAD-001 | `b334569` | Fail-closed typed snapshot tests, single-generation mode matrix, worker-thread `bpy` canary, production-path fixture, and non-main-thread startup denial before preference access | Blender 5.2.1 worker authorization passed with no `bpy` reference in the security module | **FIXED** for pre-queue authorization — the broader ExecutionEngine/background THREAD-001 audit remains open |

## Updating This Ledger

Replace `Pending` with the implementing commit hash after a coherent slice is committed. A finding
may move to `FIXED` only when every reachable source-to-sink path is traced, negative regression
tests pass, documentation matches effective behavior, and all relevant live-validation rows are
complete. Disabling a path is acceptable only when that is the intentional permanent control.
