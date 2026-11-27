# Playing Project Silverfish Co-op

Host via Steam Networking or Direct IP on Windows. The installed mod opens co-op from the game’s title screen. The development CLI can also start a two-player session on the stock `PlaytestMap_01`. The matched C++ stage adds supported stock campaign areas, interactions, and separate native character saves. The largest loopback party joined with one host and four guests. [Current native coverage](NATIVE-INTEGRATION.md)

## Install and open the Windows trial

Close the game on both PCs. Download the same `Silverfish-Coop-RC-*.zip` for both players. In Steam, choose **Manage → Browse local files** and extract the co-op ZIP into the folder containing `SilverFish.exe`. Use the same ZIP for a first install or an update. Merge its `SilverFish` folder and replace files if asked. The ZIP includes the pinned UE4SS loader and replaces its settings INI during an update. Start Project Silverfish through Steam and choose **Multiplayer**. [Release candidates by publication date](RELEASES.md) · [Current Steam installation](../bootstrap/README.md#current-steam-installation)

Trial setup defaults to listening on all IPv4 interfaces on UDP port 7777. The host can select an interface address and port in **Host** settings. For LAN play, use the host PC's LAN address and allow inbound UDP on the chosen port. Across networks, use an address the guest can reach on that port. Use `127.0.0.1` when both players run on the same PC.

## Stage the development build

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

Use a new workspace for each fresh stage. To load the game's signatures, extract `zCustomGameConfigs.zip` from the same UE4SS release and pass `--signatures` with its `Project Silverfish\UE4SS_Signatures` directory. Use the same game build and mod revision on every PC. On one Windows volume, staging can reuse a private stock cache through `--stock-cache`. The cache contains `SilverFish-Windows.ucas` at its root and can contain the stock radio tracks and TV clips under their original `SilverFish/Content` paths. Staging checks the cached files against the selected game build before creating hard links. Co-op profiles live under the workspace, and the original Steam launch continues to use the single-player save directory.

Set `$sfPython` and `$sfCli` again when opening a new terminal. The workspace path is local to each PC. On one PC both terminals can use the same stage with separate campaign and guest profiles.

## Host from the co-op title

Choose **Multiplayer** on the first screen, then **Host Game**. **New Game** opens the stock character creator. Enter an optional save name to make the campaign easier to find later, or leave it blank to identify it by location and date. **Load Game** lists your campaigns and their last-saved times. The selected save restores its character and world. Use **Network** to select the connection method. The Direct IP form starts with the configured UDP port and listening address. Select **Require password** to enable the password field for either method. **Friendly fire** is off by default. Check it when starting or loading a campaign to allow damage attributed to another player for that hosting session. Self-inflicted damage follows the normal game rules. Up to four guests can join.

Host container pickups take effect immediately. The live campaign writes a checkpoint at normal save boundaries such as resting, Save and Quit, or a level transition. A brief connection loss reserves the guest’s character in memory. If the host game closes unexpectedly before a save, the campaign resumes at the last saved checkpoint.

Inventory drops fall from the game's stock release point. Either player can pick the item up after it lands.

### Manage multiplayer saves

Open **Multiplayer → Host Game → Manage Saves** on the host PC. Select a campaign to open its details.

- **Manage Characters** lists the guest characters for that campaign. Confirm **Delete Character** to erase the selected guest's progress and remove that character from their Join list. Manage Characters refreshes and stays open.
- Steam character rows show the recorded creator’s Steam name and profile picture when Steam can supply the image. A returning guest’s existing characters gain that attribution after a successful Steam connection and the next campaign save. Character names already stored in the campaign keep their saved values.
- Confirm **Delete Save** to erase the campaign and its characters. The host character belongs to the campaign and is deleted with it.
- Both confirmations start on **Cancel**. Confirmed deletions are permanent. The mod keeps a deletion record to prevent older Steam Cloud backups from bringing the data back. Singleplayer slots are stored separately.

## Join from the co-op title

Choose **Multiplayer** and **Join by Direct IP**. Enter the host's numeric IP and UDP port. Select **Use password** if the host gave you one, then choose **Connect**. Select a saved character or choose **Create Character**. Confirm the selected character to join.

The game asks you to confirm trust on a first connection or when a known host’s identity changes. Once accepted, the connection remains encrypted. The default resident co-op workspace is `%LOCALAPPDATA%\SilverfishCoop\Resident`. Each loaded host campaign has its own character list.

### Steam relay trial

1. Install the same Steam-enabled co-op ZIP on both PCs and launch the game through Steam on accounts that can run Silverfish concurrently.
2. On the host, choose **Steam Networking** in the campaign’s **Network** settings. **Invite Only** requires a Steam invite. **Friends Only** also lets the host’s friends use **Join Game** in Steam. **Public** adds the lobby to **Public Steam Lobbies** on the Multiplayer menu and also supports **Join Game** for friends. [Steam lobby types](https://partner.steamgames.com/doc/api/ISteamMatchmaking#ELobbyType)
3. Once the campaign is ready, open the pause menu and select **Invite Steam Friends** to open the Steam overlay. If the overlay is disabled, friends can still use **Join Game** for Friends Only and Public lobbies. Enable the overlay to send invites.
4. On the guest, accept the invite, use **Join Game**, or select a lobby from the public list. Then select **Connect**, enter the host password if set, and select or create a campaign character.

The public list shows the host’s mod version. Incompatible or unknown builds are identified before the character picker opens. While a Public lobby is connecting, **Back** cancels that join and returns to Multiplayer. From the lobby's connection form, **Back** refreshes the previous lobby page. Both Steam and Direct IP require the same packaged release and compatible game files and protocol.

Steam relay carries the connection without a publicly reachable UDP port. The host remains the authority for the same campaign and character roster. The lobby ends when the host closes the campaign. Direct IP remains available through the title menu.

The group confirms Steam joining and Public lobby discovery in its playtests. Automated tests also cover the lobby modes, Steam's Join Game callback, and skipping the password prompt for an open lobby. Verify the updated password flow and rejoining after a guest crash in the next two-PC Steam playtest. If Steam is unavailable, check the `steam-coop-status.json` file in the current game bridge for the initialization result.

### Network timing

The network worker targets a 20 Hz heartbeat on active Steam and Direct IP connections. It also checks host world changes every 50 ms. Requests and action cues can still send as soon as they are ready. Unreal applies gameplay changes on its game thread, so low frame rates can still delay when an update appears in the world.

For a Steam bandwidth playtest, enable the report on the PCs you want to measure. Before hosting or joining, run this in PowerShell.

```powershell
$sfCoopWorkspace = Join-Path $env:LOCALAPPDATA 'SilverfishCoop\Resident'
New-Item -ItemType Directory -Path $sfCoopWorkspace -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $sfCoopWorkspace 'capture-steam-network') -Force | Out-Null
```

After leaving the session normally, attach `steam-network-report.json` from that folder to the issue and mention the map where you noticed the spike. Keep a copy before the next measured session replaces it. Remove `capture-steam-network` to stop collecting reports. The report counts this mod's Steam messages, including its tunnel headers. It gives average and peak rates by map and lobby size and includes sampled heartbeat RTT when available. Steam relay overhead and other apps' traffic contribute separately to the Windows adapter total. The report contains no player identities, lobby IDs, addresses, or packet contents.

When the game publishes matching link counters, `link_activity` shows queued and delivered Unreal payload sizes, encoded snapshot sizes, and relay queue drops for the same group. The separate `steam_activity` counts sends the Steam SDK rejected, full receive batches, and times the receive loop reached its drain limit. Both appear alongside peak send and receive intervals. Use the Steam message totals for accepted send and receive bytes. Intervals crossing a map, visit, or party-size change appear in the unassigned group.

### Steam Cloud backups

The installed mod mirrors each hosted campaign as a complete backup under `%LOCALAPPDATA%\SilverFish\Saved\SaveGames\SilverfishCoopCloud`. The backup includes the world, host and guest characters, and the campaign’s private host identity so returning guests can use their existing characters. Keep the backup files within the host’s Steam account. Steam Auto-Cloud uploads these files on exit and downloads them on launch when Steam Cloud is enabled for Project Silverfish. The mod reads downloaded backups when you open the Host campaign picker. The same Steam account and a compatible mod build are required on the other PC.

Use **Multiplayer → Host Game → Load Game** on the other PC. The mod restores missing campaigns and newer revisions. An equal or older revision leaves the local campaign in place. Live multiplayer saves keep their own storage and Singleplayer continues to use the original save slots. Guests keep their progress with the host’s campaign.

### Reset co-op campaigns

Follow these steps on the host PC when a release explicitly requires a fresh campaign. Moving these folders out of AppData clears the host's co-op campaign list. The old campaigns and their characters remain in the folders you moved.

1. Close Project Silverfish. If Steam Cloud is enabled for the game, wait for the current sync to finish and turn it off in the game's Steam properties before moving the folders.
2. In File Explorer, open `%LOCALAPPDATA%\SilverfishCoop\Resident`. Move the `campaigns` folder to a backup location outside AppData. If you configured a different co-op workspace, use that workspace's `campaigns` folder.
3. If present, move only `%LOCALAPPDATA%\SilverFish\Saved\SaveGames\SilverfishCoopCloud` to the backup location. Leave the parent `SaveGames` folder and its singleplayer saves in place. Keep Steam Cloud off while you start fresh if your Steam account still has backups from incompatible builds, since those could be downloaded and imported again.
4. Launch the updated game and choose **Multiplayer → Host → New Game**. Guests can create characters when they join the new campaign.

## Host and join from the development CLI

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

If a guest unexpectedly reaches the title while the host keeps playing, the mod tries to reconnect that guest to the same character. If the host reaches the title and cleanup succeeds, the co-op menu offers **Host Game** again. Choose **Load Game** to reopen the last saved campaign, then have guests rejoin with the same characters. If the host game fully closes, relaunch it first. Changes after the last save may be lost.

Leave both games running during a brief network interruption. The guest can retry automatically. The host reserves the character for 90 seconds. The reserved pawn still counts for party travel and other active-player checks. Reconnecting within that window restores control of the same pawn if Unreal still has the connection.

If the guest game closes unexpectedly, reopen co-op and select the same character. Joining can take about two minutes while Unreal clears the old connection. The replacement joins close to the host and can resume from the available character state. Local automation reports `state: waiting_for_host` and `wait_reason: native_runtime_retiring` during that wait.

In the resident trial, use **Load Game** to reopen the same save and have the guest select the same character. The host resumes in its saved zone. For development CLI sessions, reuse the invitation and native flags, and keep the campaign's `save` directory together with its stock save generations.

The invitation fingerprint covers the game, the staged Lua scripts, the co-op launcher code, and the native add-on when enabled. After both PCs update to the same mod revision, the host can run `action --campaign expedition refresh-invite --target GUEST_ID` to refresh the existing invitation. The guest and selected character IDs stay the same.

Use the guest's `stop` action to close that guest instance. Use the host's `checkpoint` and `stop` actions for a saved shutdown.

## Leave through the stock Pause menu

In a connected co-op guest session, use **Quit to Menu** and confirm. The host saves that character before the guest returns to a fresh co-op title. The host keeps playing.

To end the shared session as host, use **Save and Quit** when the stock game enables it. If that button is disabled, choose **Quit to Menu** and confirm. Either action saves the host and connected guest characters before returning everyone to the co-op title.

In sessions opened from the in-game co-op title, automation can use `action --profile NAME leave-session` for an acknowledged guest save and departure, or `action --campaign NAME leave-session` to save and end the host's session. A successful response reports the checkpoint revision. Watch the role's status for `state: stopped` and a `stop_reason` of `guest_left` or `host_session_ended`.

## Content compatibility

Install matching content mods on every machine. Co-op discovers compatible content from the loaded game, including when it lives in another mounted package. Content authors can use these built-in paths without editing the co-op mod.

- **Quests.** Use the stock quest row and active journal. A compatible bounty board can supply a complete live offer under a unique quest ID. Stock progress flags, ordinary backpack deposits, and stock cash, XP, and item rewards use the shared contract path. A follow-up that needs to start must currently be available to the stock quest loader.
- **Items and interactables.** Derive from a supported stock Blueprint and keep its stock interaction functions. Co-op uses the exact cooked class for items and the loaded actor for placed interactables. A Blueprint that adds a new gameplay effect can use a shared adapter for that effect across all assets that use it.
- **Maps and exits.** Use the stock player and save classes and provide a working spawn or arrival marker. Stock overlap exits and transition doors supply the destination World package and entrance tag. Every player needs the destination in their local Asset Registry.

For a compatibility report, include the content mod and version, the map or quest ID, and the first step where the host and guest diverged. Use `--fixtures` only in a dedicated playtest campaign. [Compatibility and verified scenarios](KNOWN-ISSUES.md#compatibility-with-game-updates-and-content-mods)

## Optional stock campaign mode

Use the [native build instructions](../native/README.md) for a matched UE4SS runtime and C++ add-on. Stage both DLLs on each PC with `--cpp-runtime` and `--cpp-addon` in a separate workspace. This example starts an authored-map session on the matched stage.

```powershell
$sfCrossRoads = '/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P'
& $sfPython @sfCli host --campaign crossings --bind 192.168.1.25 --port 7777 --native-events --native-guest --native-zones --native-zone-walls --start-zone $sfCrossRoads
```

Use the same invitation workflow and the campaign name `crossings`. The invitation carries the host's native travel, wall, and supported interaction settings. Keep the native feature flags for subsequent launches and omit the initial `--start-zone` when resuming after travel.

On a matched C++ stage, `menu` opens a staged copy of the stock title. On the host, run `& $sfPython @sfCli menu --campaign expedition --port 7777` and choose **Multiplayer** and **Host**. The host can also supply `--bind` for a LAN session. On a guest PC, run `& $sfPython @sfCli menu --profile scout --invite $sfInvite` and choose **Multiplayer** and **Join**. The selected campaign or guest profile comes from the command line in this development path. [In-game integration](IN-GAME-INTEGRATION.md)

Invitations from a current matched native stage also enable the guest's stock inventory, primary, and reload controls. For an older invitation, refresh it from the updated host or use the explicit guest flags below.

| Option | Scope |
| --- | --- |
| `--native-start-preset wanderer` | Starts a fresh stock Wanderer campaign at the Observatory using Mercy rules. Add `--native-world-seed N` for a fixed initial seed from 1 to 999999. Omit `--start-zone` and the extraction switches when creating this preset. Omit the preset and seed when resuming. |
| `--native-difficulty Respawn_WtPenalty` or `--native-difficulty Decimation` | Selects stock extraction rules when creating a Wanderer campaign. Both enable owner-only backpack drops. `Respawn_WtPenalty` allows five retained items and enables stock loot replenishment. `Decimation` sets the retention limit to zero and disables replenishment. The campaign restores the chosen rules on restart. |
| `--native-expanded-zones` | Opts into the wider catalog of exact authored routes. Use it with `--native-zones` on a current matched stage. Supported routes still enforce host authority and the 15-meter party check. |
| `--max-guests N` | Accepts one to four guests on the native guest and zone scopes. The default is one guest. Loopback testing covered four guests at once. |
| `--native-guest-regen` | Enables the qualified stock passive regeneration path for a detached guest save. Use on a current matched native stage. |
| `--native-guest-mre` | Enables guest use of stock MREs, Canned Peaches, Canned Beans, and Brown Mushroom. Use the item from the game's Backpack context menu. Local guest actions `native-guest-mre`, `native-guest-canned-peaches`, `native-guest-canned-beans`, and `native-guest-brown-mushroom` accept the backpack item's `--target GUID`. |
| `--native-guest-held-fire` | Enables experimental hold and release for the standard SMG on a current matched native stage. The invitation enables the matching guest control. |
| `--native-authored-enemy-loot` | Enables shared stock shotgun-shell loot for the qualified CQC encounters. Look at the shell stack and press Interact. Automation can inspect nearby item actors in `game.native_world_loot_items` and use `native-enemy-loot-claim --target GUID` with the GUID shown in `gameplay.native_enemy_loot_rolls`. |
| `--native-extraction-keep N` | Starts a separate fresh authored native campaign with stock extraction mode and `N` retained inventory rows. Add `--native-extraction-backpacks` and optionally `--native-extraction-lose-slot`. The host save retains those settings, so omit these switches when resuming. |
| `--native-shop-view` | Enables the stock trade screen for guests at qualified cash merchants. On a staged native build, join with `--native-guest-inventory-view` to give the guest screen its isolated backpack. |
| Guest `--native-guest-inventory-view` | Enables the stock backpack on a matched native stage. Use its normal controls to equip, use, split, merge, or drop items. |
| Guest `--native-guest-primary-input --native-guest-reload-input` | Enables qualified stock primary and reload controls explicitly. The standard firearm path handles one shot per press. Dead Bridge also has a scoped Old Pipe equip and primary path. |

In the supported stock wipe picker, Page Up and Page Down move the selection, Space toggles an entire row, and Home twice submits the selection. An empty selection is allowed. Backpack recovery uses the local action shown below.

At a qualified stock container, press Interact to open it. Look at an item inside and press Interact to take it. The same item disappears for everyone when the host commits the pickup. Supported player drops appear on the ground and use the same Interact control. Nearby physical pickups appear in `game.native_world_loot_items`. When the host visits a container from an older co-op save, the mod restores its item positions from the container's stock spawn points and keeps the original roll.

When the host enables stock loot replenishment, the game can expire temporary container records during its daily update. A container can refill after the party returns to the level. The mod clears the previous roll and captures one new shared roll the next time someone opens it. Permanent containers keep their saved state.

Use Interact at the existing rest-site box to open your private stash. As a guest, select an item under **Stash** to withdraw it or under **Backpack** to deposit it. The item moves as a whole stack. The host's combined-stash setting determines whether your own items follow you between rest sites.

At a supported cash merchant, use Interact to open the stock shop. Buy offers or sell backpack items through the stock controls. The displayed price covers the whole item or stack. Items sold to the merchant appear under Buy Back.

At the Observatory workbench, equip your Hazard Suit and bring a Computer Board. Use Interact to review the supported recipe and Interact again to exchange the Board for one Electron Scrap.

The Scientist's cash counter offers the stock camera through the guest's stock shop screen. Buying one adds a separate camera to the guest's inventory. Equip it from the stock backpack, then select its quick slot.

At ShopKeep, use Interact at the reception and choose the cash-shop response to open the stock shop screen.

At Borough, ask Mama about trade. Near her trade post, a guest can run `native-borough-mama-trade --target COPPER_WIRE_GUID` to exchange one owned Copper Wire for an exact stock stack of 25 armor-piercing rounds. The trade requires that guest's own conversation and Copper Wire.

At Borough's board, use Interact to open the stock bounty board. Choose a contract and use the stock Accept or Turn In button.

At the Observatory Castaway, guests can use Interact to open the stock story dialogue. Use Up and Down to select a response, then press Interact to confirm it. Each guest has a separate conversation.

At the Stone Maze station, face the station and press Interact to review the exact blank tape offered from your backpack. Press Interact again to exchange it. Back at the Scientist's board, select Stone Maze Readings and use the same review and confirmation sequence.

## Machine-readable controls

Use the title profile to read available actions and the current `handoff_generation`. The installed stock entry uses the `bootstrap` profile. Start or cancel against that exact generation.

```powershell
& $sfPython @sfCli status --title-profile bootstrap
& $sfPython @sfCli title-select --title-profile bootstrap --role host --handoff-generation $sfTitleGeneration
& $sfPython @sfCli title-cancel --title-profile bootstrap --handoff-generation $sfTitleGeneration
& $sfPython @sfCli events --title-profile bootstrap --tail 20
```

A local selection uses the configured Host or Join settings. The acceptance response identifies the attempt. Track the same attempt through `ready` or `stopped`, then read the selected campaign or guest profile for gameplay. A starting attempt also publishes its generation under `attempt.generation` for cancellation. Private invitation data stays out of title status and events.

The native guest backpack also supports `native-inventory-sort` with an `--operation-id`. Look for `gameplay.native_inventory_sort_capability` equal to 1 on the connected guest. The action returns the verified row count and either `sorted` or `already_sorted`. Request a new `native-inventory-list` page to read the resulting order.

The shared crate roll appears under `gameplay.native_loot_rolls` for the current map. Use the selected row's exact `guid` and crate key as `GUID@CRATE` with `native-loot-claim --target`. Supply an `--operation-id` of 32 lowercase hexadecimal characters and reuse it for the same action on retry. A stock cash claim returns `native_loot_claim.wallet_credit` with `amount`, `before`, and `after`. Read `gameplay.shared_wallet` for the current balance, including later changes.

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
- `game.native_loose_pickups` reports nearby level items with their stock class, location, and pickup target. Use Interact or pass the reported target to `native-loose-pickup-claim --target`. The host settles the transfer in memory. It reaches disk at the next campaign save.
- `gameplay.native_stock_enemy_loot` lists nearby shared enemy drops by item GUID. `game.native_world_loot_items` shows the physical actors present in the local world. Use the stock Interact action or `native-stock-enemy-loot-claim --target GUID`. The host checks reach and sight and returns the same receipt if the original operation ID is replayed.
- `game.native_downed_markers` reports the visible markers in the current zone epoch, keyed by character ID. `game.player_local_input` reports the exact local pawn's verified input mappings and stock inventory state.
- `game.native_zone_regroup` on the host identifies the nearest blocked exit and lists each out-of-range character's role, name, and distance in meters.
- `events` returns an object with an `events` array. Use `generation` to distinguish launcher runs, `time_unix` for event time, and `command_id` where provided to match a result to its action.
- `routes --campaign expedition` or `routes --profile scout` lists the current map's cooked exits by exact actor, entrance, destination, and arrival marker. The response includes the current `zone_epoch` and leaves the route list empty if the session view is stale or disconnected. Check the expanded-zones host setting, the stock door's requirements, and the live party-distance report before crossing.
- `action` replies include an `id` and `ok` when the controller replies. Failed commands also return an error and a nonzero exit status. A missing response may mean the action was already applied. For receipt-backed transactions, retry with the same 32-character lowercase hex `--operation-id` and identical arguments. Inspect the current state before retrying other mutations.
- `drop-link` suspends automatic retries until the guest requests `reconnect`. Use it to test the 90-second reservation. Actions remain scoped to the local launcher through its workspace.

For a bounded movement test, use `action --profile scout walk --x 0 --y 1 --milliseconds 300`, then read the guest and host pawn positions again.
Add `--continuous` to supply stock movement input on each available game frame. The command uses the same world direction and duration bounds.

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

For a private rest-site stash, read the stock box IDs from the host's `game.native_stash_sites`. While within reach of the box, use `native-stash-list --target STASH_ID` to list the requesting character's items. To page the list, pass `--target "STASH_ID|OFFSET"`. Use `native-stash-move --target "STASH_ID|deposit|ITEM_GUID"` or `native-stash-move --target "STASH_ID|withdraw|ITEM_GUID"` to move the entire stock row. Supply an `--operation-id` to safely retry the same transfer after an interrupted connection. The host's combined-stash setting applies to each character separately.

Shared drops cover the campaign maps supported by native co-op. The host can Ctrl-click a shareable item in the stock backpack or choose **Drop** from its context menu. The backpack refreshes after the drop is committed. A nearby party member can look at the drop and press Interact to collect it. The mod reads drop eligibility from the loaded item class and inventory row. A Blueprint from a content mod can use this path when it inherits the supported stock item behavior. Install the same content mods on every seat. A drop keeps the full stack or the weapon's loaded magazine, along with the item's original identity.

`gameplay.native_guest_stack_drops` lists shared drops in the current zone. Use `native-host-stack-drop --target 'GUID|COUNT'` on the host or `native-guest-stack-drop --target 'GUID|COUNT'` on a guest, reading the GUID and quantity from the corresponding backpack listing. Use `GUID|1` for the Light Pistol. A nearby guest can collect a drop with `native-guest-stack-reclaim --target DROP_ID`. The host can use `native-host-stack-reclaim --target DROP_ID`. A successful pickup records the recipient as `claimed_by` and preserves the original dropper as `owner`.

At the Observatory workbench, `native-crafting-bench-preflight` returns the guest's eligible `board_guid`. Use `native-crafting-bench --target GUID` to exchange that Board. The receipt's `receive.guid` identifies the resulting stack and `receive.quantity` is the amount added by the exchange.

For a guest ammo split with an explicit count, use `native-inventory-split --target 'GUID|AMOUNT|EXPECTED'`. Read `EXPECTED` from the selected backpack row's current quantity. A successful split returns the original GUID, the new GUID, and both quantities. Reuse the same operation ID to look up that committed result after reconnect. The current split scope covers ordinary 9 mm Ball, .45 Ball, and 12 Gauge Buckshot.

At Borough's Copper Stop board, `native-wire-quest-preflight` reports the current step and checks for the interacting player's or host's exact Copper Wire. Use `native-quest-start --target Clt_Wire` and `native-quest-turnin --target Clt_Wire` within the board's stock interaction range. Eligible guests who are offline receive their queued personal rewards when they return to the campaign.

For Missing Contact, accept the contract at the Black Market board and have the host dig up and collect the sealed package in CrossRoads. To give it to a guest, stand within three meters of them in CrossRoads or Black Market and open the host's Journal. Choose **Give the sealed package**, select the recipient, then choose **Give package**. The recipient redeems it with Interact at the Black Market board.

For scripted handoff, use `native-missing-contact-handoff --target CHARACTER_ID` from the host's local controls. The guest can check custody with `native-missing-contact-preflight` and redeem with `native-quest-turnin --target Note_Farmhouse` at the Black Market board.

Within reach of the Borough or Scientist board, `native-bounty-board-list` shows the actual offers the host qualifies for. Pass `next_offset` as `--target` to read the next page. The `co_op` field identifies the supported contracts.

Within reach of the Observatory Castaway, use `native-dialogue-open --target castaway`. The response returns the stock row, choices, and a confirmation token. Use `native-dialogue-choose --target TOKEN@ORDINAL` with an ordinal from that response. Read the next response for its current token and available choices.

Use `native-dialogue-open --target mosby` at the Scientist to explore Mosby's stock topics. Once the party has asked about anomalies and drill data, his dialogue can issue Stone Maze Readings for the host campaign. Each guest keeps their own conversation and the party shares the unlocked topics.

Collect the Scientist's authored blank data tape and take it to the Stone Maze download station. Use `native-mosby-tape-preflight --target BLANK_GUID` to inspect the stock offer, then `native-mosby-tape-convert --target BLANK_GUID` to exchange it for the exact filled tape. At the Scientist board, `native-mosby-readings-preflight` confirms the owned tape and `native-quest-turnin --target Clt_Tape_StoneMaze_01` redeems it.

For Pump Room, take a blank data tape to the Utility Tunnels download station. Use Interact twice to review and confirm the conversion. Black Market offers `Clt_EatherPumpTape` at 8 host XP. The guest who owns the filled tape can redeem it there through stock Interact.

Black Market offers `Jmr_Lake` at zero host XP. Accept it at the board, travel together to Lake, and destroy the authored jammer with stock weapons. Return to Black Market and redeem the shared contract through Interact.

At Borough's board, use the stock controls to select Copper Stop, Find a Computer Board, or Dead Bridge Turret Photos when offered. Use the Scientist's board to turn in Find a Computer Board as the player carrying it.

The Dead Bridge photography contract unlocks on Borough's board at 4 host XP. Equip a stock camera and center a turret before pressing primary input. Either player can take either photo. For scripted actions, use `native-turret-photo-capture --target turret-13` or `--target turret-15` while framing the matching turret. The host verifies the shot in the game and records each turret once. Return to Borough and use `native-quest-turnin --target Photo_DB_Turrets` for the shared cash and personal XP.

The raw run directories are `campaigns/<campaign>/run` and `clients/<profile>/run` under the chosen workspace. Keep their credentials, invitation files, saves, and generated stage outside commits. The CLI emits telemetry without requiring tools to read the invitation contents.
