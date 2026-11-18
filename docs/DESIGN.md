# Project Silverfish Co-op Design

Rules 1–18 are locked.

Project Silverfish co-op is a shared, host-run campaign. The party shares the world and wallet. Each player controls a character with their own inventory in that campaign.

## Locked rules

1. **Connection options.** Players can join through Steam Invite or Direct IP. The host chooses the UDP listening port for Direct IP. Both routes enter the same host session and use the same character roster. A Steam invite may carry a direct endpoint or lead to a lobby that uses Steam's relay. Steam relay sessions use virtual port addressing.

2. **Connection recovery.** Brief Internet or Steam interruptions can be recovered from using the reconnect rules in rule 14. Reconnecting through Direct IP during a Steam outage requires a reachable direct endpoint and the ability to authenticate the same campaign participant over that route.

3. **Save isolation.** Single-player and co-op use independent save storage. Loading, autosaving, restoring, and deleting saves in either mode affect that mode's saves only.

4. **Host authority.** The host owns game settings, world seed, reputation, quest state, time of day, weather, world events, and shared world flags. The host resolves updates and distributes the authoritative state to the party.

5. **Character selection.** All co-op characters, including guest characters, live in the host's campaign save. Joining players can create a character or select one of their existing characters for that campaign.

6. **Hot join.** Players can join a running campaign. A new connection spawns at a safe position near the host in the host's current zone when the party is ready. A reconnect within the grace period follows rule 14.

7. **Downed, revive, and party wipe.** Lethal damage downs a player. A downed player is forced to crouch, moves at half speed, and cannot jump or use items, weapons, or inventory. All other players in the same map see an exclamation mark above the downed player's location. An eligible teammate revives them with the interact action, bound to F by default. A revive removes the downed restrictions and restores health under rule 17. When all active characters are downed, the host declares a game over and respawns the party at the host's last place of rest. If the game's item retention setting is enabled, each player chooses the allowed items to keep. If backpack drops are enabled, each drop is unique and interactable only by its associated character.

8. **Shops, NPCs, and quests.** Players can interact with shops and NPCs independently. Any player can start and complete quests on the host's behalf. The host resolves world-changing choices and synchronizes progress for the party. Each eligible character receives their personal rewards. Shared cash follows rule 16.

9. **Shared wallet.** Every player draws from the same money pool. The host owns the balance and validates changes to it.

10. **Independent inventories.** Each character has their own inventory. The host tracks and saves it with that character.

11. **Host-only zone transitions.** Only the host can initiate zone transitions. Zone boundaries block guests. The host is also blocked when any active character is more than 15 meters from the host. Party travel follows rule 18.

12. **Shared, trait-aware loot.** The host rolls loot from a container or dead enemy using the traits of the player who opened it or was credited with the kill. For a container, the first accepted open establishes the opener. The host synchronizes the same rolled pool to the party. A stack can be claimed once, and reopening or reconnecting preserves the original roll.

13. **Permanent character ownership.** Each character has a persistent owner within the host campaign. Guests see and control their own characters. Each participant controls one character at a time, and each character has one active controller. Verified Steam identity and host-recognized Direct IP credentials can identify the same participant and character roster. The host checks game and mod compatibility before admitting a player.

14. **Reconnect grace period and active roster.** The default grace period is 90 seconds. During that window, a disconnected character stays in place and can take damage or be revived. The character remains active for distance checks, party wipes, and quest reward eligibility. A valid reconnect returns control of the same character with its current state. On expiry, the host saves the character's state and removes it from the active roster. When the player joins again, they use the normal safe spawn near the host and retain their saved health, downed state, and inventory. Pending joins count as active once their character enters the world.

15. **Consistent host saves.** Keep the world, wallet, characters, inventories, checkpoint, and dropped backpacks together in one consistent campaign snapshot with a backup. Commit a save on rest, zone travel, and orderly host exit. Resume from the last committed co-op save after a crash. The original host resumes the campaign in the first release.

16. **Authoritative rewards and transactions.** For each quest completion, grant personal rewards once to every character eligible at completion, including characters within reconnect grace. Credit the listed quest cash once per party to the shared wallet. The host settles each quest outcome once. A shop transaction validates funds and stock and commits the wallet, stock, and item transfer together. The host arbitrates simultaneous loot claims, shop purchases, and quest turn-ins.

17. **Revive and wipe details.** Revive requires a living teammate outside the downed state and within interaction range. Revive restores 10% of the target's maximum health, with a minimum of 1 HP. The initial defaults are no bleed-out timer and friendly fire off. A downed host continues hosting. Once a party wipe starts, lock the roster and give each player a bounded item-selection window if retention is enabled. Resolve each character's item retention and owner-only backpack drop once before the party respawns.

18. **Coordinated zone travel.** The host validates the 15-meter check against the active roster before travel. Downed characters and characters within reconnect grace count. Show which characters block the transition. Queue incoming joins during travel or a wipe, then admit new arrivals at a safe spot near the host once the party can accept them.

## Implementation milestones

### First prototype

- Run a two-player Direct IP listen session on the shipped test map. A client can join by address and host-selected port and take control of a separate character.
- Persist host-owned co-op character and session state under an isolated co-op save path. Validate loading after restart and check that single-player save files retain their contents.
- Exercise a brief disconnect and reconnect against the same host with the same character. Check that a returning character reclaims its slot, the original pawn or saved state required by the reconnect path, and any persistent data already covered by the prototype.
- Verify two processes locally first. The next network check is two separate PCs, including whether they can see each other move during play.
- Provide machine-readable status, diagnostic events, and a documented local control interface for this prototype.

### First playable slice and campaign work

- Add one contested pickup and one revive to the two-player session. Both clients should agree on the outcome, including after reconnect.
- Validate Steam invite integration on the supported Steam build. Test a reachable Direct IP path separately from relay availability and route switching.
- Extend the first slice to campaign travel, shop and quest interactions, shared economy, world events, inventory persistence, and the complete downed and party wipe rules.

## AI operation

Expose documented, stable commands for supported actions and structured output for their results. Status should identify the role, current map, session and connection state, active and reserved roster, and save outcome as those capabilities become available. Give events timestamps and identifiers that link an action to its result. Use explicit outcomes for rejected or incomplete operations so a model can tell when a join, save, or reconnect has actually finished.

Keep operator controls local and explicitly scoped to the running mod instance. Keep credentials out of diagnostic status and logs. Run playtesting from staged game data and give each process its own user data directory. Keep the repository focused on mod code, documentation, and reproducible tooling.
