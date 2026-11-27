# Known Issues

Use the same release candidate on all PCs. Builds before `rc-ad3c9da` used an older co-op save format. Start a fresh campaign when upgrading from one of those builds. [Release index](RELEASES.md) · [Co-op save reset](PLAYING-COOP.md#reset-co-op-campaigns)

## Current priorities

1. **Menu UX.** Refine focus, back navigation, and hover and click sounds across the title, Host and Join screens, and Manage Saves. Confirm Steam password handling on two accounts.
2. **Responsiveness and networking.** Measure the Observatory cabinet hitch, Crossroads frame pacing, and actual mod traffic in Depot and larger Steam parties. The worker already targets 20 Hz and compresses eligible traffic. Aim for 100 ms or less round trip where the connection allows it. Continue Lua-to-C++ migration where profiling finds a bottleneck.
3. **Session stability and combat.** Give any reproduced crash or unexpected return to title first priority. Verify guest hits, NPC retaliation and death, and loot pickup in a normal Steam fight.
4. **World and content compatibility.** Expand discovery of stock-style quests, randomized objectives, buried items, maps, transitions, and interactables. Exercise broader coverage as shared handlers mature.
5. **Polish and optional features.** Keep avatar and weapon alignment, animation blending, lean, visual and audio effects, and the optional GitHub update check behind functional work.

## Open defects and targeted follow-ups

| Issue | Current status |
| --- | --- |
| Title and Steam join | In isolated Direct IP, `rc-bd113a8` passed Manage Saves, including leaving a pending character lookup and deleting a guest. The `rc-8d7ccfc` Steam fixture verified a fresh invite arriving before the cancelled Public join callback finishes. Retest this sequence, passwordless join, and a protected retry with two Steam accounts. |
| Oil lantern state | `rc-5a157fb` passed ignition and extinguishing from both seats and a guest joining after the host broke a lamp in isolated Direct IP. Check gold and gray lanterns in a Steam playtest. |
| Concurrent trader menus | On `rc-8d7ccfc`, both Observatory counters updated their own stock revisions after guest purchases in isolated Direct IP. In an earlier trial, roaming barter stayed open and refreshed after a competing host purchase. Verify concurrent purchases over Steam and at other trader types. |
| Interaction delay and frame hitches | On `rc-b200c1b`, the first cold-crate item appeared 81 ms after guest Interact in isolated Direct IP. On `rc-8d7ccfc`, the native head setter reduced Lua allocations and the host still reached 44 ms in a coarse offscreen Crossroads walk. Foreground Steam timing and larger-party traffic remain open. [Timing details](NATIVE-INTEGRATION.md#replication-and-travel-pacing) · [Enable the Steam report](PLAYING-COOP.md#network-timing) |
| Crash and disconnect recovery | A forced guest return to title rejoined the same Direct IP campaign in about 3 seconds on `rc-b200c1b`, with the host still in the map. A separate forced host return allowed both players to reopen the original campaign. Retest unexpected title recovery, aimed weapon swaps, enemy loot pickup, and singleplayer combat over Steam. |

<a id="current-playtest-checks"></a>

## Next two-PC Steam retest

| Route | Check |
| --- | --- |
| Title and Steam join | Check passwordless and protected Public lobbies, and confirm an edited guest character name appears in the save menu. The edited name and Manage Saves passed in isolated Direct IP. On `rc-8abcbbb`, deleting a guest returned to the refreshed character list and the host reopened the same campaign. |
| Observatory | Take several items from the cabinet in the shed by the campfire and watch for hitches. Try head and chest gear, Contractor Vest, Land Mine, and manual stack merge and split. Those inventory paths passed isolated checks. |
| Crossroads excavation | Dig between the two rocks by the scarecrow. The post-crash fix is in `rc-ad3c9da`. In isolated Direct IP, the host dug up and dropped the package, and the guest reclaimed and opened it. Check the original route in Steam co-op. [Fix source](https://github.com/hypnotoadtrance/silverfish-coop/commit/2b6e90b8748c433ac2538587fbc25f9a9dcd1604) |
| Intro warehouse | Let the guest fight moving enemies with melee and ranged weapons, including a shot under the garage door. Check repeat swings, damage and death on both seats, then loot an enemy drop. Break a warehouse plank and confirm passage for both players. Spot-check guest reload, alternate ammo, and magazine counts across weapon swaps and travel. |
| Simmi and travel | On `rc-b200c1b`, all three Simmi jammers passed in isolated Direct IP. The stock Smuggler briefcase stayed visible through its renewal and the guest collected and redeemed it once at Magpie's board. The Metro and Flood Town Dead Drops also passed stock pickup and turn-in after reconnecting in an earlier candidate. Check quest sharing, randomized placements, transition sound and fade, and weather over Steam. |

Other development fixes awaiting broader play include door, gate, switch, ladder, and lockpick interactions, carried and breakable props, destructibles in streamed sublevels, placed loot on revisits, spawned NPCs and moving anomalies, consumables and cooking, and guest damage feedback. The next avatar check covers stiff head turns, occasional cross-eyed pupils, and head and tail attachments during aim. Head rotation currently eases toward its target on the 25 ms observer loop. Compare pupil direction against the stock tracker, which drives the eye material's `LookVector`. [Native verification](NATIVE-INTEGRATION.md)

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
