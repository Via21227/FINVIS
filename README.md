# FINVIS Competition Release Candidate

FINVIS helps you explore how a possible purchase or life event changes your money, then compare your estimate with what actually happened. Natural language proposes facts. Deterministic calculations show the consequences. Only your explicit review and authorization can change the recorded Financial World.

**Primary entry: Competition Demo.** This package uses one FINVIS codebase and a persistent, isolated Competition instance. It starts empty. It contains no personal financial records, API key, archived model responses or benchmark corpus.

## Requirements and status

- Python 3.9 or newer, with its standard library and a supported SQLite build: 3.51.3+, 3.50.7–3.50.x or 3.44.6–3.44.x. The launcher checks prerequisites; use a current official Python distribution when your bundled SQLite is older. No Python package installation is required.
- Google Chrome for the Competition launcher and its isolated browser profile.
- A network connection and your own OpenAI API key only when you deliberately use the cloud provider. API inference may incur charges.
- macOS is the primary release platform. See `docs/PLATFORM_STATUS.md` for exact Windows status; a batch file alone does not establish Windows support.
- This is a bounded competition candidate. General natural-language reliability remains limited; arbitrary paraphrases are not guaranteed. No Feature Freeze is declared.

## Start on macOS

1. Extract the entire package to a normal writable folder.
2. Double-click **启动 FINVIS 比赛版.command**. Keep its Terminal window open.
3. The launcher creates or reopens the Competition instance and opens its local Chrome window.
4. Stop the service with Control-C in that Terminal. Restarting retains the same Competition records.

If Finder does not execute the launcher, open Terminal in the extracted folder and run:

```sh
python3 -B -m finvis.competition_launcher
```

Do not bypass macOS security settings or run FINVIS as administrator. Install Python from its official distribution if Python is missing. An old or damaged instance fails closed; do not delete its files to force startup.

## Windows

**Windows runtime is UNVERIFIED.** A native startup path and defensive Windows storage adapter are included for Windows acceptance testing; use the verified macOS path for the competition until a real Windows run is accepted.

On Windows 10/11, install Python 3.9+ with a supported SQLite build and Google Chrome, then double-click **Start FINVIS Competition.cmd**. The launcher uses `py -3` or `python`, starts the same Core, and opens a separate Chrome profile. Keep the console open; press Control-C to stop. It never runs as administrator or resets an existing instance.

Storage uses the native Local AppData folder (normally `%LOCALAPPDATA%\FINVIS Competition`). Only a fixed local NTFS volume with private permissions is supported. Network shares, redirected/reparse paths, FAT/exFAT and unsafe existing permissions fail closed. See `docs/PLATFORM_STATUS.md` for the outstanding native acceptance checklist.

## Connect OpenAI

Open **AI Connection**, select OpenAI, paste the key into the masked API Key field, acknowledge the cloud privacy notice and click Connect. The key is held only in the server process memory and disappears when the server stops. It is not saved in browser storage or the financial database. Disconnect or replace it in the same panel.

Connection configuration is not a paid model call and does not prove that a key has valid billing or model access. A cloud call requires deliberate provider consent and submitting an input. FINVIS does not silently fall back to another provider or automatically retry paid calls. Do not put keys in this repository, screenshots or issue reports.

## Supported competition journey

Use synthetic information only for this demo. Review the understood facts at every step.

1. Start with an empty Dashboard. Enter `Bank account balance EUR 3000.` Review, authorize and apply it.
2. Enter `I'm planning a graduation trip in November. It includes a €900 economy flight, a €1,200 hotel, and an optional €600 camera. I haven't paid for any of them yet.` Review the parent and components, then open Future.
3. Set payment months explicitly to October, November and December using the existing timing controls. When a year is material, supply it; an event month is not automatically a payment date.
4. Add a possible EUR 1,000 reimbursement that has not arrived. Keep it conditional. Explicitly set its receipt month before using the timeline.
5. Add a EUR 1,400 premium flight as an alternative to the EUR 900 economy flight. Choose one flight, never both.
6. Start simulation. With all three expenses selected: EUR 3,000 − 2,700 = **EUR 300**; including the conditional reimbursement gives **EUR 1,300 if it arrives**. This is not guaranteed cash.
7. Open Comparison: the flight cost difference is **EUR 500**. Return to Adjust choices to change selection, then simulate again. Existing results stay frozen until you deliberately rerun.
8. Open Why or ask about November. Explanations refer to the indicated simulation snapshot, which may be an earlier selection.
9. Report the economy flight bought for EUR 944. Confirm which option the report concerns, review its original EUR 900 estimate, authorize the exact payment and Apply. The recorded balance becomes **EUR 2,056**. Inspect the receipt and Records/History.
10. Open Mobile Preview. It shows the same authorized state.

Meaning failures and questions preserve recoverable input. Do not keep submitting repeatedly if interpretation fails. Clarify using supported controls and inspect the actual result; do not claim general language acceptance from this demonstration.

## Reset and data isolation

The Competition-only clear action requires three deliberate steps. It archives the current instance's state before starting empty; it does not clear personal FINVIS or another Competition instance. Never manually delete a database, WAL, receipt or backup. Restart is not a reset. See `docs/SECURITY_AND_LIMITATIONS.md`.

## Verification

From this package directory:

```sh
python3 -B release_tools/verify_release.py
python3 -B release_tools/run_tests.py
```

On Windows, use `py -3 -X utf8` where appropriate. The curated full regression is currently verified on macOS; Windows test fixtures and runtime still require native acceptance. The curated suite uses Python temporary directories, synthetic proposals and fake transports; it makes no paid calls. Some UI unit tests require an existing Node.js installation. Helpers are included for imports; **do not use unittest discovery as a substitute for the curated runner**: historical Core gates depend on evidence intentionally omitted from this package.

Optional real-Chrome E2E uses an independently installed Node.js/Playwright development environment:

```sh
node release_tools/chrome_e2e.cjs
```

Set `FINVIS_PLAYWRIGHT_MODULE` to an existing Playwright module path if needed, `FINVIS_PYTHON` to the intended Python executable and `FINVIS_CHROME_EXECUTABLE` if Chrome discovery needs an override. The harness runs from this exported package, creates only temporary synthetic stores and preserves output screenshots/report in a temporary folder. Its hand-authored proposals are explicitly **not live model semantic acceptance**.

## Troubleshooting

- **Cloud not enabled:** use AI Connection and confirm cloud disclosure. No Terminal key configuration is needed.
- **Key rejected / no model access:** check your OpenAI project permissions and billing without posting the key publicly.
- **Understanding failed:** original text remains recoverable; general Meaning limitations are known. Review or clarify rather than assuming a financial update happened.
- **One undated item:** set the affected payment/receipt month explicitly; unknown timing is not silently guessed.
- **Different Why value:** check whether the panel describes an earlier frozen simulation; rerun after changing choices.
- **Receipt reopens once:** a known refresh race can reopen a read-only receipt. Close it again. This does not create another payment.
- **Port or storage error:** preserve the existing instance and diagnostic category. Never terminate an unrelated process or delete state to recover.

See `RELEASE_MANIFEST.json` for the exact files and hashes. Publication destination, visibility and source-license choice remain subject to owner approval.
