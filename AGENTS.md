# Automated testing

For routine changes and incremental release candidates, run only the tests for the changed behavior and any callers or shared paths it can affect. For live gameplay changes, use a copied checkpoint near the affected mechanic and run its targeted smoke test in muted replicas.

Reserve the full suite for major releases or when the user explicitly requests it.
