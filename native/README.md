# Native C++ playtest

The C++ add-on hooks the stock `Player_FP_C:Die` Blueprint function in a stage built with the original game packages. The explicit diagnostic arms one authority pawn for one call. The paired playtest renews a short-lived scope for the exact host and authenticated guest after their native health becomes independent.

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
& $sfPython tests/live_smoke.py --work $sfCppLab --native-guest --native-pair --native-pickup --native-wipe --native-quest-flow --native-shop --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-guest --native-pair --native-fall --native-roster --native-pickup --native-guest-save --zones --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
```

The inventory check copies allowlisted inventory, equipment, and vitals from the stock save to a temporary guest SaveGame. It changes one guest-side native item value and verifies that the donor's value stays fixed. The report identifies an existing stock item or a synthetic Bandage in temporary guest storage. This check restores the normal pawn save references.

The paired playtest uses `host --fixtures --native-events --native-guest`. C++ rechecks pawn, controller, connection, world, and save ownership at the stock death call. The smoke test waits for scope renewal, triggers stock direct damage, and checks both in-place revives at 10% of native maximum HP. It tests friendly fire separately through the stock damage interface. The wipe check tests full native health at the saved rest location, removal of the guest Bandage from the backpack, and recovery by its owner through the co-op ledger.

The staged merchant quotes one Bandage from real infinite stock. `native-shop-buy` applies the quoted amount to the shared wallet before issuing the item. The sampled merchant quote is zero. The positive-price and insufficient-funds cases are covered by automated transaction tests. The `status` JSON exposes the verified native inventory GUIDs, native pawn HP, downed state, wallet, and quest progress. The stock inventory widget, generic use and equip actions, and finite shop stock remain development areas.

## Authored travel

```powershell
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-walls --authored-zone --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-walls --observatory-zone --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-door --native-transport --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-gold --native-walls --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-blue --native-transport --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-stardust --native-seed --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-stardust --dream-early --native-seed --native-host-save --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-red --native-roster --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
& $sfPython tests/live_smoke.py --work $sfCppLab --native-zones --native-green --native-roster --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
```

For a manual run, start a new campaign with `host --native-zones --native-zone-walls --start-zone /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P`. Invitations carry the native travel options. The qualified DeadBridge route uses the physical blocker and `DB_CR` arrival marker. The Cistern route uses the stock transition door and `CR_Cstrn` marker. In CrossRoads a Golden Cube in the host's backpack opens the qualified Gold Nexus route. The host service checks the active party within 15 meters and places guests near the host after travel. The host's `native_gold_request` event identifies which character is blocking a Golden Cube use. The host's campaign status records the committed cube GUID and settles its receipt after verified arrival. The `native-gold-*` local fixture actions are available in tests with `--fixtures`.

Add `--native-events --native-guest` for detached stock character saves and scoped native death handling. This combination supports a normal staged Direct IP session without fixture actions. Add `--native-expanded-zones` to opt into the expanded authored route catalog.

The Observatory route uses the stock `Obsv_Etr` exit and arrival marker in both directions. In fixture mode, `native-exit-position` and `native-exit-wall-probe` accept `--entrance Obsv_Etr` to select that exit in CrossRoads. Omit `--entrance` to select `DB_CR` there.

A Blue Cube use by the host reads the exact recorded Observatory or DeadBridge rest. In a campaign with no rest set, it uses the Observatory fallback. `native_blue_request` provides the distance-gate outcome, and `blue_travel_completed` records the live stock bed identity, reputation threshold, and verified arrival location. The Blue smoke test uses a rest-field fixture to exercise in-zone teleport and save restoration.

In CrossRoads, host Stardust use reaches Exchange when all Tarot flags are inactive. The natural smoke test waits the full stock-length visit. Add `--dream-early` to exercise the stock countdown actor's powered return. The host's structured status exposes the visit phase and deadline. `stardust_return_completed` records the exact stock rest placement. `--native-seed` checks the restored seed against the stock day-cycle random stream after restart.

The host save smoke test checks the Unreal save file against its campaign reference, verifies the host pawn adopted the same canonical save generation at startup, and inspects the exact remaining inventory GUID. The generated slots live in the campaign's isolated game profile. Keep that profile with the campaign JSON and backup when moving a campaign.

The Red Cube smoke test exercises a real stock use request, the distance gate, and the durable draw and debit. The `native_roster` status lists each native connection and its verified controller and pawn on the host. `--native-roster` checks the identity through relay reconnect. To exercise two concurrently enrolled guests, run:

```powershell
& $sfPython tests/live_multi_smoke.py --work $sfCppLab --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
```

`--native-green` checks the live eligible destinations and faction relationships, the stock placement for the rolled map, and the durable charge. The guest save test verifies that the separate character's hashed Unreal save is reattached to the correct pawn and still contains the claimed Bandage after host restart. `--native-fall` reports the measured authoritative duration and vertical speed from the stock landing callback.
