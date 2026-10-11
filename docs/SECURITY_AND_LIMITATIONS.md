# Security and limitations

- Loopback-only local application; trusted Host/Origin and per-process request tokens protect writes. This is not a remotely hosted multi-user service.
- API credentials remain in process memory. Session keys expire on server shutdown; no key is included in the package. Explicitly supplied environment keys remain compatible, but are not needed for normal Competition setup.
- Cloud disclosure and user submission remain required. Only the submitted description, relevant object names/references and necessary pending Meaning context are sent; the entire Financial World, balance table and History are not uploaded automatically.
- Models produce non-authoritative proposals. Review, exact authorization, Apply, immutable receipts, stale rejection and idempotency remain separate boundaries.
- Unknown is not zero. Native currencies are separate; no FX, recurring-cash-flow engine, affordability recommendation or bank execution is provided.
- Expected and conditional income is not current cash or guaranteed payment. Dates not safely interpreted must be supplied explicitly.
- Competition state is isolated from the personal environment, including database/WAL/SHM, drafts, preferences, browser profile, logs and backups. Competition clear archives only the current instance; it never silently deletes personal History.
- A process or user with local administrator privileges is outside this application's isolation guarantee. Tests do not constitute penetration testing or a security certification.
- General Meaning P1 remains open. A fixed successful competition journey does not guarantee arbitrary equivalent language. Eight sealed independent holdouts are not included or claimed as passed.
- A known P2 refresh race may reopen an already-applied read-only receipt after closing; financial idempotency remains protected.
- Source export deliberately excludes personal/Competition data, private inputs, historical prototypes, raw model responses, benchmark corpora, local caches and machine configuration. Full historical Core verification is performed in the complete source repository, separately from the curated exported suite.
