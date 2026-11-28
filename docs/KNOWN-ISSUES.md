# Known Issues

Use the same release candidate on all PCs. Builds before `rc-ad3c9da` used an older co-op save format. Start a fresh campaign when upgrading from one of those builds. [Release index](RELEASES.md) · [Co-op save reset](PLAYING-COOP.md#reset-co-op-campaigns)

## Current priorities

1. **Menu UX.** Verify physical keyboard and controller navigation and audible hover and click sounds on the title, Host, Join, and Manage Saves screens. Confirm Steam password handling on two accounts.
2. **Responsiveness, frame pacing, and networking.** Investigate long Crossroads frames, including a 38.6 ms interval with a Lua heap drop on `rc-2d588f2`, alongside the Observatory cabinet hitch and traffic in larger Steam parties. The Steam report identifies each anonymous peer's contribution to send and receive peaks and, on `rc-9444e46`, also measures falling and carried loot traffic. Two-player Direct IP walking measured around 77–91 kbps sent per seat on `rc-5780cd4`. The worker targets 20 Hz. Aim for 100 ms or less round trip where the connection allows it. [Network test notes](NATIVE-INTEGRATION.md#replication-and-travel-pacing)
3. **Session stability and combat.** Give any reproduced crash or unexpected return to title first priority. On `rc-33e58fc`, two guests held overlapping stock swings in isolated Direct IP and each damaged the shared NPC once. Check simultaneous guest swings, NPC retaliation and death, and loot pickup in a normal Steam fight.
4. **World and content compatibility.** Expand discovery of stock-style quests, randomized objectives, buried items, maps, transitions, and interactables. Exercise broader coverage as shared handlers mature.
5. **Polish and optional features.** Keep avatar and weapon alignment, animation blending, lean, visual and audio effects, and the optional GitHub update check behind functional work.

## Open defects and targeted follow-ups

| Issue | Current status |
| --- | --- |
| Title and Steam join | In two rendered isolated launches on `rc-5264ae0`, an invalid Direct IP port put focus on UDP port, Back restored focus to Join, and the host passed Manage Saves. Earlier rendered checks covered resizing the Host and Join forms and restoring Public lobby focus after a refresh. Verify physical keyboard and controller input, audible hover and click sounds, passwordless and protected Public joins, a lobby closing during join, and an invite after cancelling a Public join on two Steam accounts. |
| Lamp state | The lamp by the bed failed to light on the guest's screen during Steam testing. On `rc-5aa9620`, both gold gas lamps and the Observatory and Crossroads oil lanterns passed isolated Direct IP checks through the shared switch path. Retest lighting and extinguishing from each seat and picking up an unlit oil lantern over Steam. |
| Container and lock interactions | Guests reported lids snapping open and missing lockpick result popups. The cabinet showed intermediate lid poses on the guest. In isolated Direct IP on `rc-df710ee`, a guest unlocked, opened, closed, and relocked the Crossroads warehouse office door. On `rc-49a4cd3`, guests opened and closed the Crossroads wood door and Tutorial Dock gate. Focused checks verified that a cached door reading inside a newer world update cannot reverse the confirmed pose. Retest the latch and closely spaced door interactions over Steam, along with dumpster motion and both lockpick outcomes. |
| Guest equipment and medicine | Guests reported that the Fur Cap and Flashbang would not equip, and Homebrew Medicine would not consume. On `rc-605ebde`, the Fur Cap and Flashbang remained in the guest's equipped slots after reconciliation. Check Homebrew Medicine's item debit and personal effects in Steam co-op. |
| Throwable equipment | On `rc-445c238`, an isolated guest threw a stock Flashbang, spent one charge, and received separate world blast and personal flash feedback from that throw. The previous grouped run covered projectile flight and retirement on both seats and host and guest bolt and pipe bomb throws. Check rendered particles, screen fade, and audible effects in a foreground Steam session. Mounted custom equipment still needs playtesting. |
| Faction approval and disguises | The isolated `rc-8c4ca79` pair kept shared approval at 25 when only the guest wore a Fur Cap. Both saw 75 once the host wore one. The guest's matching State gear activated only their disguise, and swapping back to the Fur Cap cleared it. Check the Steam Journal, NPC recognition, and other disguise combinations. |
| Trade stack quantity | Isolated merchant purchases and NPC barter support partial stacks. Recheck those quantity pickers over Steam. A finite merchant sale still transfers the whole selected stack. |
| Stardust presentation | The host used Stardust and both players returned to the last rest site as expected. The use animation was missing. |
| Concurrent trader menus | On `rc-8d7ccfc`, both Observatory counters updated their own stock revisions after guest purchases in isolated Direct IP. In an earlier trial, roaming barter stayed open and refreshed after a competing host purchase. Verify concurrent purchases over Steam and at other trader types. |
| Interaction delay and frame hitches | On `rc-f034ed5`, the isolated host saw the cold shed cabinet pickup preview at 8 ms and heard the cue at 42 ms. The longest nearby frame was 22 ms. A checkpointed crate passed host and guest pickups with unchanged save files during the interactions. Verify rapid cabinet pickups, switches, simultaneous opening, and mod traffic in a two-guest Steam party. [Enable the Steam report](PLAYING-COOP.md#network-timing) |
| Crash and disconnect recovery | A Steam host with two guests crashed in Crossroads. The dump identified a global object lookup in loose-pickup readback. The current path uses current-world actor references and passed isolated travel, carrying, and pickup. A forced title return rejoined the same isolated Direct IP campaign in 2.5 seconds on `rc-0e7e46f`. Missing combat replies recovered without repeating the action on `rc-4d95541`. On `rc-b28e32f`, focused fault tests verified that a guest handler error before game or save publication leaves the session running. On `rc-2d588f2`, the isolated guest completed Crossroads travel with one enrollment while the host restored their save. Retest extended Crossroads play with two Steam guests, rejoining after a guest exits, aimed weapon swaps, enemy loot pickup, and singleplayer combat. |

<a id="current-playtest-checks"></a>

## Next two-PC Steam retest

| Route | Check |
| --- | --- |
| Title and Steam join | Check passwordless and protected Public lobbies, and confirm an edited guest character name appears in the save menu. The edited name and Manage Saves passed in isolated Direct IP. On `rc-8abcbbb`, deleting a guest returned to the refreshed character list and the host reopened the same campaign. |
| Observatory | Take several items from the cabinet in the shed by the campfire and watch for hitches. Try head and chest gear, Contractor Vest, Land Mine, and manual stack merge and split. Those inventory paths passed isolated checks. |
| Crossroads excavation | On `rc-9703bd1`, in isolated Direct IP the guest saw the ordinary hole before the contact report was sent. The pair verified shared loot and the Missing Contact farmhouse package through reconnect and turn-in. Retest shovel animations and the separate scarecrow artifact over Steam, including digging, handoff, and opening. [Shovel check](https://github.com/hypnotoadtrance/silverfish-coop/commit/9703bd19f4511bfc94871cd6a557a6e8c50afbf8) |
| Intro warehouse | On `rc-2efba86`, the isolated guest drew retaliation, reloaded during the fight, killed the worker, and collected its drop once. `rc-da4698a` passed the garage-door disagreement with the Rusty Pistol, a LightAR burst, and buckshot against a Bandit, drone, and turret. An earlier run passed the warehouse plank. Retest these paths over Steam, including pressing R immediately after firing and checking reserve and magazine counts across weapon swaps and travel. |
| Tutorial Dock loot on a revisit | In isolated Direct IP on `rc-64632cb`, the Rusty Pistol respawned for both players. Both games still withheld the looted bandage. A streamed loot pickup also stayed claimed when its sublevel reloaded. Retest both items over Steam. |
| Ordinary containers | Isolated Direct IP passed a streamed Cistern crate and a host-spawned Observatory crate. Verify simultaneous opening over Steam and try a placed data-only child of a stock crate or cabinet. Scripted subclasses, guest-spawned containers, and runtime containers in streamed sublevels still need coverage. |
| Quests and travel | In isolated Direct IP, `rc-9e2bc30` passed the stock Smuggler briefcase through Magpie's turn-in, a streamed quest board, a discovered map exit, and stock buried rewards in Ruin Fields and Beach. A Steam pair confirmed Crossroads jammer placement, destruction, and the journal step for turn-in. Starting Crossroads Dead Drop from Simmi failed in that playtest. On `rc-5aa9620`, the isolated host and guest could each accept it from Simmi once the campaign met the stock 1 XP requirement. Both journals updated. Recheck Simmi's Accept action over Steam when the offer is visible, along with shared debt, randomized placements, late joins, transition sound and fade, and weather. |

Other development fixes awaiting broader play include door, gate, switch, ladder, and lockpick interactions, carried and breakable props, destructibles in streamed sublevels, placed loot on revisits, spawned NPCs and moving anomalies, consumables and cooking, and guest damage feedback. On `rc-c4ab055`, the stock eye material's gaze direction followed the remote head in isolated turns from both seats. The next foreground avatar check covers pupil alignment across head styles and viewing angles, head turn stiffness, and head and tail attachments during aim. [Native verification](NATIVE-INTEGRATION.md)

## Resolved in two-PC playtests

- Ordinary Steam joining, Public lobby discovery, and the oversized Steam avatar catalog error.
- Ragdolls, Take Ammo, melee Friendly Fire, and ordinary trader stock and barter.
- Wild mushroom harvesting, Live Frog consumption, pipe bomb throw and explosion, and TV, radio, and candle toggles.
- Simmi's Crossroads jammer from acceptance to turn-in, individual death-backpack recovery, food restoring guest hunger, and current and lasting guest damage.
- **Party wipe tentatively closed.** The last observed wipe showed the stock equipment chooser and waited for each player to continue. Reopen on a new report.

### Completed cleanup pass

The cleanup retired superseded stock gameplay paths and old co-op save migrations. Save compatibility across development candidates can change. `rc-ad3c9da` uses schema 2 and skips schema 1 campaigns and cloud backups while preserving their files. [Release notes](https://github.com/hypnotoadtrance/silverfish-coop/releases/tag/rc-ad3c9da)

## Compatibility with game updates and content mods

Stock-style data and interactions have shared co-op paths. Custom scripts, quest callbacks, and new actor behavior can require additional integration. For a content mod report, include its name and version, the map or quest, and the first step where the two seats differ.

## Report a bug

Include the candidate tag, map or landmark, item or NPC, connection type, and what each player saw. [Open an issue](https://github.com/hypnotoadtrance/silverfish-coop/issues/new)

[Historical playtest reports and build evidence](https://github.com/hypnotoadtrance/silverfish-coop/blob/c0b0c87ac6f684a87afd1b32bee61fa23092f34a/docs/KNOWN-ISSUES.md) · [Integration notes](IN-GAME-INTEGRATION.md)
