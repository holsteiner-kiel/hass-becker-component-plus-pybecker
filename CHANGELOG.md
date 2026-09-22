# Changelog

## 0.5.3

A diagnostics hardening release.

- Keep Home Assistant diagnostics downloadable when database inspection fails
- Keep diagnostics downloadable when communicator status inspection fails
- Report only exception types for failed diagnostics sections, not sensitive exception text
- Mark database and communication diagnostic sections explicitly as available or unavailable

## 0.5.2

A diagnostics and supportability release.

- Add native Home Assistant config-entry diagnostics
- Report privacy-safe connection, thread, RF queue and retry status
- Report configured cover/channel and database-unit counts without exposing rolling codes
- Keep device addresses, database paths, remote IDs and rolling-code state out of diagnostics
- Let Home Assistant fully control logging configuration

## 0.5.1

A small logging and diagnostics polish release.

- Use configured cover names in movement debug logs instead of Home Assistant's entity-name placeholder
- Make movement start/stop messages shorter and easier to scan
- Remove per-second Home Assistant state-refresh callback noise while preserving live position updates
- Keep RF communication and meaningful movement diagnostics available at debug level

## 0.5.0

A modernization and reliability milestone for the maintained Becker Home Assistant integration.

### Highlights

- Modern Home Assistant UI configuration with config entries and cover subentries
- Serial USB and serial-to-TCP connection setup
- In-UI pairing and re-pairing
- Remote-control button presses exposed as event entities
- Rolling-code-safe JSON and SQLite export/import with automatic backups
- Protection against stale rolling-code imports and configured-state rollback
- Serialized imports, RF commands and SQLite access
- Resilient USB/network reconnect behavior
- Strict setup-time connection validation
- RF queue backpressure and retry handling
- Blocking RF queue writes moved off Home Assistant's asyncio event loop
- Live cover position tracking improvements
- Automated CI and regression test coverage against current Home Assistant releases
- Explicit service targeting for installations with multiple Becker config entries
- Safer database-path validation
- Refreshed project documentation and branding

### Compatibility

The integration remains compatible with existing centronic-stick.db state. Because Becker uses rolling codes, always keep a current state backup before migrating or restoring an installation.

## 0.4.x

The 0.4.x series introduced the initial maintained-fork modernization work that is consolidated in 0.5.0, including UI configuration, database import/export, reconnect hardening, rolling-code protection, SQLite serialization and event-loop hardening.
