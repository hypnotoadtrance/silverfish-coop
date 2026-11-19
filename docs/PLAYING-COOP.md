# Playing Project Silverfish Co-op

Use Direct IP through the co-op launcher on Windows. The default mode starts a two-player session on the stock `PlaytestMap_01`. The matched C++ stage adds supported stock campaign areas, interactions, and separate native character saves. The largest loopback party joined with one host and four guests. [Current native coverage](NATIVE-INTEGRATION.md)

## Stage the same build on each PC

Open PowerShell in the mod repository. Use Python 3.11 or later, a Steam installation of Project Silverfish, and the extracted [UE4SS v4.0.0 release](https://github.com/UE4SS-RE/RE-UE4SS/releases/tag/v4.0.0). Point the launcher at the release folder that contains `dwmapi.dll` and `ue4ss`.

```powershell
python -m venv .venv
$sfPython = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $sfPython -m pip install -r requirements.txt

$sfWork = 'C:\Path\To\Silverfish-Coop-Work'
$sfGame = 'C:\Program Files (x86)\Steam\steamapps\common\Project Silverfish'
$sfUe4ss = 'C:\Path\To\Extracted\UE4SS_v4.0.0'
$sfCli = @('-m', 'tools.silverfish', '--work', $sfWork)
& $sfPython @sfCli stage --source $sfGame --ue4ss $sfUe4ss
```

Use a new workspace for each fresh stage. To load the game's signatures, extract `zCustomGameConfigs.zip` from the same UE4SS release and pass `--signatures` with its `Project Silverfish\UE4SS_Signatures` directory. Use the same game build and mod revision on every PC. The launcher copies the stock game files and writes co-op profiles under the workspace. The original Steam launch continues to use the single-player save directory.

Set `$sfPython` and `$sfCli` again when opening a new terminal. The workspace path is local to each PC. On one PC both terminals can use the same stage with separate campaign and guest profiles.

## Host and join

On the host, bind to the host machine's LAN address and choose the UDP port the guest will use. Replace the example address with the address of your host.

```powershell
& $sfPython @sfCli host --campaign expedition --bind 192.168.1.25 --port 7777
```

For two processes on one PC, omit `--bind` to use `127.0.0.1`. On separate PCs, the guest needs to reach the chosen UDP endpoint. The host's firewall may need to allow that port. Across networks, the chosen endpoint may also require router configuration. Only the launcher port carries remote traffic. Unreal connects to the launcher over loopback.

In a second host terminal, confirm that the campaign reports `state: hosting`. Issue one invitation per guest.

```powershell
& $sfPython @sfCli status --campaign expedition
& $sfPython @sfCli invite --campaign expedition --name 'Guest' --character 'Scout'
```

Share the returned `invite_file` privately. It carries the guest credential and the certificate used to authenticate the host. Use `invite --address <reachable-host-address>` when the guest uses a different address from the bind address. The invitation's endpoint uses the port from the running host.

On the guest PC, use the invitation in a dedicated terminal. The guest profile name is local. The invitation holds the default character.

```powershell
$sfInvite = 'C:\Path\To\Received.invite.json'
& $sfPython @sfCli join --profile scout --invite $sfInvite
```

To select or create another character owned by the same guest, use the catalog before launching the guest game. Pass the chosen character ID with `join --character <id>`.

```powershell
& $sfPython @sfCli characters --invite $sfInvite
& $sfPython @sfCli create-character --invite $sfInvite --name 'Pathfinder'
```

Check guest status from another terminal. A ready gameplay session reports `state: connected`, `link_connected: true`, and `game.state: connected` on the guest. In windowed play, use the ordinary game controls to move. Use `--headless` on both launch commands for unattended diagnostics.

## Rejoin and resume

Leave the host and guest launchers running during a brief network interruption. The guest can retry automatically. The host reserves the character for 90 seconds. The reserved pawn still counts for party travel and other active-player checks. Rejoining within that window can restore control of the same pawn. After the reservation expires, the host attempts a final native checkpoint where supported, retires the old connection, and restores the character near the host on a fresh join.

Reuse the invitation and character selection for a later session. Keep the host campaign's `save` and `game-user` directories together. The native campaign uses both for its checkpoint and stock save generations. Restart the host with the same campaign name and native flags. Its saved zone is selected on startup.

The invitation fingerprint covers the game, the staged Lua scripts, the co-op launcher code, and the native add-on when enabled. After both PCs update to the same mod revision, the host can run `action --campaign expedition refresh-invite --target GUEST_ID` to refresh the existing invitation. The guest and selected character IDs stay the same.

Use the guest's `stop` action to close that guest instance. Use the host's `checkpoint` and `stop` actions for a saved shutdown.

## Optional stock campaign mode

Use the [native build instructions](../native/README.md) for a matched UE4SS runtime and C++ add-on. Stage both DLLs on each PC with `--cpp-runtime` and `--cpp-addon` in a separate workspace. This example starts an authored-map session on the matched stage.

```powershell
$sfCrossRoads = '/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P'
& $sfPython @sfCli host --campaign crossings --bind 192.168.1.25 --port 7777 --native-events --native-guest --native-zones --native-zone-walls --start-zone $sfCrossRoads
```

Use the same invitation workflow and the campaign name `crossings`. The invitation carries the host's native travel, wall, and supported interaction settings. Keep the native feature flags for subsequent launches and omit the initial `--start-zone` when resuming after travel.

Invitations from a current matched native stage also enable the guest's stock inventory, primary, and reload controls. For an older invitation, refresh it from the updated host or use the explicit guest flags below.

| Option | Scope |
| --- | --- |
| `--native-start-preset wanderer` | Starts a fresh stock Wanderer campaign at the Observatory using Mercy rules. Add `--native-world-seed N` for a fixed initial seed from 1 to 999999. Omit `--start-zone` and the extraction switches when creating this preset. Omit the preset and seed when resuming. |
| `--native-expanded-zones` | Opts into the wider catalog of exact authored routes. Use it with `--native-zones` on a current matched stage. Supported routes still enforce host authority and the 15-meter party check. |
| `--max-guests N` | Accepts one to four guests on the native guest and zone scopes. The default is one guest. Loopback testing covered four guests at once. |
| `--native-guest-regen` | Enables the qualified stock passive regeneration path for a detached guest save. Use on a current matched native stage. |
| `--native-guest-mre` | Enables stock MRE use for guests. Select an owned MRE in the inventory overlay and press Home twice, or use `native-guest-mre --target GUID` from the guest profile. |
| `--native-guest-held-fire` | Enables experimental hold and release for the standard SMG on a current matched native stage. The invitation enables the matching guest control. |
| `--native-dead-bridge-enemy-loot` | Automatically shares the qualified Dead Bridge State Infantry CQC's stock shotgun-shell roll. Near the drop, use `native-enemy-loot-claim --target GUID` with the GUID shown in `gameplay.native_enemy_loot`. |
| `--native-extraction-keep N` | Starts a separate fresh authored native campaign with stock extraction mode and `N` retained inventory rows. Add `--native-extraction-backpacks` and optionally `--native-extraction-lose-slot`. The host save retains those settings, so omit these switches when resuming. |
| `--native-shop-view` | Enables the guest's purchase overlay at configured finite merchants. Use the stock interact key to open it, Up or Down to choose, Enter to review and confirm, and Backspace to close. |
| Guest `--native-guest-inventory-view` | Enables the inventory overlay explicitly. The stock inventory key opens the first page. Page Up and Page Down move the selection. Home twice equips supported gear or uses one clean Bandage. End cancels a confirmation or closes the overlay. Reopen it to refresh the inventory. |
| Guest `--native-guest-primary-input --native-guest-reload-input` | Enables qualified stock firearm and reload controls explicitly. The default stock primary path handles one shot per press. |

In the supported stock wipe picker, Page Up and Page Down move the selection, Space toggles an entire row, and Home twice submits the selection. An empty selection is allowed. Backpack recovery uses the local action shown below.

The current stage has qualified stock paths for selected rest points, exits, transport items, an authored CrossRoads loot crate, finite merchants, Copper Stop at Borough, and specific Broker objectives. The quest catalog can show authored templates beyond the objectives supported for co-op turn-in. General inventory editing, item and weapon coverage, NPC dialogue and quest completion, enemy loot, and map compatibility remain experimental. Steam Invite and live validation across separate PCs remain pending in the current test record. Use `--fixtures` only in a dedicated playtest campaign to enable synthetic loot, damage, and actions. [Verified scenarios and open coverage](NATIVE-INTEGRATION.md)

At the supported CrossRoads crate, interact to open it. The shared loot panel appears while a player stands within reach of the opened crate. Use Page Up or Page Down to select an item, Home twice to take it, and End to cancel or close. At a supported merchant, use Up and Down to select an offer and Page Up or Page Down to browse stock. Enter confirms and Backspace cancels or closes the shop.

The Scientist's cash counter offers the stock camera through the same guest shop overlay. Buying one adds a separate camera to the guest's inventory. The inventory overlay can equip the camera.

At ShopKeep, use Interact at the reception and choose the cash-shop response to open the finite stock overlay.

At Borough, ask Mama about trade. Near her trade post, a guest can run `native-borough-mama-trade --target COPPER_WIRE_GUID` to exchange one owned Copper Wire for an exact stock stack of 25 armor-piercing rounds. The trade requires that guest's own conversation and Copper Wire.

At Borough's board, use Interact to view your selected contract and press it again to accept or turn in when prompted. The prompt closes when you look away or move out of range.

At the Observatory Castaway, guests can use Interact to open the stock story dialogue. Use Up and Down to select a response, then press Interact to confirm it. Each guest has a separate conversation.

At the Stone Maze station, face the station and press Interact to review the exact blank tape offered from your backpack. Press Interact again to exchange it. Back at the Scientist's board, select Stone Maze Readings and use the same review and confirmation sequence.

## Machine-readable controls

Run the commands below on the PC with the targeted launcher. Use `--campaign` for the host and `--profile` for the guest.

```powershell
& $sfPython @sfCli status --campaign expedition
& $sfPython @sfCli status --profile scout
& $sfPython @sfCli events --campaign expedition --tail 20
& $sfPython @sfCli action --campaign expedition checkpoint
& $sfPython @sfCli action --profile scout drop-link
& $sfPython @sfCli action --profile scout reconnect
& $sfPython @sfCli action --profile scout stop
& $sfPython @sfCli action --campaign expedition stop
```

- `status` reports the launcher state, `link_connected`, and an Unreal snapshot under `game`. Check `active`, `stale`, and both connection layers before acting. On the guest, check `gameplay_fresh` before treating the host's replicated party state as current. `game.pawns` includes positions and health where measurable. A `null` health value indicates an unavailable or shared stock field.
- `game.native_downed_markers` reports the visible markers in the current zone epoch, keyed by character ID. `game.player_local_input` reports the exact local pawn's verified input mappings and stock inventory state.
- `game.native_zone_regroup` on the host identifies the nearest blocked exit and lists each out-of-range character's role, name, and distance in meters.
- `events` returns an object with an `events` array. Use `generation` to distinguish launcher runs, `time_unix` for event time, and `command_id` where provided to match a result to its action.
- `action` replies include an `id` and `ok` when the controller replies. Failed commands also return an error and a nonzero exit status. A missing response may mean the action was already applied. For receipt-backed transactions, retry with the same 32-character lowercase hex `--operation-id` and identical arguments. Inspect the current state before retrying other mutations.
- `drop-link` suspends automatic retries until the guest requests `reconnect`. Use it to test the 90-second reservation. Actions remain scoped to the local launcher through its workspace.

For a bounded movement test, use `action --profile scout walk --x 0 --y 1 --milliseconds 300`, then read the guest and host pawn positions again.

The authored campaign supports local reads for the current native scope. Use the quoted item IDs and the live position for any subsequent interaction.

```powershell
& $sfPython @sfCli action --profile scout native-inventory-list
& $sfPython @sfCli action --profile scout native-quest-list --target stock
& $sfPython @sfCli action --profile scout native-bounty-board-list
& $sfPython @sfCli action --profile scout native-wire-quest-preflight
& $sfPython @sfCli action --profile scout native-finite-shop-list
& $sfPython @sfCli action --profile scout native-loot-inspect
```

Inventory and quest listings expose `next_offset`. To page the guest inventory, pass the offset in `--target`. The host version of `native-inventory-list` uses `--target` for the enrolled role and `--amount` for the offset. For stock quests, use a target such as `stock@8`. For a confirmed merchant quote, use `native-finite-shop-buy --target GUID@PRICE` or `native-finite-shop-sell --target GUID@PRICE`. Use `native-finite-shop-sale-quote --target GUID` before confirming a sale. During a qualified stock wipe, use `native-wipe-items` and `keep --keep GUID=1` for whole-row selection. The owner's `gameplay.native_backpacks` lists exact recovery IDs and death positions for `native-wipe-recover --target BACKPACK_ID`.

At Borough's Copper Stop board, `native-wire-quest-preflight` reports the current step and checks for the interacting player's or host's exact Copper Wire. Use `native-quest-start --target Clt_Wire` and `native-quest-turnin --target Clt_Wire` within the board's stock interaction range. Eligible guests who are offline receive their queued personal rewards when they return to the campaign.

Within reach of the Borough or Scientist board, `native-bounty-board-list` shows the actual offers the host qualifies for. Pass `next_offset` as `--target` to read the next page. The `co_op` field identifies the supported contracts.

Within reach of the Observatory Castaway, use `native-dialogue-open --target castaway`. The response returns the stock row, choices, and a confirmation token. Use `native-dialogue-choose --target TOKEN@ORDINAL` with an ordinal from that response. Read the next response for its current token and available choices.

Use `native-dialogue-open --target mosby` at the Scientist to explore Mosby's stock topics. Once the party has asked about anomalies and drill data, his dialogue can issue Stone Maze Readings for the host campaign. Each guest keeps their own conversation and the party shares the unlocked topics.

Collect the Scientist's authored blank data tape and take it to the Stone Maze download station. Use `native-mosby-tape-preflight --target BLANK_GUID` to inspect the stock offer, then `native-mosby-tape-convert --target BLANK_GUID` to exchange it for the exact filled tape. At the Scientist board, `native-mosby-readings-preflight` confirms the owned tape and `native-quest-turnin --target Clt_Tape_StoneMaze_01` redeems it.

For Pump Room, take a blank data tape to the Utility Tunnels download station. Use Interact twice to review and confirm the conversion. Black Market offers `Clt_EatherPumpTape` at 8 host XP. The guest who owns the filled tape can redeem it there through stock Interact.

At Borough's board, use Up and Down to select Copper Stop, Find a Computer Board, or Dead Bridge Turret Photos. Interact checks the selected contract. Press it again to accept or redeem when the panel prompts you. At the Scientist's board, the same controls redeem Find a Computer Board for the player carrying it.

The Dead Bridge photography contract unlocks on Borough's board at 4 host XP. Equip a stock camera and center a turret before pressing primary input. Either player can take either photo. For scripted actions, use `native-turret-photo-capture --target turret-13` or `--target turret-15` while framing the matching turret. The host verifies the shot in the game and records each turret once. Return to Borough and use `native-quest-turnin --target Photo_DB_Turrets` for the shared cash and personal XP.

The raw run directories are `campaigns/<campaign>/run` and `clients/<profile>/run` under the chosen workspace. Keep their credentials, invitation files, saves, and generated stage outside commits. The CLI emits telemetry without requiring tools to read the invitation contents.
