# Disk writes

Keep gameplay and networking updates in memory by default. Commit campaign changes at the game's normal save boundaries such as resting, Save and Quit, and level transitions, or when an operator explicitly requests a save. A brief disconnect or an ordinary pickup, drop, or interaction should leave the save files alone.

Use a gameplay-time disk write only when an operation requires durable storage to work correctly. Keep the write off the game thread where possible and document the dependency beside the operation. Avoid recurring file writes for diagnostics and interprocess communication when the in-memory bridge can carry the same information.

# Retiring superseded code

When a shared gameplay path replaces a specialized patch, trace both paths through the normal game entry point before removing the old code. Retire superseded dispatch cases, hooks, fixtures, and documentation together. Keep coverage for the original report through the shared path. Keep a dedicated handler only when it still implements distinct stock behavior or supports a documented compatibility path. Development candidates may drop support for earlier co-op save schemas. When changing the schema, reject unsupported checkpoints, keep the existing files intact, and document which builds require a fresh campaign. Begin maintaining save compatibility with the first official release.

# Local gameplay and shared outcomes

Run stock input, personal gameplay, and presentation on the acting player's machine. Prefer reporting the stock result over forwarding input for the host to execute again. Give admitted guests the same trust as the host for their own inventories, equipment, stat changes, and actions. Preserve built-in traits and effects and discover supported content through stock classes and events.

Use the host to relay outcomes and settle conflicts over shared resources such as the same pickup, trader stock, or a quest reward. Scope reports to the current visit, source, and operation. Make retries return the same result, preserve exact item data, and reconcile only the provisional changes a conflicting outcome affects. Keep local controls responsive while the network is catching up.

When adding or changing an integration, trace both players' normal game input through local feedback, the network message, and the other player's applied result. Record any host round trip that still gates normal play in the [gameplay path audit](docs/DESIGN.md#gameplay-path-audit). Test simultaneous claims and late or repeated results where the action can race.

# Avatar animation

Use the base game's existing poses and animations whenever they fit the action. For remote avatars, check the stock NPC animation path first for weapon hold, aim, fire, reload, melee, and other actions NPCs already perform. Feed the matching stock states and equipment data into that path and reuse its assets as authored. Check idle, aim, and action transitions with the relevant equipment and avatar rigs before adding custom animation or pose adjustments.

# Automated testing

Reuse the existing isolated game replicas. For new replicas on the same volume, pass `stage --stock-cache` with the project-local shared stock cache so the IoStore archive, radio tracks, and TV clips use hard links. The cache has `SilverFish-Windows.ucas` at its root and stores media under its original `SilverFish/Content` paths. Keep mods, configuration and user data private to each replica. Keep the shared stock copy separate from the live Steam installation.

Group related gameplay changes before running live tests. Use a small set of representative smoke tests for the shared paths changed by the batch. Run them in muted, isolated replicas with copied checkpoints. Add a focused check when the batch changes a distinct path that the representative tests do not exercise.

Group compatible backlog fixes into a candidate before starting live verification. Build and stage it once, then start one private host and guest and run the selected checks on those same processes. Use fresh campaigns in the open processes when checks need clean starting state. Relaunch the executable when a check requires a cold start or the existing processes cannot be recovered. The portable smoke runner supports combining `--stock-campfire`, `--stock-melee-edges`, and `--stock-food` in one Observatory campaign, and running `--stock-unequip` in a separate campaign on the same pair.

Reset or verify each check's starting state and record its result and timing. Continue independent checks after a failure when they can reestablish their starting state. Record blocked checks with the earlier failure that prevented them from running. Select checks affected by the candidate and report the number of executable launches alongside the gameplay results.

When related checks span maps, start one campaign and arrange the checks in travel order. The portable smoke runner can combine `--stock-host-prop-observer` and `--stock-warehouse-switch` from Observatory through Tutorial Dock. For Crossroads performance and world state, combine `--crossroads-profile --stock-critters --stock-lingering-teleport --stock-placed-item --stock-mushrooms` for one Observatory-to-Crossroads campaign. Add `--stock-crate-ring` to this route to exercise a guest Interact before the guest crate cache is populated. The individual results include setup failures and identify checks blocked by an earlier failure. Use the recorded results to rerun the affected check after a fix.

Reserve the full automated suite for major releases or when the user explicitly requests it. Leave in-depth foreground and two-PC playtesting to the players. In release handoffs, identify the behaviors to try, the expected results, and which symptoms or details to report.

# Documentation maintenance

During the first repository update of each working day, review the README, player guide, installation steps, and known issues against the published candidate and recent test evidence. Update current guidance and mark older test results with their build. Refresh the release download index whenever published candidates change.
