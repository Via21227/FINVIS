# Platform acceptance

## macOS

Primary competition platform. This release uses the existing `.command` entry and one shared Core. Final exported launcher and synthetic Chrome results are recorded in the source release report; release verification does not imply live-model semantic acceptance.

Storage: `~/Library/Application Support/FINVIS Competition/instances/<id>/`, including state, isolated browser profile, logs, backups and temporary files. Personal FINVIS uses the separate `FINVIS/UserWorld` root. Startup reopens the selected instance; `--new` explicitly creates another instance without deleting earlier ones.

## Windows — runtime UNVERIFIED

Native entry: `Start FINVIS Competition.cmd`, relative to its own folder, UTF-8 mode, Python launcher detection, no shell-specific POSIX command or administrator requirement.

Storage: the Windows Local AppData known folder, normally `%LOCALAPPDATA%\FINVIS Competition\instances\<id>`. Personal storage is a separate `%LOCALAPPDATA%\FINVIS\UserWorld`. Windows storage requires a fixed local NTFS volume, current-user private ACLs, no reparse path or hardlink aliases, exclusive authority handles and exclusive local socket binding. Namespace changes use same-volume write-through publication; SQLite WAL/FULL and the existing initialization/reset recovery order remain intact.

Static and mocked platform checks are not native Windows verification. Native ACL inheritance, lock interoperability, restart/reset recovery and filesystem durability have not been demonstrated on Windows. No Windows host was available for this release work. The provided native launcher remains an acceptance candidate, not a claim of cross-platform completion.

Before Windows acceptance, use isolated synthetic data to verify:

1. Empty English Competition startup, correct local Chrome profile and exact instance reopen.
2. Session-key connection/disconnect/restart without any inference.
3. EUR 3,000 review/authorization, supported Future/Simulation and EUR 944 actual payment; identical receipts after restart and repeated Apply.
4. Competing process/port rejection, unsafe ACL/reparse/network-path rejection.
5. Three-confirmation clear preserves the archived state and leaves a separate personal and second Competition instance unchanged.
6. Console shutdown, relaunch and failure recovery. Real interrupted-write/power-loss testing remains a separate durability limitation.

No source license or public GitHub destination is selected automatically. No Feature Freeze.
