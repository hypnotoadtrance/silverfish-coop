# Known Issues

Project Silverfish Co-op is a proof of concept under active development. This list summarizes reports from release-candidate playtests. Check the notes for the specific ZIP you installed. Fixes verified in development can still need a live two-PC retest. [Release notes](https://github.com/hypnotoadtrance/silverfish-coop/releases)

## Being worked on

| Area | Known issue |
| --- | --- |
| Enemy and allied NPC combat (current priority) | Isolated tests verified a guest Old Pipe kill, a State conscript killing a Bandit, and a turret dropping a downed target. Development RC `2f9a765` adds the Rusty Pistol from Tutorial Dock. Over Direct IP, the gun kept its spent ammo through the stock Observatory exit into Tutorial Dock. A guest then killed a staged, map-placed worker with one shot, and both peers showed one corpse. Natural target selection, the reported free-play fight, other melee weapons, and turret blast and smoke effects still need coverage. |
| Guest weapon inputs | Published builds can disagree on firing, magazine counts, and manual reload. An isolated test in RC f38d230 verified a guest shot spent one round on both seats and stock reload consumed the guest's reserve. Other weapons and a two-PC retest remain open. |
| Party wipe and respawn | Published builds can respawn the party early and show a dropped weapon still in hand. The development build uses the base-game keep slots and waits for both confirmations. An isolated two-player test verified a choice from a 17-item inventory, and separate tests verified the kept and dropped weapon states. Foreground mouse and keyboard interaction still needs playtesting. Keep whole stacks when selecting items. |
| Quests and bounty boards | Some eligible offers disappear from Simmi's jobs and the physical board. The development build restored a missing Flood Plains offer in an isolated Observatory test and accepted it into both journals. Additional offers, objectives, and redemption still need verification. |
| Crossroads weather | After traveling, a guest can see a storm while the host has clear skies. In another playtest, both players saw the storm and heard its siren during the same visit. |

## Queued and awaiting wider verification

| Area | Known issue |
| --- | --- |
| Guest inventory and medical items | Isolated two-player tests verified equipping and removing items in both native armor slots. The development build now reads ordinary gear defaults from the game. The latest test used the Lab Coat and Demo helmet. Gear with special passives, faction effects or powered behavior and guest medical item use need additional coverage. |
| Downed controls | In published builds, a player downed while aiming can stay zoomed in. The development fix calls the game's aim-cancel routine. An isolated two-player test verified that the guest stopped aiming and crouched when downed. |
| Held items and animations | Rifle aiming can show an incorrect grip or muzzle angle. Eating a mushroom is visible and audible to the other player, but the hand motion can appear near the hips. Some weapon and equipment effects still need coverage. |
| Roaming NPC barter | Dedicated traders worked in the reported session. Ordinary talkable NPCs can reject guest exchanges or leave the guest looking at stale stock after a host purchase. |
| Warehouse doors, props, and switches | Breakable doorway boards can remain in one player's way after the other breaks them. The office switch and lights can disagree, and a locally broken bottle can remain intact on the host. |
| World items and creatures | Guests can fail to collect a live frog. Flare and bolt interactions, some movable props, and corpse dragging still need broader coverage and checks after joining. |
| Enemies and anomalies | Development tests have verified several host-owned enemies and traps, including a stock flying drone's death and ragdoll. Spawn populations, guest damage, and death state still need coverage across more encounters. |
| Travel and presentation | Stock zone exits and door transitions have passed some isolated tests. Recheck the 15-meter regroup rule, the fade and sound, and the guest's first visible frame on arrival. |
| Performance and connection recovery | Players have reported interaction delay, Crossroads frame hitches, and visible corrections while moving props. Steam reconnect, lobby discovery, and behavior under internet latency need wider testing. |
| Menu and code cleanup | Stock UI integration, hosting and join usability, and removal of obsolete development paths remain on the backlog. |

Recent playtests confirmed individual death-backpack recovery, food restoring the guest's hunger, and current and lasting damage applying to the guest.

When reporting a bug, include the release tag, map or landmark, exact item or NPC if known, whether you used Steam or Direct IP, and what the host and guest each saw. [Open a GitHub issue](https://github.com/hypnotoadtrance/silverfish-coop/issues/new)

For detailed engineering checks, see the [integration notes](IN-GAME-INTEGRATION.md) and [native test coverage](NATIVE-INTEGRATION.md).
