# Known Issues

Use the same release candidate on all PCs. Builds before `rc-ad3c9da` used an older co-op save format. Start a fresh campaign when upgrading from one of those builds. [Release index](RELEASES.md) · [Co-op save reset](PLAYING-COOP.md#reset-co-op-campaigns)

## Current priorities

1. **Menu UX.** Refine focus, back navigation, and hover and click sounds across the title, Host and Join screens, and Manage Saves. Confirm Steam password handling on two accounts.
2. **Responsiveness and networking.** Measure the Observatory cabinet hitch and actual mod traffic in Depot and larger Steam parties. The worker targets 20 Hz and compresses eligible traffic. Aim for 100 ms or less round trip where the connection allows it. The Crossroads frame-pacing investigation is on hold.
3. **Session stability and combat.** Give any reproduced crash or unexpected return to title first priority. Verify guest hits, NPC retaliation and death, and loot pickup in a normal Steam fight.
4. **World and content compatibility.** Expand discovery of stock-style quests, randomized objectives, buried items, maps, transitions, and interactables. Exercise broader coverage as shared handlers mature.
5. **Polish and optional features.** Keep avatar and weapon alignment, animation blending, lean, visual and audio effects, and the optional GitHub update check behind functional work.

## Open defects and targeted follow-ups

| Issue | Current status |
| --- | --- |
| Title and Steam join | In isolated Direct IP, `rc-bd113a8` passed Manage Saves, including leaving a pending character lookup and deleting a guest. The `rc-8d7ccfc` Steam fixture verified a fresh invite arriving before the cancelled Public join callback finishes. In `rc-07ca9f4`, reusing an older campaign name opened the stock creator in isolated Direct IP. The Invite Only handoff regression also passed. Retest named Invite Only creation, the invite sequence, and passwordless and protected joining with two Steam accounts. |
| Lamp state | The two gold gas-lamp variants in the Observatory use their own stock switch. The lamp by the bed failed to light on the guest's screen during Steam testing. Both gold models and their light state passed on `rc-605ebde` in isolated Direct IP. Check the flame visuals, guest Interact, and the gray oil lanterns over Steam. |
| Container and lock interactions | Guests reported lids snapping open, missing lockpick result popups, and an unusable manual latch at the Crossroads warehouse office. The cabinet showed intermediate lid poses on the guest. Locker success feedback passed an earlier isolated check. Retest dumpster motion, both lockpick outcomes, and unlocking and opening the warehouse door. |
| Guest equipment and medicine | Guests reported that the Fur Cap and Flashbang would not equip, and Homebrew Medicine would not consume. On `rc-605ebde`, the Fur Cap and Flashbang remained in the guest's equipped slots after reconciliation. Check Homebrew Medicine's item debit and personal effects in Steam co-op. |
| Throwable equipment | Flashbang equip passed in isolated Direct IP. The supported stock throw route covers flares, the bolt, and the pipe bomb. Broader coverage needs projectile discovery and matching effects for the whole party. |
| Faction approval and disguises | The isolated `rc-8c4ca79` pair kept shared approval at 25 when only the guest wore a Fur Cap. Both saw 75 once the host wore one. The guest's matching State gear activated only their disguise, and swapping back to the Fur Cap cleared it. Check the Steam Journal, NPC recognition, and other disguise combinations. |
| Trade stack quantity | The guest trade menu offers the entire stack at once. Check smaller purchases and sales through the stock quantity picker. |
| Stardust presentation | The host used Stardust and both players returned to the last rest site as expected. The use animation was missing. |
| Concurrent trader menus | On `rc-8d7ccfc`, both Observatory counters updated their own stock revisions after guest purchases in isolated Direct IP. In an earlier trial, roaming barter stayed open and refreshed after a competing host purchase. Verify concurrent purchases over Steam and at other trader types. |
| Interaction delay and frame hitches | On `rc-b200c1b`, the first cold-crate item appeared 81 ms after guest Interact in isolated Direct IP. On `rc-8d7ccfc`, the native head setter reduced Lua allocations and the host still reached 44 ms in a coarse offscreen Crossroads walk. The latest Steam playtest reported severe latency and hitches with two guests. Measure interaction delay, frame time, and mod traffic in that party size. [Timing details](NATIVE-INTEGRATION.md#replication-and-travel-pacing) · [Enable the Steam report](PLAYING-COOP.md#network-timing) |
| Crash and disconnect recovery | A Steam host with two guests crashed in Crossroads. The dump and matching DLL symbols identify a global object lookup in the loose-pickup readback. `rc-605ebde` uses current-world actor references for these reads. It passed isolated travel, carrying, and pickup. Retest extended Crossroads play with two Steam guests. On `rc-da4698a`, a local command that expires before the game claims it can be retried within the same session. The native fixture verified both sides of the claim boundary. A forced guest return to title rejoined the same isolated Direct IP campaign in 2.3 seconds. A prior forced host return allowed both players to reopen the campaign. Retest recovery, aimed weapon swaps, enemy loot pickup, and singleplayer combat over Steam. |

<a id="current-playtest-checks"></a>

## Next two-PC Steam retest

| Route | Check |
| --- | --- |
| Title and Steam join | Check passwordless and protected Public lobbies, and confirm an edited guest character name appears in the save menu. The edited name and Manage Saves passed in isolated Direct IP. On `rc-8abcbbb`, deleting a guest returned to the refreshed character list and the host reopened the same campaign. |
| Observatory | Take several items from the cabinet in the shed by the campfire and watch for hitches. Try head and chest gear, Contractor Vest, Land Mine, and manual stack merge and split. Those inventory paths passed isolated checks. |
| Crossroads excavation | Dig between the two rocks by the scarecrow. The post-crash fix is in `rc-ad3c9da`. In isolated Direct IP, the host dug up and dropped the package, and the guest reclaimed and opened it. Check the original route in Steam co-op. [Fix source](https://github.com/hypnotoadtrance/silverfish-coop/commit/2b6e90b8748c433ac2538587fbc25f9a9dcd1604) |
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
