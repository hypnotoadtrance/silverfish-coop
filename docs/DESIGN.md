# Project Silverfish Co-op Design

The rules below describe the intended co-op behavior. The trusted-participant changes in rules 4, 9, 10, and 16 are being implemented.

Project Silverfish co-op is a shared, host-run campaign. The party shares the world and wallet. Each player controls a character with their own inventory in that campaign.

## Locked rules

1. **Connection options.** Direct IP Join asks for the host's IP address, UDP port, and password when the host sets one. The host chooses the UDP port and whether to require a password. Steam invites and lobby joins use the same host session and character roster through Steam relay. The host can make a Steam lobby Invite Only, Friends Only, or Public. Steam relay sessions use virtual port addressing.

2. **Connection recovery.** Brief Internet or Steam interruptions can be recovered from using the reconnect rules in rule 14. Reconnecting through Direct IP during a Steam outage requires a reachable direct endpoint and the ability to authenticate the same campaign participant over that route.

3. **Save isolation.** Single-player and co-op use independent save storage. Loading, autosaving, restoring, and deleting saves in either mode affect that mode's saves only.

4. **Trusted participants and shared state.** Each admitted player can report gameplay outcomes with the same trust as the host, including changes made through in-game cheat tools. The host keeps one ordered record of shared changes, applies each report once, and distributes the resulting campaign state. Match reports to the current session, character, and world objects so reconnects, late reports, and simultaneous changes converge. Campaign hosting settings and save operations still belong to the host.

5. **Character selection.** All co-op characters, including guest characters, live in the host's campaign save. The host can start a new co-op campaign through the full stock character creator or load an existing co-op campaign. Joining players can create a character or select one of their existing characters for that campaign.

6. **Hot join.** Players can join a running campaign. A new connection spawns at a safe position near the host in the host's current zone when the party is ready. A reconnect within the grace period follows rule 14.

7. **Downed, revive, and party wipe.** Lethal damage downs a player. A downed player is forced to crouch, moves at half speed, and cannot jump or use items, weapons, or inventory. All other players in the same map see an exclamation mark above the downed player's location. An eligible teammate revives them with the interact action, bound to F by default. A revive removes the downed restrictions and restores health under rule 17. When all active characters are downed, the host declares a game over and respawns the party at the host's last place of rest. If the game's item retention setting is enabled, each player chooses the allowed items to keep. If backpack drops are enabled, each drop is unique and interactable only by its associated character.

8. **Shops, NPCs, and quests.** Players can interact with shops and NPCs independently. Any player can start and complete quests on the host's behalf. The host resolves world-changing choices and synchronizes progress for the party. The party shares the host's effective faction approval. Guests can wear gear with reputation bonuses, and their gear contributes no bonus to that shared score. The game evaluates disguises from each player's own worn gear and applies recognition and compromise to that player. Each eligible character receives their personal rewards. Shared cash follows rule 16.

9. **Shared wallet.** Every player draws from the same pool and can add or remove money, including through in-game cheat tools. The host adds or subtracts each reported change once against the current shared balance. A late report of an old balance cannot overwrite another player's transaction.

10. **Independent inventories.** Each player owns the state of their character's inventory, including equipment, held items, and locally added items. The host mirrors and saves that state. Each character also has private rest-site stash contents. Players use the same physical stash box at a rest site and each sees only their character's items. With the host's combined-stash setting enabled, each character can access the same private contents from every rest site. With it disabled, their contents are stored separately for each stock stash ID. Any nearby party member can collect an item deliberately dropped in the world. The host settles the first valid pickup exactly once and saves the item with the recipient.

11. **Host-only zone transitions.** Only the host can initiate zone transitions. Zone boundaries block guests. A walk-through exit admits the host when all active characters are within 15 meters. Transition doors check the same distance when the host interacts and still require the door's normal conditions. Party travel follows rule 18.

12. **Shared, trait-aware loot.** The host rolls loot from a container or dead enemy using the traits of the player who opened it or was credited with the kill. For a container, the first accepted open establishes the opener. The host synchronizes the same rolled pool to the party. A stack can be claimed once, and reopening or reconnecting preserves the original roll until the host's stock game replenishes that source.

13. **Permanent character ownership.** Each character has a persistent owner within the host campaign. Guests see and control their own characters. Each participant controls one character at a time, and each character has one active controller. Verified Steam identity and host-recognized Direct IP credentials can identify the same participant and character roster. The host checks game and mod compatibility before admitting a player.

14. **Reconnect grace period and active roster.** The default grace period is 90 seconds. During that window, a disconnected character stays in place and can take damage or be revived. The character remains active for distance checks, party wipes, and quest reward eligibility. A valid reconnect returns control of the same character with its current state. On expiry, the host saves the character's state and removes it from the active roster. When the player joins again, they use the normal safe spawn near the host and retain their saved health, downed state, and inventory. Pending joins count as active once their character enters the world.

15. **Consistent host saves.** Keep the world, wallet, characters, inventories, checkpoint, and dropped backpacks together in one consistent campaign snapshot with a backup. Commit a save on rest, zone travel, and orderly host exit. Resume from the last committed co-op save after a crash. The original host resumes the campaign in the first release.

16. **Shared outcomes and transactions.** For each reported quest completion, grant personal rewards once to every character eligible at completion, including characters within reconnect grace. Credit the listed quest cash once per party to the shared wallet. Accept personal actions and matching world outcomes from each admitted player. Settle simultaneous claims to the same loot, shop stock, and quest rewards in one shared order. A shop transaction updates the wallet, stock, and item transfer together.

17. **Revive and wipe details.** Revive requires a living teammate outside the downed state and within interaction range. Revive restores 10% of the target's maximum health, with a minimum of 1 HP. The initial defaults are no bleed-out timer and friendly fire off. A downed host continues hosting. Once a party wipe starts, lock the roster and give each player a bounded item-selection window if retention is enabled. Resolve each character's item retention and owner-only backpack drop once before the party respawns.

18. **Coordinated zone travel.** The host validates the 15-meter check against the active roster. Downed characters and characters within reconnect grace count. Show which characters block the transition. After the wall admits the host and the host reaches the game's exit trigger, travel commits for the whole party even if a guest has fallen beyond 15 meters. The source world, route, and active roster must still match. Stardust, the green, blue, red, and golden cubes, and similar transport items or events are host-only in co-op. An approved use transports the whole party to the event's destination and uses its arrival rules. A destination in the current map moves the party together without reconnecting. A rejected use leaves the item, charges, and travel state intact. Queue incoming joins during travel or a wipe, then admit new arrivals at a safe spot near the host once the party can accept them.

## Responsiveness

- Keep local movement, camera, input feedback, and presentation running while a co-op action is pending. Use Unreal prediction and smoothing for movement and keep socket I/O and durable disk work on background workers.
- Apply gameplay changes on the game thread when their inputs and current world are ready. Use exact, bounded requests and budget bulk discovery and projection across frames.
- Send player actions and lasting outcomes reliably. Settle competing claims at the host in accepted order, using one result per operation ID. Apply a confirmed inventory transfer, trade, revive, or quest change to the relevant players. Use a current baseline on late join, travel, or reconnect.
- Give gameplay traffic and action receipts priority over bulky snapshots and diagnostics. Use short-lived messages for replaceable visual and audio effects. Keep each character's latest accepted state and the shared ledger consistent through a delay or disconnect.
- Keep routine campaign changes in memory until the next checkpoint. Save on rest, travel, Save and Quit, or an explicit save request.

## Compatibility with game updates and other mods

- Discover quest offers, objective items, progress, and reward data from the host’s active stock boards and journal. Use stock acceptance and completion paths for quests with the same recipe. Publish the resulting journal and campaign changes to the party.
- Discover world items and interactables through their game classes and stock events. Match placed actors by map and actor path. Give spawned actors a host identity tied to the current visit so late join and pickup refer to the same object.
- Discover throwable equipment through the mounted asset registry and the grenade base class. Resolve each class's stock projectile reference as the class becomes available, including equipment mounted after startup. Reuse the stock throw, charge, and replication paths for compatible projectiles. Keep equipping independent of projectile support. Projectile classes with additional scripted effects need an equivalent replicated effect or a shared effect handler.
- Discover exits and arrivals through the game’s transition actors and their configured destinations. Qualify the active source and the matching arrival before moving the party. Use the same arrival and checkpoint rules for maps provided by other mods.
- Use shared integration for stock behavior inherited by new content. A scripted quest, object, or transition that introduces a new behavior can extend the relevant integration. Report unsupported actions where they occur and keep the rest of the session running.

## Implementation milestones

### Physical world loot

- Players use the stock Interact action on individual world items throughout the campaign. This applies to container contents, enemy drops, player drops, and loose items placed in a level. All nearby items remain visible together.
- The host records each item's identity, stock class, payload, and world transform. Replicas use the stock item appearance at the host's position. The host settles the first eligible pickup once, updates the recipient's inventory or the shared wallet, and removes that item for everyone.
- Use the stock container, death, drop, and item-interaction paths to discover sources by type and live world identity. A supported source type must work across authored placements without a separate per-crate, per-item, or per-map opt-in.
- Test representative stock classes and placement cases through the common paths. Cover contested pickup, multiple items in one location, late join, reconnect, travel and revisit, restart, and loading existing co-op saves. Keep structured item and claim status available for automation.

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
- **Disguise handling for individual players.** Store disguise state per character in the host campaign. Apply each disguise change only to that character's appearance and resolve NPC recognition separately for each player. The Conscript Uniform and Standard Infantry helmet passed this check in Dead Bridge. [Stock disguise trial](../tests/live_guest_armor_smoke.py)
- The active carrier drives a held physics object's position. The host validates the carrier, object, and reach for each update, and returns the object to stock physics when the hold ends. Positions while held may move abruptly or appear to pass through geometry on another peer.

## Steam title and save routing

The target is the original Steam installation and one running game process per player. The stock title offers Singleplayer and Multiplayer. Both modes use the player's existing graphics, audio, input, and other local preferences. Singleplayer uses its existing campaign save path. Multiplayer selects the private co-op campaign save directory before opening Host or Join and keeps that selection through play, checkpointing, and travel. Returning to Singleplayer requires closing the co-op network session, draining its saves, and restoring the stock title's save selection.

The stock `Find_MostRecentSaveFile` first enumerates `GetProjectSavedDirectory()/SaveGames/` for `.sav` files, then checks and loads each candidate by slot. Manage Saves also enumerates that directory. Stock save and delete paths address slots directly. The co-op route needs to cover physical enumeration and campaign slot reads and writes, and handle the stock `Config` slot explicitly so changing modes preserves the player's local preferences. Both modes can use the same campaign slot names and stock save format in separate directories.

Qualify the routing in an isolated stock replica first. In one process, create the same slot in both modes, verify the stock Continue list and each slot's contents, and delete each mode's test save independently. Then qualify in-process Host and Join on a chosen UDP port with the existing authenticated Direct IP transport. Repeat the save check after co-op travel, disconnect, and return to the stock title.

## AI operation

Expose documented, stable commands for supported actions and structured output for their results. Status should identify the role, current map, session and connection state, active and reserved roster, and save outcome as those capabilities become available. Give events timestamps and identifiers that link an action to its result. Use explicit outcomes for rejected or incomplete operations so a model can tell when a join, save, or reconnect has actually finished.

Keep operator controls local and explicitly scoped to the running mod instance. Keep credentials out of diagnostic status and logs. Run playtesting from staged game data and give each process its own user data directory. Keep the repository focused on mod code, documentation, and reproducible tooling.
