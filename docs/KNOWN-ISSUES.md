# Known Issues

Project Silverfish Co-op is a proof of concept under active development. This list summarizes reports from release-candidate playtests. Check the notes for the specific ZIP you installed. Fixes verified in development can still need a live two-PC retest. [Release notes](https://github.com/hypnotoadtrance/silverfish-coop/releases)

## Being worked on

| Area | Known issue |
| --- | --- |
| Guest weapons and combat | A guest can aim and fire locally while the host sees no shot and hears no report. Ammo can return after the shot, and manual reload can fail. Guest shots and melee were also reported to leave the Intro warehouse robots unharmed on the host. |
| Party wipe and respawn | In published builds, the party can respawn before everyone confirms what to keep. An isolated two-player test of the development fix verified that the guest could confirm first and the party waited for the host. The base-game selection screen and a mismatch in held weapons after respawn are still open. |
| Quests and bounty boards | Some eligible offers disappear from Simmi's jobs and the physical board. The development build restored a missing Flood Plains offer in an isolated Observatory test and accepted it into both journals. Additional offers, objectives, and redemption still need verification. |
| Crossroads weather | After traveling, a guest can see a storm while the host has clear skies. In another playtest, both players saw the storm and heard its siren during the same visit. |

## Queued and awaiting wider verification

| Area | Known issue |
| --- | --- |
| Guest inventory and medical items | Guests can fail to equip or remove armor, or to use a medical item to restore health. Exact behavior varies by item and slot. |
| Downed controls | A player downed while aiming can stay zoomed in while the downed state forces them to crouch. |
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
