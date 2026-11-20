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

The module passes the verified executable as the Windows application path and the only argument. It confines inherited handles to the read end of the receipt pipe and quiet output. It creates the coordinator suspended and resumes it only after verifying the exact process image, detached job state, and receipt write.

`SilverfishReadCoopBootstrapLaunchStatus()` returns integer `1`, the phase, the generation or `nil`, and the request ID or `nil`. The phases are `unavailable`, `checking`, `offered`, `queued`, `started`, and `failed`. **Started** means the coordinator process passed the launch checks and resumed. Coordinator readiness and the original title's eventual retirement require a separate proof. The loader log records the start or a bounded failure code. This module issues one startup offer per process.

## Isolated verification

Use a stock-layout replica and fresh isolated `-userdir` as described in the [bootstrap check](../README.md). Verify the title remains usable with the coordinator absent, a wrong hash, or a reparse point at the expected sibling. For a positive test, install the exact packaged worker, focus the replica, and press F7 once. Confirm one launch, the exact two IDs at the worker, receipt EOF, and the absence of an extra console window. A held key or repeated press for that generation leaves the process count steady.

Try the same positive case in a test job that allows explicit breakaway. For a controlled refusal use a job that forbids breakaway. The latter should log `job_breakaway_rejected` or `child_job_not_detached`, keep the title usable, and leave the coordinator unstarted. Verify the stock save hashes and process tree in all cases.
