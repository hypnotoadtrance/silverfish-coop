# Known Issues

Use the same release candidate on all PCs. Builds before `rc-ad3c9da` used an older co-op save format. Start a fresh campaign when upgrading from one of those builds. [Release index](RELEASES.md) · [Co-op save reset](PLAYING-COOP.md#reset-co-op-campaigns)

## Current priorities

1. **Menu UX.** Refine the title, Host and Join screens, and Manage Saves. Include focus, back navigation, hover and click sounds, and the Steam password prompt.
2. **Responsiveness and networking.** Measure the Observatory cabinet hitch, Crossroads frame pacing, and actual mod traffic in Depot and larger Steam parties. The worker already targets 20 Hz and compresses eligible traffic. Aim for 100 ms or less round trip where the connection allows it. Continue Lua-to-C++ migration where profiling finds a bottleneck.
3. **Session stability and combat.** Give any reproduced crash or unexpected return to title first priority. Verify guest hits, NPC retaliation and death, and loot pickup in a normal Steam fight.
4. **World and content compatibility.** Expand discovery of stock-style quests, randomized objectives, buried items, maps, transitions, and interactables. Exercise broader coverage as shared handlers mature.
5. **Polish and optional features.** Keep avatar and weapon alignment, animation blending, lean, visual and audio effects, and the optional GitHub update check behind functional work.

## Open defects and targeted follow-ups

| Issue | Current status |
| --- | --- |
| Steam join screens | Automated checks cover open and protected lobbies and retry. `rc-8bf1ded` replaces a slow Public search when the player leaves and reopens the list. Confirm on two Steam accounts. |
| Oil lantern state | `rc-5a157fb` passed ignition and extinguishing from both seats and a guest joining after the host broke a lamp in isolated Direct IP. Check gold and gray lanterns in a Steam playtest. |
| Concurrent trader menus | On `rc-5d08fc1`, the roaming barter window stayed open and refreshed after the host bought the guest's selected item in isolated Direct IP. Verify concurrent purchases over Steam and at other trader types. |
| Interaction delay and frame hitches | The saved cabinet pickup left saves untouched and the longest nearby frame was 23 ms. On `rc-8bf1ded`, a five-second offscreen Direct IP Crossroads walk reached 33.5 ms P99 and 38.4 ms maximum host frame intervals. The largest interval coincided with a Lua heap drop. Foreground Steam timing and larger-party traffic measurements remain open. [Timing details](NATIVE-INTEGRATION.md#replication-and-travel-pacing) · [Enable the Steam report](PLAYING-COOP.md#network-timing) |
| Crash and disconnect recovery | `rc-68f74c6` recovered a guest's inventory and unsaved character state after a forced exit in isolated Direct IP. The host and second guest stayed in play. Replacement took 113 seconds. Repeat over Steam. On `rc-5d08fc1`, verified enemy-loot capture and claim refusals leave the host active. The aimed rifle-to-pistol swap passed isolated Direct IP on `rc-5251702`. Named object misses during travel use the hash-only path on `rc-8bf1ded`. The Flood Town crossing passed in isolated Direct IP. Keep the Steam swap, enemy-loot crash, rejoin after guest exit, and singleplayer combat on the regression list. |

<a id="current-playtest-checks"></a>

## Next two-PC Steam retest

| Route | Check |
| --- | --- |
| Title and Steam join | Check passwordless and protected Public lobbies, and confirm an edited guest character name appears in the save menu. The edited name and Manage Saves passed in isolated Direct IP. On `rc-8abcbbb`, deleting a guest returned to the refreshed character list and the host reopened the same campaign. |
| Observatory | Take several items from the cabinet in the shed by the campfire and watch for hitches. Try head and chest gear, Contractor Vest, Land Mine, and manual stack merge and split. Those inventory paths passed isolated checks. |
| Crossroads excavation | Dig between the two rocks by the scarecrow. The post-crash fix is in `rc-ad3c9da`. In isolated Direct IP, the host dug up and dropped the package, and the guest reclaimed and opened it. Check the original route in Steam co-op. [Fix source](https://github.com/hypnotoadtrance/silverfish-coop/commit/2b6e90b8748c433ac2538587fbc25f9a9dcd1604) |
| Intro warehouse | Let the guest fight moving enemies with melee and ranged weapons, including a shot under the garage door. Check repeat swings, damage and death on both seats, then loot an enemy drop. Break a warehouse plank and confirm passage for both players. Spot-check guest reload, alternate ammo, and magazine counts across weapon swaps and travel. |
| Simmi and travel | On `rc-5d08fc1`, the guest completed the Flood Plains, Black Lake, and Crossroads jammer objectives. Both journals reflected all three turn-ins and the shared wallet received $950 in isolated Direct IP. Repeat over Steam and check transition sound, fade, weather, and randomized objective locations. |

Other development fixes awaiting broader play include door, gate, switch, ladder, and lockpick interactions, carried and breakable props, destructibles in streamed sublevels, placed loot on revisits, spawned NPCs and moving anomalies, consumables and cooking, and guest damage feedback. Use those checks when they come up during ordinary play. [Native verification](NATIVE-INTEGRATION.md)

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
