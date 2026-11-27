# Known Issues

Use the same release candidate on all PCs. Builds before `rc-ad3c9da` used an older co-op save format. Start a fresh campaign when upgrading from one of those builds. [Release index](RELEASES.md) · [Co-op save reset](PLAYING-COOP.md#reset-co-op-campaigns)

## Current priorities

1. **Menu UX.** Verify physical keyboard and controller navigation and audible hover and click sounds on the title, Host, Join, and Manage Saves screens. Confirm Steam password handling on two accounts.
2. **Responsiveness and networking.** Measure the Observatory cabinet hitch and mod traffic in larger Steam parties. On `rc-5780cd4`, replaying the same captured views with the new dictionary cut encoded snapshot bytes by about 17% in Crossroads and Tutorial Dock. Two-player Direct IP walking still measured around 77–91 kbps sent per seat in those runs. The worker targets 20 Hz. Aim for 100 ms or less round trip where the connection allows it. The Crossroads frame-pacing investigation is on hold. [Network test notes](NATIVE-INTEGRATION.md#replication-and-travel-pacing)
3. **Session stability and combat.** Give any reproduced crash or unexpected return to title first priority. Verify guest hits, NPC retaliation and death, and loot pickup in a normal Steam fight.
4. **World and content compatibility.** Expand discovery of stock-style quests, randomized objectives, buried items, maps, transitions, and interactables. Exercise broader coverage as shared handlers mature.
5. **Polish and optional features.** Keep avatar and weapon alignment, animation blending, lean, visual and audio effects, and the optional GitHub update check behind functional work.

## Open defects and targeted follow-ups

| Issue | Current status |
| --- | --- |
| Title and Steam join | In rendered isolation on `rc-d7ab8df`, the Host and Direct IP Join forms kept focus and stayed in view when resized from 640×480 to 392×260 at stock DPI. Back and the Direct IP save management flow passed in the same two launches. On `rc-edabc5b`, a simulated Steam refresh moved the selected lobby to another page and preserved focus by identity. When it closed, focus moved to Refresh. Retest passwordless and protected Public joins, a lobby closing during join, and an invite after cancelling a Public join on two Steam accounts. |
| Lamp state | The lamp by the bed failed to light on the guest's screen during Steam testing. On `rc-e28804d`, the isolated pair agreed on the stock oil lantern's light after the host lit it and the guest extinguished it. Both gold gas-lamp models passed isolated state checks on `rc-605ebde`. Retest the bed lamp, both gold models, and the gray oil lanterns over Steam. |
| Container and lock interactions | Guests reported lids snapping open and missing lockpick result popups. The cabinet showed intermediate lid poses on the guest. In isolated Direct IP on `rc-df710ee`, a guest unlocked, opened, closed, and relocked the Crossroads warehouse office door. On `rc-49a4cd3`, guests opened and closed the Crossroads wood door and Tutorial Dock gate. Focused checks verified that a cached door reading inside a newer world update cannot reverse the confirmed pose. Retest the latch and closely spaced door interactions over Steam, along with dumpster motion and both lockpick outcomes. |
| Guest equipment and medicine | Guests reported that the Fur Cap and Flashbang would not equip, and Homebrew Medicine would not consume. On `rc-605ebde`, the Fur Cap and Flashbang remained in the guest's equipped slots after reconciliation. Check Homebrew Medicine's item debit and personal effects in Steam co-op. |
| Throwable equipment | On `rc-445c238`, an isolated guest threw a stock Flashbang, spent one charge, and received separate world blast and personal flash feedback from that throw. The previous grouped run covered projectile flight and retirement on both seats and host and guest bolt and pipe bomb throws. Check rendered particles, screen fade, and audible effects in a foreground Steam session. Mounted custom equipment still needs playtesting. |
| Faction approval and disguises | The isolated `rc-8c4ca79` pair kept shared approval at 25 when only the guest wore a Fur Cap. Both saw 75 once the host wore one. The guest's matching State gear activated only their disguise, and swapping back to the Fur Cap cleared it. Check the Steam Journal, NPC recognition, and other disguise combinations. |
| Trade stack quantity | Isolated merchant purchases and NPC barter support partial stacks. Recheck those quantity pickers over Steam. A finite merchant sale still transfers the whole selected stack. |
| Stardust presentation | The host used Stardust and both players returned to the last rest site as expected. The use animation was missing. |
| Concurrent trader menus | On `rc-8d7ccfc`, both Observatory counters updated their own stock revisions after guest purchases in isolated Direct IP. In an earlier trial, roaming barter stayed open and refreshed after a competing host purchase. Verify concurrent purchases over Steam and at other trader types. |
| Interaction delay and frame hitches | On `rc-3dbf826`, a confirmed crate or enemy-loot pickup can update the backpack even after a world update removes the item model. An isolated pair verified both transfers. Verify the cabinet hitch, rapid switches and pickups, simultaneous opening, and mod traffic in a two-guest Steam party. [Enable the Steam report](PLAYING-COOP.md#network-timing) |
| Crash and disconnect recovery | A Steam host with two guests crashed in Crossroads. The dump identified a global object lookup in loose-pickup readback. The current path uses current-world actor references and passed isolated travel, carrying, and pickup. On `rc-0e7e46f`, a forced title return rejoined the same isolated Direct IP campaign in 2.5 seconds, and a focused test verified that a newer visit displaces an in-progress join. Retest extended Crossroads play with two Steam guests, rejoining after a guest exits, aimed weapon swaps, enemy loot pickup, and singleplayer combat. |

<a id="current-playtest-checks"></a>

## Next two-PC Steam retest

| Route | Check |
| --- | --- |
| Title and Steam join | Check passwordless and protected Public lobbies, and confirm an edited guest character name appears in the save menu. The edited name and Manage Saves passed in isolated Direct IP. On `rc-8abcbbb`, deleting a guest returned to the refreshed character list and the host reopened the same campaign. |
| Observatory | Take several items from the cabinet in the shed by the campfire and watch for hitches. Try head and chest gear, Contractor Vest, Land Mine, and manual stack merge and split. Those inventory paths passed isolated checks. |
| Crossroads excavation | On `rc-7d40020`, both seats saw ordinary holes in isolated Direct IP, including a guest dig using their own camera angle. The guest also dug up the Missing Contact farmhouse package, kept it through reconnect, and turned it in once. The scarecrow artifact is a separate placement. Check that original route in Steam co-op, including digging, handoff, and opening the package. [Shovel fix](https://github.com/hypnotoadtrance/silverfish-coop/commit/7d400205af59bd365ab7a79d33236a0c76d2f590) |
| Intro warehouse | On `rc-da4698a`, the isolated guest drew retaliation, reloaded during the fight, killed the worker, and collected its drop once. The same ZIP passed the garage-door disagreement with the Rusty Pistol, a LightAR burst, and buckshot against a Bandit, drone, and turret. An earlier run passed the warehouse plank. Retest these paths over Steam and check alternate ammo and magazine counts across weapon swaps and travel. |
| Tutorial Dock loot on a revisit | In isolated Direct IP on `rc-64632cb`, the Rusty Pistol respawned for both players. Both games still withheld the looted bandage. A streamed loot pickup also stayed claimed when its sublevel reloaded. Retest both items over Steam. |
| Quests and travel | In isolated Direct IP, `rc-9e2bc30` passed the stock Smuggler briefcase through Magpie's turn-in, a streamed quest board, a discovered map exit, and stock buried rewards in Ruin Fields and Beach. A Steam pair confirmed Crossroads jammer placement, destruction, and the journal step for turn-in. Starting Crossroads Dead Drop from Simmi failed in that playtest. Check that acceptance, shared debt in the journal, randomized placements, late joins, transition sound and fade, and weather. |

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
