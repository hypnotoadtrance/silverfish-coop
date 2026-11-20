# Playing Project Silverfish Co-op

Use Direct IP on Windows. The installed title entry opens the matched private co-op stage. The development CLI can also start a two-player session on the stock `PlaytestMap_01`. The matched C++ stage adds supported stock campaign areas, interactions, and separate native character saves. The largest loopback party joined with one host and four guests. [Current native coverage](NATIVE-INTEGRATION.md)

## Install and open the Windows trial

Close the game on both PCs. Use the same Steam game build and the same trial ZIP, and follow the one-time setup from the extracted ZIP. Setup uses the pinned UE4SS release and Project Silverfish signatures. A fresh workspace needs roughly 5.2 GB for the private game copy. After setup, start Project Silverfish through Steam and press **F7** for **Open Co-op** on the stock title. [Trial ZIP setup](../bootstrap/README.md#install-from-a-trial-zip)

For two PCs on a LAN, enter the host PC's LAN IP and a UDP port in **Host** settings. Allow inbound UDP on that port on the host PC and use that LAN address in the invitation. The default `127.0.0.1` address works for a host and guest running on the same PC.

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

Use a new workspace for each fresh stage. To load the game's signatures, extract `zCustomGameConfigs.zip` from the same UE4SS release and pass `--signatures` with its `Project Silverfish\UE4SS_Signatures` directory. Use the same game build and mod revision on every PC. The launcher copies the stock game files and writes co-op profiles under the workspace. The original Steam launch continues to use the single-player save directory.

Set `$sfPython` and `$sfCli` again when opening a new terminal. The workspace path is local to each PC. On one PC both terminals can use the same stage with separate campaign and guest profiles.

## Host from the co-op title

After the [stock title setup](../bootstrap/README.md#install-the-stock-title-entry), open co-op from the title. In the isolated title, press **F7** to open Host settings. Edit the campaign, UDP port, and numeric listening address. Use **F8** to move between fields, **F7** to start, or **F9** to return to the title panel. A blank field uses its configured default. Up to four guests can join the campaign. The headless trial verified the fields and selected endpoint. Foreground keyboard and focus still need a live check.

## Create an invitation while hosting

Open the stock **Journal** in the active co-op campaign with **J** or your configured key. Press **F7** to open the invite form. Enter the participant's name, their starter character's name, and the address they can reach. Use **F8** to advance through the fields. Leave the address blank to use the configured host address. On the review screen, press **F7** to create the invitation, then press **F7** again to copy it once it is ready. Share the invitation privately. It contains the guest credential. **F9** returns to the Journal. The host can also share the private invitation file from the campaign's `run\invites` directory.

## Join from the co-op title

Press **F8**, paste the host's invitation text into the masked field, and press **F7** to load the host's character list. Use **F8** to cycle through your characters and the option to create one. **F7** selects the current character or opens creation. After selecting a character, leave the local profile blank to use its configured name, or enter a different name. Press **F7** to join. **F9** backs out of the current step.

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

Leave both games running during a brief network interruption. The guest can retry automatically. The host reserves the character for 90 seconds. The reserved pawn still counts for party travel and other active-player checks. Reconnecting within that window restores control of the same pawn if Unreal still has the connection.

If the guest game closes unexpectedly, reopen co-op and select the same character. Join can wait about two minutes while the host saves the old pawn and Unreal clears its connection. The replacement then joins close to the host with its saved inventory. Local automation reports `state: waiting_for_host` and `wait_reason: native_runtime_retiring` during that wait.

Reuse the invitation and character selection for a later session. Keep the host campaign's `save` and `game-user` directories together. The native campaign uses both for its checkpoint and stock save generations. Restart the host with the same campaign name and native flags. Its saved zone is selected on startup.

The invitation fingerprint covers the game, the staged Lua scripts, the co-op launcher code, and the native add-on when enabled. After both PCs update to the same mod revision, the host can run `action --campaign expedition refresh-invite --target GUEST_ID` to refresh the existing invitation. The guest and selected character IDs stay the same.

Use the guest's `stop` action to close that guest instance. Use the host's `checkpoint` and `stop` actions for a saved shutdown.

## Leave through the stock Pause menu

In a connected co-op guest session, use **Quit to Menu** and confirm. The host saves that character before the guest returns to a fresh co-op title. The host keeps playing. To end the shared session, the host can use **Quit to Menu** or **Save and Quit** where the stock game offers it. The host completes the paired save before returning connected players to the co-op title.

In sessions opened from the in-game co-op title, automation can use `action --profile NAME leave-session` for an acknowledged guest save and departure, or `action --campaign NAME leave-session` to save and end the host's session. A successful response reports the checkpoint revision. Watch the role's status for `state: stopped` and a `stop_reason` of `guest_left` or `host_session_ended`.

## Optional stock campaign mode

Use the [native build instructions](../native/README.md) for a matched UE4SS runtime and C++ add-on. Stage both DLLs on each PC with `--cpp-runtime` and `--cpp-addon` in a separate workspace. This example starts an authored-map session on the matched stage.

```powershell
$sfCrossRoads = '/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P'
& $sfPython @sfCli host --campaign crossings --bind 192.168.1.25 --port 7777 --native-events --native-guest --native-zones --native-zone-walls --start-zone $sfCrossRoads
```

Use the same invitation workflow and the campaign name `crossings`. The invitation carries the host's native travel, wall, and supported interaction settings. Keep the native feature flags for subsequent launches and omit the initial `--start-zone` when resuming after travel.

On a matched C++ stage, `menu` opens a staged copy of the stock title with an in-game co-op panel. On the host, run `& $sfPython @sfCli menu --campaign expedition --port 7777` and press F7 to host. The host can also supply `--bind` for a LAN session. On a guest PC, run `& $sfPython @sfCli menu --profile scout --invite $sfInvite` and press F8 to join. F9 hides or restores the panel. The selected campaign or guest profile still comes from the command line in this trial build. [In-game integration](IN-GAME-INTEGRATION.md)

Invitations from a current matched native stage also enable the guest's stock inventory, primary, and reload controls. For an older invitation, refresh it from the updated host or use the explicit guest flags below.

| Option | Scope |
| --- | --- |
| `--native-start-preset wanderer` | Starts a fresh stock Wanderer campaign at the Observatory using Mercy rules. Add `--native-world-seed N` for a fixed initial seed from 1 to 999999. Omit `--start-zone` and the extraction switches when creating this preset. Omit the preset and seed when resuming. |
| `--native-difficulty Respawn_WtPenalty` or `--native-difficulty Decimation` | Selects stock extraction rules when creating a Wanderer campaign. Both enable owner-only backpack drops. `Respawn_WtPenalty` allows five retained items and enables stock loot replenishment. `Decimation` sets the retention limit to zero and disables replenishment. The campaign restores the chosen rules on restart. |
| `--native-expanded-zones` | Opts into the wider catalog of exact authored routes. Use it with `--native-zones` on a current matched stage. Supported routes still enforce host authority and the 15-meter party check. |
| `--max-guests N` | Accepts one to four guests on the native guest and zone scopes. The default is one guest. Loopback testing covered four guests at once. |
| `--native-guest-regen` | Enables the qualified stock passive regeneration path for a detached guest save. Use on a current matched native stage. |
| `--native-guest-mre` | Enables stock MRE and Canned Peaches use for guests. Select an owned item in the inventory overlay and press Home twice. The guest profile also accepts `native-guest-mre --target GUID` and `native-guest-canned-peaches --target GUID`. |
| `--native-guest-held-fire` | Enables experimental hold and release for the standard SMG on a current matched native stage. The invitation enables the matching guest control. |
| `--native-authored-enemy-loot` | Enables shared stock shotgun-shell loot for the qualified CQC encounters. Look at a nearby shell cue and press Interact. For agent control, use `native-enemy-loot-claim --target GUID` with the GUID shown in `gameplay.native_enemy_loot_rolls`. |
| `--native-extraction-keep N` | Starts a separate fresh authored native campaign with stock extraction mode and `N` retained inventory rows. Add `--native-extraction-backpacks` and optionally `--native-extraction-lose-slot`. The host save retains those settings, so omit these switches when resuming. |
| `--native-shop-view` | Enables the guest's purchase overlay at configured finite merchants. Use the stock interact key to open it, Up or Down to choose, Enter to review and confirm, and Backspace to close. |
| Guest `--native-guest-inventory-view` | Enables the inventory overlay explicitly. The stock inventory key opens the first page. Page Up and Page Down move the selection and continue to the next page at the end of the list, or the previous page at the beginning. Press Home to preview a supported equip, use, or ammo split and again to confirm. The overlay splits half the selected ammo stack, rounded down. In Observatory or CrossRoads, select a 9mm Ball stack or a base Light Pistol in the backpack, press Delete to prepare the drop, and press Home to confirm. Any nearby party member can look at the drop and press Interact to collect it. End cancels a confirmation or closes the overlay. Reopen it to refresh the inventory. |
| Guest `--native-guest-primary-input --native-guest-reload-input` | Enables qualified stock primary and reload controls explicitly. The standard firearm path handles one shot per press. Dead Bridge also has a scoped Old Pipe equip and primary path. |

In the supported stock wipe picker, Page Up and Page Down move the selection, Space toggles an entire row, and Home twice submits the selection. An empty selection is allowed. Backpack recovery uses the local action shown below.

The current stage has qualified stock paths for selected rest points, exits, transport items, qualified stock small crates, finite merchants, Copper Stop at Borough, and specific Broker objectives. The quest catalog can show authored templates beyond the objectives supported for co-op turn-in. General inventory editing, item and weapon coverage, NPC dialogue and quest completion, enemy loot, and map compatibility remain experimental. Steam Invite and live validation across separate PCs remain pending in the current test record. Use `--fixtures` only in a dedicated playtest campaign to enable synthetic loot, damage, and actions. [Verified scenarios and open coverage](NATIVE-INTEGRATION.md)

At a qualified stock container, press Interact to open it. The shared loot panel appears while a player stands within reach. Use Page Up or Page Down to select an item, Home twice to take it, and End to cancel or close. At a supported merchant, use Up and Down to select an offer and Page Up or Page Down to browse stock. Enter confirms and Backspace cancels or closes the shop.

At the Observatory workbench, equip your Hazard Suit and bring a Computer Board. Use Interact to review the supported recipe and Interact again to exchange the Board for one Electron Scrap.

The Scientist's cash counter offers the stock camera through the same guest shop overlay. Buying one adds a separate camera to the guest's inventory. The inventory overlay can equip the camera.

At ShopKeep, use Interact at the reception and choose the cash-shop response to open the finite stock overlay.

At Borough, ask Mama about trade. Near her trade post, a guest can run `native-borough-mama-trade --target COPPER_WIRE_GUID` to exchange one owned Copper Wire for an exact stock stack of 25 armor-piercing rounds. The trade requires that guest's own conversation and Copper Wire.

At Borough's board, use Interact to view your selected contract and press it again to accept or turn in when prompted. The prompt closes when you look away or move out of range.

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

In Observatory or CrossRoads, the host can Ctrl-click a 9mm Ball stack or a base Light Pistol in the stock backpack or choose **Drop** from its context menu. The backpack refreshes when the shared drop is committed. A nearby party member can look at the drop and press Interact to collect it. 9mm Ball drops include the full stack. The pistol keeps its loaded magazine.

`gameplay.native_guest_stack_drops` lists shared drops in the current zone. Use `native-host-stack-drop --target 'GUID|COUNT'` on the host or `native-guest-stack-drop --target 'GUID|COUNT'` on a guest, reading the GUID and quantity from the corresponding backpack listing. Use `GUID|1` for the Light Pistol. A nearby guest can collect a drop with `native-guest-stack-reclaim --target DROP_ID`. The host can use `native-host-stack-reclaim --target DROP_ID`. A successful pickup records the recipient as `claimed_by` and preserves the original dropper as `owner`.

At the Observatory workbench, `native-crafting-bench-preflight` returns the guest's eligible `board_guid`. Use `native-crafting-bench --target GUID` to exchange that Board. The receipt's `receive.guid` identifies the resulting stack and `receive.quantity` is the amount added by the exchange.

For a guest ammo split with an explicit count, use `native-inventory-split --target 'GUID|AMOUNT|EXPECTED'`. Read `EXPECTED` from the selected backpack row's current quantity. A successful split returns the original GUID, the new GUID, and both quantities. Reuse the same operation ID to look up that committed result after reconnect. The current split scope covers ordinary 9 mm Ball, .45 Ball, and 12 Gauge Buckshot.

At Borough's Copper Stop board, `native-wire-quest-preflight` reports the current step and checks for the interacting player's or host's exact Copper Wire. Use `native-quest-start --target Clt_Wire` and `native-quest-turnin --target Clt_Wire` within the board's stock interaction range. Eligible guests who are offline receive their queued personal rewards when they return to the campaign.

For Missing Contact, accept the contract at the Black Market board and have the host dig up and collect the sealed package in CrossRoads. To give it to a guest, stand within three meters of them in CrossRoads or Black Market and open the host's Journal. Press F8 to open the recipient chooser and again to cycle through nearby guests. Press F7 to give the package to the displayed guest or F9 to cancel. The recipient redeems it with Interact at the Black Market board.

For scripted handoff, use `native-missing-contact-handoff --target CHARACTER_ID` from the host's local controls. The guest can check custody with `native-missing-contact-preflight` and redeem with `native-quest-turnin --target Note_Farmhouse` at the Black Market board.

Within reach of the Borough or Scientist board, `native-bounty-board-list` shows the actual offers the host qualifies for. Pass `next_offset` as `--target` to read the next page. The `co_op` field identifies the supported contracts.

Within reach of the Observatory Castaway, use `native-dialogue-open --target castaway`. The response returns the stock row, choices, and a confirmation token. Use `native-dialogue-choose --target TOKEN@ORDINAL` with an ordinal from that response. Read the next response for its current token and available choices.

Use `native-dialogue-open --target mosby` at the Scientist to explore Mosby's stock topics. Once the party has asked about anomalies and drill data, his dialogue can issue Stone Maze Readings for the host campaign. Each guest keeps their own conversation and the party shares the unlocked topics.

Collect the Scientist's authored blank data tape and take it to the Stone Maze download station. Use `native-mosby-tape-preflight --target BLANK_GUID` to inspect the stock offer, then `native-mosby-tape-convert --target BLANK_GUID` to exchange it for the exact filled tape. At the Scientist board, `native-mosby-readings-preflight` confirms the owned tape and `native-quest-turnin --target Clt_Tape_StoneMaze_01` redeems it.

For Pump Room, take a blank data tape to the Utility Tunnels download station. Use Interact twice to review and confirm the conversion. Black Market offers `Clt_EatherPumpTape` at 8 host XP. The guest who owns the filled tape can redeem it there through stock Interact.

Black Market offers `Jmr_Lake` at zero host XP. Accept it at the board, travel together to Lake, and destroy the authored jammer with stock weapons. Return to Black Market and redeem the shared contract through Interact.

At Borough's board, use Up and Down to select Copper Stop, Find a Computer Board, or Dead Bridge Turret Photos. Interact checks the selected contract. Press it again to accept or redeem when the panel prompts you. At the Scientist's board, the same controls redeem Find a Computer Board for the player carrying it.

The Dead Bridge photography contract unlocks on Borough's board at 4 host XP. Equip a stock camera and center a turret before pressing primary input. Either player can take either photo. For scripted actions, use `native-turret-photo-capture --target turret-13` or `--target turret-15` while framing the matching turret. The host verifies the shot in the game and records each turret once. Return to Borough and use `native-quest-turnin --target Photo_DB_Turrets` for the shared cash and personal XP.

The raw run directories are `campaigns/<campaign>/run` and `clients/<profile>/run` under the chosen workspace. Keep their credentials, invitation files, saves, and generated stage outside commits. The CLI emits telemetry without requiring tools to read the invitation contents.
