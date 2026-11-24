# Disk writes

Keep gameplay and networking updates in memory by default. Commit campaign changes at the game's normal save boundaries such as resting, Save and Quit, and level transitions, or when an operator explicitly requests a save. A brief disconnect or an ordinary pickup, drop, or interaction should leave the save files alone.

Use a gameplay-time disk write only when an operation requires durable storage to work correctly. Keep the write off the game thread where possible and document the dependency beside the operation. Avoid recurring file writes for diagnostics and interprocess communication when the in-memory bridge can carry the same information.

# Automated testing

Group related gameplay changes before running live tests. Use a small set of representative smoke tests for the shared paths changed by the batch. Run them in muted, isolated replicas with copied checkpoints. Add a focused check when the batch changes a distinct path that the representative tests do not exercise.

Group compatible backlog fixes into a candidate before starting live verification. Build and stage it once, then start one private host and guest and run the selected checks on those same processes. Use fresh campaigns in the open processes when checks need clean starting state. Relaunch the executable when a check requires a cold start or the existing processes cannot be recovered. The portable smoke runner supports combining `--stock-campfire`, `--stock-melee-edges`, and `--stock-food` in one Observatory campaign, and running `--stock-unequip` in a separate campaign on the same pair.

Reset or verify each check's starting state and record its result and timing. Continue independent checks after a failure when they can reestablish their starting state. Record blocked checks with the earlier failure that prevented them from running. Select checks affected by the candidate and report the number of executable launches alongside the gameplay results.

When related checks span maps, start one campaign and arrange the checks in travel order. The portable smoke runner can combine `--stock-host-prop-observer` and `--stock-warehouse-switch` from Observatory through Tutorial Dock. For Crossroads performance and world state, combine `--crossroads-profile --stock-critters --stock-lingering-teleport --stock-placed-item` for one Observatory-to-Crossroads campaign. The individual results include setup failures and identify checks blocked by an earlier failure. Use the recorded results to rerun the affected check after a fix.

Reserve the full automated suite for major releases or when the user explicitly requests it. Leave in-depth foreground and two-PC playtesting to the players. In release handoffs, identify the behaviors to try, the expected results, and which symptoms or details to report.
