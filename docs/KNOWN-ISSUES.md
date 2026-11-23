# Known Issues

Project Silverfish Co-op is a proof of concept under active development. This list summarizes reports from release-candidate playtests. Check the notes for the specific ZIP you installed. Fixes verified in development can still need a live two-PC retest. [Release notes](https://github.com/hypnotoadtrance/silverfish-coop/releases)

## Being worked on

| Area | Known issue |
| --- | --- |
| Enemy and allied NPC combat (current priority) | In the latest two-PC Intro warehouse fight, both seats saw the guest's Rusty Pistol hit robots and saw the robots react. The guest could not kill the robots. Host kills worked. An isolated Direct IP test on `rc-9ef9f14` used a guest Rusty Pistol against a full-health Tutorial Dock worker and verified its death and ragdoll on both peers. Wider two-PC combat retesting is pending. |
| Guest weapon inputs and presentation | Both seats saw the cut-down rifle break a bottle. They also saw the Rusty Pistol fire, and the guest could reload it. The 9mm Revolver neither appeared to fire to the host nor broke the bottle. Manual R reload failed on the 9mm Revolver and the cut-down rifle. The guest's first-person muzzle flash was offset on the cut-down rifle and Rusty Pistol, and their Rusty Pistol reload animation only appeared to the host. `rc-9ef9f14` changes weapon routing, reload timing and presentation. Retest the exact guns from that report. |
| Party wipe and respawn | Published builds can respawn the party early and show a dropped weapon still in hand. The development build uses the base-game keep slots and waits for both confirmations. An isolated two-player test verified a choice from a 17-item inventory, and separate tests verified the kept and dropped weapon states. Foreground mouse and keyboard interaction still needs playtesting. Keep whole stacks when selecting items. |
| Quests and bounty boards | Some eligible offers disappear from Simmi's jobs and the physical board. The development build restored a missing Flood Plains offer in an isolated Observatory test and accepted it into both journals. Additional offers, objectives, and redemption still need verification. |
| Crossroads weather | After traveling, a guest can see a storm while the host has clear skies. In another playtest, both players saw the storm and heard its siren during the same visit. |

## Queued and awaiting wider verification

| Area | Known issue |
| --- | --- |
| Guest inventory and medical items | Guests can equip worn gear but report that neither dropping from the context menu nor dragging a worn item out works. This affects both the chest and head slots. Some medical items still fail to apply, or fail before they are consumed. |
| Downed controls | In published builds, a player downed while aiming can stay zoomed in. The development fix calls the game's aim-cancel routine. An isolated two-player test verified that the guest stopped aiming and crouched when downed. |
| Held items and animations | The guest saw the host swing a melee weapon. The host missed the guest's swing, and a melee weapon appeared to float near the remote character's feet. A previous playtest also reported the mushroom-use motion near the hips. |
| Roaming NPC barter | The playtest reported failed exchanges and stale offers on ordinary talkable NPCs. Isolated Observatory tests verified a stock barter exchange and reopening against host stock. Retest an ordinary roaming NPC with a guest exchange and a host purchase while the guest's barter menu stays open. |
| Warehouse doors, props, and switches | Breakable doorway boards can remain in one player's way after the other breaks them. The office switch and lights can disagree, and a locally broken bottle can remain intact on the host. |
| Flashlights | Both seats heard the switch but did not see the other player's beam illuminate the scene. Holding C with a one-handed weapon did not show the offhand flashlight to the observer. |
| Guest consumables | The guest used an MRE but could not eat Leftovers or Clyde’s Honey Egg. Audit the stock use routes for food, medicine, and items with additional effects. |
| World items and creatures | The guest did not see a bolt or pipe bomb thrown by the host, and missed the pipe bomb detonation. `rc-9ef9f14` changes pipe bomb and bolt replication. Guests can fail to collect a live frog. |
| Placed loot on revisits | A previously collected Intro bandage reappeared for the guest while the host still saw it as taken. In a muted Direct IP test on `rc-86e2774`, the host's stock copy was absent and the guest's copy stayed hidden after returning to Tutorial Dock. Retest longer loot replenishment cycles in a live campaign. |
| Shovel digging | Host and guest reported that neither could see the other's shovel digs. In muted Direct IP tests on `rc-015f00a`, the guest picked up and equipped the stock Crossroads shovel and both peers observed one hole at the same location. Host-originated digs, unearthed loot, and guest excavation of the Missing Contact package still need verification. |
| Ragdoll carrying | Guest-carried bodies moved smoothly on both seats. While the host carried a body, the guest saw it repeatedly snap toward its pickup point, then saw the correct final position after release. |
| Enemies and anomalies | Development tests have verified several host-owned enemies and traps, including a stock flying drone's death and ragdoll. Spawn populations, guest damage, and death state still need coverage across more encounters. |
| Travel and presentation | Stock zone exits and door transitions have passed some isolated tests. In one `rc-015f00a` run, the host session stopped while the guest was reconnecting after Observatory-to-Crossroads travel. The route passed on retry. Recheck that route, the 15-meter regroup rule, the fade and sound, and the guest's first visible frame on arrival. |
| Performance and connection recovery | Players have reported interaction delay, Crossroads frame hitches, and visible corrections while moving props. Steam reconnect, lobby discovery, and behavior under internet latency need wider testing. |
| Menu and code cleanup | Stock UI integration, hosting and join usability, and removal of obsolete development paths remain on the backlog. |

Recent playtests confirmed individual death-backpack recovery, food restoring the guest's hunger, and current and lasting damage applying to the guest.

When reporting a bug, include the release tag, map or landmark, exact item or NPC if known, whether you used Steam or Direct IP, and what the host and guest each saw. [Open a GitHub issue](https://github.com/hypnotoadtrance/silverfish-coop/issues/new)

For detailed engineering checks, see the [integration notes](IN-GAME-INTEGRATION.md) and [native test coverage](NATIVE-INTEGRATION.md).
