# Disk writes

Keep gameplay and networking updates in memory by default. Commit campaign changes at the game's normal save boundaries such as resting, Save and Quit, and level transitions, or when an operator explicitly requests a save. A brief disconnect or an ordinary pickup, drop, or interaction should leave the save files alone.

Use a gameplay-time disk write only when an operation requires durable storage to work correctly. Keep the write off the game thread where possible and document the dependency beside the operation. Avoid recurring file writes for diagnostics and interprocess communication when the in-memory bridge can carry the same information.

# Automated testing

Group related gameplay changes before running live tests. Use a small set of representative smoke tests for the shared paths changed by the batch. Run them in muted, isolated replicas with copied checkpoints. Add a focused check when the batch changes a distinct path that the representative tests do not exercise.

Reserve the full automated suite for major releases or when the user explicitly requests it. Leave in-depth foreground and two-PC playtesting to the players. In release handoffs, identify the behaviors to try, the expected results, and which symptoms or details to report.
