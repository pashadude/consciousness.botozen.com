"""Live resource-region telemetry helpers.

The package is intentionally explicit: importing it does not call Google Cloud.
Use `mutual-spec-telemetry` subcommands to initialize tables, pull Pub/Sub
events, poll Monitoring metrics, seed power proxy rows, and install views.
"""

