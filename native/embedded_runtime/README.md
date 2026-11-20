# Embedded co-op runtime add-on

`SilverfishCoopEmbeddedRuntime` starts the packaged Python session worker inside the isolated Project Silverfish process. Once the engine tick hook runs on the game thread, it starts one worker thread. Python owns the typed startup request and acquires the exact host or guest engine lease before issuing gameplay commands. Native startup reads the launch environment and verifies the runtime package.

## Build and layout

Build this CMake target with the pinned x64 UE4SS toolchain. Supply the UE4SS checkout as `SILVERFISH_UE4SS_SOURCE_DIR`, the exact CPython 3.14 runtime's headers as `SILVERFISH_CPYTHON_INCLUDE_DIR`, and the lowercase SHA-256 of its package's `runtime-manifest.json` as `SILVERFISH_RUNTIME_MANIFEST_SHA256`. An unpinned build remains inactive. The DLL resolves the necessary Python exports when it loads the verified interpreter at runtime.

```text
stage/SilverFish/Binaries/Win64/ue4ss/Mods/
  SilverfishCoopEmbeddedRuntime/
    dlls/main.dll
    runtime/
      runtime-manifest.json
      python314.dll
      python314.zip
      python314._pth
      site-packages/...
      app/tools/silverfish/embedded_startup.py
```

Use the exact staged runtime built with the matching co-op Python sources, Windows binaries, wheel data, and notices. Enable it in the isolated stage with `SilverfishCoopEmbeddedRuntime : 1`. The module verifies the manifest pin, its Windows x64 and CPython build fields, and every payload file against the manifest before importing Python. Extra files, missing files, reparse points, an unexpected `._pth`, or altered contents refuse startup.

## Startup

The staged engine needs the exact shipping image, a stage manifest, and the bridge under the same workspace's `campaigns/<name>/run/game-bridge/<id>` or `clients/<name>/run/game-bridge/<id>`. `SILVERFISH_COOP_EMBEDDED_STARTUP` is that nonzero lowercase 32-character ID, `SILVERFISH_COOP_BRIDGE` is the full bridge directory, and `SILVERFISH_COOP_EMBEDDED_ENGINE` is `1`. The engine must have started with the same profile's `-userdir` and `-MULTIHOME=127.0.0.1`.

The worker uses only the pinned runtime's stdlib, native extension, dependency, and application paths. It calls `tools.silverfish.embedded_startup.run()` with zero arguments. On a normal return, it finalizes Python on the same worker thread. The staged loader log reports the startup PID and thread IDs, followed by `state=finished` or a bounded failure state. A native `state=starting` entry confirms package validation. The Python startup receipt confirms request acceptance. The existing session status confirms the game is ready.

## Isolated checks

Verify absent or mismatched startup IDs stay inactive, a wrong profile refuses before import, and changed package contents refuse before import. With the matching package and startup request, observe the same staged game PID in the native and Python receipts, separate engine and worker thread IDs, and advancing stock title frames while the worker starts. Exercise Host, Join, and Stop through the qualified engine lease. Stop should return the game to its isolated stock title. Record original saved-file hashes before and after the staged trial.
