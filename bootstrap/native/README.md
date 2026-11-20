# Native title bootstrap bridge

The optional C++ module implements the title-only Lua coordinator callbacks. A verified F7 selection queues one launch of the packaged coordinator. The module reads the live standalone title and checks the originating game window and F7 state before consuming its one-use offer.

## Build and layout

Build `bootstrap/native` with the pinned UE4SS v4.0.0 toolchain used by the gameplay add-on. Set `SILVERFISH_UE4SS_SOURCE_DIR` to that checkout and `SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256` to the lowercase SHA-256 of the packaged Windows coordinator. An empty pin produces a build that offers no launch.

Add the resulting `main.dll` and the exact `SilverfishCoopCoordinator.exe` to the standalone bootstrap package under `SilverfishCoopBootstrap/dlls/`. The Lua entry and native DLL use the same opt-in `SilverfishCoopBootstrap : 1` mod entry. Both coordinator files can be omitted for the read-only Lua package.

On its first qualified title read, the module resolves its own loaded DLL and verifies the sibling executable's normalized local path, ordinary file attributes, and SHA-256. It keeps a read handle that denies write and delete sharing through the launch check.

## Launch contract

`SilverfishReadCoopBootstrapOffer()` and `SilverfishRequestCoopBootstrapOpen(generation, request_id)` use the [title contract](../README.md). The executable receives this exact 78-byte ASCII record followed by EOF via its standard input handle.

```text
1<TAB>open-coop<TAB><32 lowercase hex digits><TAB><32 lowercase hex digits><LF>
```

The module passes the verified executable as the Windows application path and the only argument. It confines inherited handles to the stdin receipt reader, the stdout readiness writer, and quiet stderr. It creates the coordinator suspended and resumes it only after verifying the exact process image, detached job state, and receipt write.

The coordinator sends this 85-byte ASCII record on stdout after verifying the isolated title.

```text
1<TAB>coop-title-ready<TAB><32 lowercase hex digits><TAB><32 lowercase hex digits><LF>
```

The native worker waits up to 120 seconds on its private pipe and the exact coordinator process. It verifies the complete record against the request and rejects extra bytes present on the pipe. The LF ends the record, allowing an outer package runner to retain its writer while the coordinator continues. The coordinator keeps stdout reserved for this record.

`SilverfishReadCoopBootstrapLaunchStatus()` returns integer `1`, the phase, the generation or `nil`, and the request ID or `nil`. The phases are `unavailable`, `checking`, `offered`, `queued`, `started`, `ready`, and `failed`. **Started** means the checked coordinator process resumed. **Ready** also requires its exact readiness record and continued process liveness. The original title requalifies its own stock context before it can retire. The loader log records launch, readiness, and bounded failure codes. This module issues one startup offer per process.

## Isolated verification

For the headless handoff test, build a second adapter with `SILVERFISH_BOOTSTRAP_HEADLESS_PROOF=ON` and the same coordinator SHA-256. Put that adapter only on an isolated stock-layout replica. The stock installer rejects this adapter. The test publishes its private input marker only after the stock title offers co-op. The matched coordinator opens the private title and role with rendering, sound, mouse capture, and mouse lock disabled. The test still exercises the real coordinator process, both pipe records, the stock Quit action, and the staged gameplay startup. [Handoff smoke](../../tests/live_stock_handoff_smoke.py)

Use a stock-layout replica and fresh isolated `-userdir` as described in the [bootstrap check](../README.md). Verify the title remains usable with the coordinator absent, a wrong hash, or a reparse point at the expected sibling. For a positive test, install the exact packaged worker, focus the replica, and press F7 once. Confirm one launch, the exact two IDs at the worker, stdin receipt EOF, the later readiness record, and the absence of an extra console window. A held key or repeated press for that generation leaves the process count steady.

For readiness refusals, exercise a partial record, wrong identity, extra data, a process exit before readiness, and a live coordinator with no record. These paths leave the original title available and give the resumed coordinator responsibility for its own shutdown.

Try the same positive case in a test job that allows explicit breakaway. For a controlled refusal use a job that forbids breakaway. The latter should log `job_breakaway_rejected` or `child_job_not_detached`, keep the title usable, and leave the coordinator unstarted. Verify the stock save hashes and process tree in all cases.
