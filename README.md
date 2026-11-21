# Project Silverfish Co-op

A Windows Direct IP prototype with a native campaign stage. The largest loopback test used one host and four guests. [18 locked rules](docs/DESIGN.md) · [In-game integration plan](docs/IN-GAME-INTEGRATION.md)

The Windows trial installs the UE4SS bridge into the existing game and routes co-op saves to separate campaign scopes. Players connect to the host's UDP port over an authenticated, encrypted QUIC connection. The embedded runtime relays game packets to Unreal over loopback. [Player and automation guide](docs/PLAYING-COOP.md)

## Setup

For the in-game trial, use the Windows trial ZIP and its one-time setup. Open the installed co-op menu from the Steam game title. [Windows trial instructions](docs/PLAYING-COOP.md#install-and-open-the-windows-trial)

### Development checkout

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

The host starts on the shipped `PlaytestMap_01`. Add `--headless` to host and join for diagnostic runs. Headless launches use Unreal's `-RenderOffScreen` option, which selects a cursor backend that leaves the Windows desktop cursor alone. Use windowed launches for manual movement and gameplay checks.

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

`drop-link` deliberately stops the guest's Direct IP transport and suspends automatic retries. `reconnect` resumes them. A real link failure can trigger automatic retry. The host reserves the character for 90 seconds. The retained pawn remains eligible for party rules during that window. At expiry, the host attempts a final native save before retiring the old connection. A fresh join after expiry spawns near the host using the character's saved state.

Action responses have an `id` and an `ok` flag. Events carry the session generation and, for actions, the command ID. Inspect `stale` before trusting a status. Host and guest status files are under `campaigns/<campaign>/run` and `clients/<profile>/run` within the workspace. The interface accepts allowlisted commands from the local workspace.

On native campaign stages, `native-inventory-list` reads an exact stock inventory page. Run it against a guest profile to read that character. On the host, the same action reads the host's inventory or the enrolled role selected with `--target`. Use `next_offset` to advance the page. The guest passes the offset through `--target`. The host uses `--amount`.

The host's `game.native_stock_enemy_drops` status reports recent item actors from stock NPC deaths in the current world. Items from the same death share a roll number. The record includes the stock source NPC, the party pawn credited by the game when available, and each item's class and actor identity.

Pass `--native-guest-inventory-view` on `join` to open the guest's first inventory page with the stock inventory key. Use Page Up or Page Down to select a row, Home twice to equip a supported weapon, and End to cancel or close. Pass `--native-guest-primary-input` and `--native-guest-reload-input` to route a stock primary press or short reload press to the host for the qualified guest SMG and Light Pistol.

At a supported locked trunk or locker, equip a blowtorch and press primary fire, or equip a crowbar and tap Mode Toggle if the lock can be pried with it. For local automation, `native-guest-vehicle-blowtorch` and `native-guest-vehicle-crowbar` accept `--target "TOOL_GUID@CONTAINER_FULL_NAME"`. The response includes the tool result and the authoritative loot roll. The blowtorch uses one fuel charge per successful pry. Carry the matching key and press Interact at a keyed locker. A secure locker's first press returns `native_loot_unlock` through `native-loot-open`. The next press opens it through the shared loot transaction.

The native campaign status reports qualified hinged doors under `game.native_stock_doors`. Guests use the stock Interact action on an unlocked door, or submit `native-stock-door-interact --target "DOOR_FULL_NAME"` when within stock reach. The result reports whether the host opened or closed the door.

Guests can pry eligible hinged doors with the same stock tool controls as vehicle trunks. The crowbar follows the lock's stock strength limit. For local automation, `native-guest-door-crowbar` and `native-guest-door-blowtorch` accept `--target "TOOL_GUID@DOOR_FULL_NAME"`. The host commits the lock change, tool result, and guest inventory together.

In a new authored campaign with native guest ownership, set `--native-extraction-keep N` to enable the stock extraction mode and keep up to `N` inventory rows after a wipe. Add `--native-extraction-backpacks` for owner-only drops and `--native-extraction-lose-slot` to reduce the host's saved retention limit after each wipe. These settings are stored in the campaign's native save and resume automatically.

During a stock retention wipe, the host and guests see their own frozen inventory. Use Page Up or Page Down to move, Space to toggle a whole row, and Home twice to submit. An empty selection is allowed. Local automation can use `native-wipe-items` and `keep --keep GUID=1`. Once the party has respawned, the owner's `native_backpacks` status contains their backpack IDs and death positions. Use `native-wipe-recover --target BACKPACK_ID` while nearby.

Use `native-quest-list` to inspect the host's stock quest progress from a guest profile. `--target stock` lists the shipped templates. Follow `next_offset` with a target such as `stock@8`. At the Black Market Broker station, the guest's interact key submits the supported objective when exactly one is eligible.

At the Observatory merchant, use `native-finite-shop-list` for stock prices and `native-finite-shop-buy --target GUID@PRICE` to buy a whole stock row. Use `native-finite-shop-sale-quote --target GUID` and `native-finite-shop-sell --target GUID@PRICE` to sell a qualified backpack row into the merchant's buyback stock. With `--native-shop-view` on the host, a guest can also use the stock interact key to open the purchase overlay. Use the arrow keys to select, press Enter to review and confirm, and press Backspace to close.

## What persists

The host writes a versioned campaign snapshot, a previous snapshot for recovery, and host-issued character identities under `campaigns/<campaign>/save`. This build stores the test map, character positions, the co-op gameplay ledger, and the host's observed native world state. The separate `game-user` folders isolate any stock game writes. The playtest fixtures cover the shared wallet, owned inventories, revive, and travel. See the [native integration status](docs/NATIVE-INTEGRATION.md) for the full-campaign work.

Keep stages, saves, host identity keys, invitations, and extracted assets outside Git. The repository contains mod code, tooling, and docs.

## Tests

For day-to-day changes, run the affected test module first. Reuse the staged loader and native add-ons when their source and pins still match. For a live gameplay change, start from a copied campaign checkpoint near the affected scene and run that mechanic's smoke test. Run the full suite and rebuild the Windows ZIP when preparing a trial build.

```powershell
& $sfPython -m unittest tests.test_runtime -q
& $sfPython -m unittest discover -s tests -v
```

Use the release stock adapter for the quick `live_stock_bootstrap_smoke.py --coordinator-ready` check. The full headless stock-title handoff requires the separately built replica-only adapter and a matching coordinator. [Bootstrap tests](bootstrap/native/README.md)

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

The staged labs cover selected stock exits, transport items, combat, Bandage purchases and pickups, and quest turn-ins. General campaign interactions, inventory controls, and wide-area networking remain in development. Steam Invite is deferred. See the [native integration status](docs/NATIVE-INTEGRATION.md).
