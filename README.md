# Project Silverfish Co-op

An early Windows prototype for one host and one guest. See the [18 locked rules](docs/DESIGN.md) for the full design.

The launcher copies the stock game into a separate runtime, installs a small UE4SS bridge in the copy, and starts Unreal with a separate user-data directory. Players connect directly to the host's UDP port using an authenticated, encrypted QUIC connection. The launcher relays game packets to Unreal over loopback.

## Setup

Use Python 3.11 or later, a Steam installation of Project Silverfish, and an extracted [UE4SS v4.0.0 release](https://github.com/UE4SS-RE/RE-UE4SS/releases/tag/v4.0.0). Stage the same game build and mod revision on each PC.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$sfPython = '.\.venv\Scripts\python.exe'
$sfGame = 'C:\Program Files (x86)\Steam\steamapps\common\Project Silverfish'
$sfUe4ss = 'C:\Path\To\Extracted\UE4SS_v4.0.0'
& $sfPython -m tools.silverfish stage --source $sfGame --ue4ss $sfUe4ss
```

If needed, extract `zCustomGameConfigs.zip` from the same release. Pass `--signatures` pointing to its `Project Silverfish\UE4SS_Signatures` folder. Use `--work 'C:\Path\To\LocalWorkspace'` before the subcommand to set a different workspace for staging and all later commands. The default is `.work` under the repository.

## Run a session

On the host, use a dedicated terminal for the running process. Substitute your machine's LAN IP and your chosen UDP port.

```powershell
& $sfPython -m tools.silverfish host --campaign expedition --bind 192.168.1.25 --port 7777
```

For same-PC testing, omit `--bind` to use loopback. On separate PCs, the guest must be able to reach the chosen UDP endpoint. Set `--address` when inviting if the guest uses a different reachable address from the local bind address. Hosting across networks may require firewall or router setup.

In another host terminal, wait for `state: hosting`, then create a guest and a starter character. The command returns the path to a private invitation file. Share that file privately with the guest. It includes a credential and the host certificate. Reuse the same file for the same character.

```powershell
& $sfPython -m tools.silverfish status --campaign expedition
& $sfPython -m tools.silverfish invite --campaign expedition --name Guest --character Scout
```

On the guest PC, launch with the received invitation.

```powershell
& $sfPython -m tools.silverfish join --profile scout --invite 'C:\Path\To\Received.invite.json'
```

The host starts on the shipped `PlaytestMap_01`. Add `--headless` to host and join for diagnostic runs. Use windowed launches for manual movement and gameplay checks.

## Local controls and AI operation

`status`, `events`, and `action` emit JSON. Run them from a second terminal or an automation process on the same PC as the launcher being controlled. Status includes role, map, pawn positions, the number of Unreal clients, transport state, and the host's campaign revision and character lease. `health` is `null` when the stock game shares the underlying save reference or the field is unavailable. Compare both the game and transport states when verifying a connection.

```powershell
& $sfPython -m tools.silverfish status --campaign expedition
& $sfPython -m tools.silverfish status --profile scout
& $sfPython -m tools.silverfish events --campaign expedition --tail 20
& $sfPython -m tools.silverfish action --campaign expedition checkpoint
& $sfPython -m tools.silverfish action --profile scout drop-link
& $sfPython -m tools.silverfish action --profile scout reconnect
& $sfPython -m tools.silverfish action --profile scout stop
& $sfPython -m tools.silverfish action --campaign expedition stop
```

`drop-link` deliberately stops the guest's Direct IP transport and suspends automatic retries. `reconnect` resumes them. A real link failure can trigger automatic retry. A short lapse can preserve the native Unreal pawn while it remains live. If Unreal tears it down, a replacement can restore the character's saved position when available. Full 90-second pawn retention after engine teardown is future campaign work.

Action responses have an `id` and an `ok` flag. Events carry the session generation and, for actions, the command ID. Inspect `stale` before trusting a status. Host and guest status files are under `campaigns/<campaign>/run` and `clients/<profile>/run` within the workspace. The interface accepts allowlisted commands from the local workspace.

## What persists

The host writes a versioned campaign snapshot, a previous snapshot for recovery, and host-issued character identities under `campaigns/<campaign>/save`. This build stores the test map, character positions, the co-op gameplay ledger, and the host's observed native world state. The separate `game-user` folders isolate any stock game writes. The playtest fixtures cover the shared wallet, owned inventories, revive, and travel. See the [native integration status](docs/NATIVE-INTEGRATION.md) for the full-campaign work.

Keep stages, saves, host identity keys, invitations, and extracted assets outside Git. The repository contains mod code, tooling, and docs.

## Tests

```powershell
& $sfPython -m unittest discover -s tests -v
```

Run the optional Windows integration test against an existing staged workspace.

```powershell
& $sfPython tests/live_smoke.py --work .work --vanilla-saved "$env:LOCALAPPDATA\SilverFish\Saved"
```

It starts two headless staged game processes, joins through an encrypted loopback relay, tests both deliberate reconnect and dropped packets, restarts the host, and verifies the guest's existing character returns. It records the results in the generated validation campaign. The optional vanilla path is used for a before-and-after hash comparison.

## Playtest gameplay

Enable `--fixtures` on the host to run the two-player mechanics test on the shipped playtest maps. Both processes still use the normal Direct IP path. The fixture gives the session one world pickup and a controlled way to exercise damage, rewards, and purchases.

Use the invitation to list and create characters before starting the guest game. Pass `--character` to select one of the returned IDs. Omitting it selects the character specified in the invitation.

```powershell
& $sfPython -m tools.silverfish characters --invite 'C:\Path\To\Received.invite.json'
& $sfPython -m tools.silverfish create-character --invite 'C:\Path\To\Received.invite.json' --name Scout
& $sfPython -m tools.silverfish join --profile scout --invite 'C:\Path\To\Received.invite.json' --character $sfCharacterId
```

Playtest operators and AI clients can schedule bounded native movement and submit party interactions locally. Use `status` to read the resulting pawn positions, independent inventories, downed state, shared cash, travel epoch, and pending respawns. The guest receives the host's authoritative gameplay state over the authenticated connection. Check `gameplay_fresh` alongside the displayed game connection state.

```powershell
& $sfPython -m tools.silverfish action --profile scout walk --x 0 --y 1 --milliseconds 300
& $sfPython -m tools.silverfish action --profile scout interact --target playtest-toy
& $sfPython -m tools.silverfish action --campaign expedition test-damage --target guest --amount 100
& $sfPython -m tools.silverfish action --campaign expedition interact --target $sfCharacterId
& $sfPython -m tools.silverfish action --campaign expedition travel --target /Game/Classes/Maps/PlaytestMap_02
```

For an operation that changes state, an explicit `--operation-id` lets a caller retry with the same ID and arguments after a lost response. The host persists recent receipts. A retry with different arguments is rejected. A claimed loot stack stays claimed across restarts.

To exercise item retention, start a test campaign with `--fixtures --fixture-keep-slots 1 --fixture-backpacks`. Record the host location with `action --campaign expedition rest`. When all active participants are downed, the host freezes the roster. Each player can use `action … keep --keep ITEM=COUNT` within the selection window. Owner-only backpacks appear in the structured gameplay state and can be recovered through `interact --target BACKPACK_ID`.

`test-quest-reward` grants a controlled personal item to each eligible character and credits the party once. `buy --target training-supplies` exercises an atomic balance, stock, and inventory update. The party balance projects into the staged host's native cash flag. The fixture inventories are kept by the co-op save service.

## Native integration labs

The staged host reports placed merchant stock, saved scene fields, and an allowlisted authored quest probe. The `vitals-probe` and `vitals-reset` commands verify transient guest save ownership in a fixture session. A C++ lab uses the stock packages to test native inventory value copying, guest death and revive, and native health restoration after travel or restart. See the [native lab instructions](native/README.md). The generated death overlay is qualified for the sampled Steam build and uses a separate stage. See the [local cooked-patch tool](tools/cooked_patch/README.md).

```powershell
& $sfPython tests/live_smoke.py --work .work --gameplay --wipe --zones --native-probe --native-quest
```

Full stock inventory and equipment routing, native purchases and quest turn-in, natural zone exits, stock combat, and Steam Invite are development work. Gameplay fixtures and the native labs run from the staged workspace.
