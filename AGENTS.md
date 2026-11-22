# Automated testing

Group related gameplay changes before running live tests. Use a small set of representative smoke tests for the shared paths changed by the batch. Run them in muted, isolated replicas with copied checkpoints. Add a focused check when the batch changes a distinct path that the representative tests do not exercise.

Reserve the full automated suite for major releases or when the user explicitly requests it. Leave in-depth foreground and two-PC playtesting to the players. In release handoffs, identify the behaviors to try, the expected results, and which symptoms or details to report.
