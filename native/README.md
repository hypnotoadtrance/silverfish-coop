# Native C++ playtest

The C++ add-on tests an exact pre-call hook on the stock `Player_FP_C:Die` Blueprint function. The stock game packages are used in this stage. The normal diagnostic arms one authority pawn for one synchronous call. An opt-in mode renews a short-lived scope for the authenticated remote guest with independent native health.

## Build the matched runtime and add-on

Fetch UE4SS v4.0.0 with recursive submodules. The release points to UEPseudo `6f8683ffb1be2425e649fc3817189b99316cad0f` and PatternSleuth `1d90b02c7610b595f940af04ce97e0f6feb06923`. Epic-linked GitHub access is needed for UEPseudo. Keep the SDK checkout and build outputs in the local Tools workspace. [UE4SS C++ build guide](https://github.com/UE4SS-RE/RE-UE4SS/blob/v4.0.0/docs/guides/creating-a-c%2B%2B-mod.md)

From an x64 Visual Studio Developer PowerShell with CMake, Ninja, and a Windows MSVC Rust toolchain on its path, run from the mod repository root. The pinned Rust dependency uses edition 2024 and let chains, so use Rust 1.88 or newer. The validated build used MSVC 19.44 and Rust 1.100.0.

```powershell
cmake -S native -B $sfBuild -G Ninja "-DSILVERFISH_UE4SS_SOURCE_DIR=$sfUe4ssSource" -DCMAKE_BUILD_TYPE=Game__Shipping__Win64
cmake --build $sfBuild --target SilverfishCoopNative
$sfConfig = Join-Path $sfBuild 'Game__Shipping__Win64'
$sfCppRuntime = Join-Path $sfConfig 'bin\UE4SS.dll'
$sfCppAddon = Join-Path $sfConfig 'SilverfishCoopNative\main.dll'
```

## Stage and verify

Select a new workspace for `$sfCppLab`. The stage manifest records both DLL hashes and gives the C++ lab a distinct compatibility fingerprint. Use the UE4SS release folder and Silverfish signatures described in the main README for the loader package and configuration.

```powershell
& $sfPython -m tools.silverfish --work $sfCppLab stage --source $sfGame --ue4ss $sfUe4ss --signatures $sfSignatures --cpp-runtime $sfCppRuntime --cpp-addon $sfCppAddon
& $sfPython tests/live_smoke.py --work $sfCppLab --native-damage --native-backend cpp --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
```

The live check invokes native direct damage against a transient independent guest save. It checks that the native hook routed the result to co-op, the original save references were restored, both stock `Dead` flags stayed clear, and the guest retained the same pawn and could be revived. The smoke test also checks reconnect and restart.

## Native guest ownership

```powershell
& $sfPython tests/live_smoke.py --work $sfCppLab --native-inventory --native-damage --native-backend cpp-scoped --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-guest --zones --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
```

The inventory check copies allowlisted inventory, equipment, and vitals from the stock save to a temporary guest SaveGame. It changes one guest-side native item value and verifies that the donor's value stays fixed. The report identifies an existing stock item or a synthetic Bandage in temporary guest storage. This check restores the normal pawn save references.

The guest ownership playtest uses `host --fixtures --native-events --native-guest`. The host binds its authenticated guest's exact server pawn to an independent save in memory. C++ rechecks pawn, controller, connection, world, and save ownership at the stock death call. If the host stops renewing the scope, C++ stops intercepting calls and the Lua bridge takes the guest out of the experiment. The smoke test waits for several renewals, runs deterministic stock direct damage, observes the downed event from the normal bridge tick, and checks that the same pawn revives at 10% of native maximum HP. It also checks that the host-owned checkpoint restores health after host-led travel and restart. The `status` JSON exposes the bound pawn, current death scope, and authoritative HP. Native inventory actions still use stock routing and this mode is limited to the playtest maps.
