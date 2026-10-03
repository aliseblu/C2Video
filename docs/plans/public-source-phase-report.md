# Public source + local editorial phase report

Date: 2026-09-11

## Scope

- Added account-free RSS, Hacker News, and GitHub candidate aggregation.
- Added deterministic local Curation and Script generation.
- Made content-card metrics and Studio health status source-aware.
- Kept Grok OAuth and X MCP as optional source providers.
- Added bounded Edge TTS retries and macOS/Windows local speech fallback.

## Verification

- `ruff check .`: passed.
- `pytest -q`: 73 passed; two third-party deprecation warnings.
- `npm test -- --run`: 2 passed.
- `npm run build`: passed.
- Real public fetch: RSS, Hacker News, and GitHub all returned candidates.
- Final real live Agent Run: `run_e44278a550974e47ac508399440b59a3` completed.
- Output video: 1080×1920, 25.57 seconds, H.264/AAC, QC passed. The only
  informational warning records that this run selected two items from the live pool.
- macOS Mandarin speech fallback: generated and probed a 1.81-second MP3.

## Browser QA

The changed Settings route was checked with real Chromium at 1440×1000 and 390×844 in both configured states. No horizontal overflow was detected.

- `artifacts/ui-qa/2026-09-11/settings-public-ready-desktop.png`
- `artifacts/ui-qa/2026-09-11/settings-public-ready-mobile.png`
- `artifacts/ui-qa/2026-09-11/settings-grok-needs-login-desktop.png`
- `artifacts/ui-qa/2026-09-11/settings-grok-needs-login-mobile.png`

## Defects found and resolved

- Public GitHub search admitted weakly related repositories: added name/description/topic relevance filtering and compacted raw payloads.
- CLI Curation bypassed the new local provider: routed `provider=local` through deterministic scoring.
- Source cards displayed X-specific metrics: replaced them with GitHub, Hacker News, or RSS labels.
- Studio always labelled live setup as X login: added source and editorial-provider status rows.
- Edge TTS had an intermittent connection failure: added bounded retry and OS-native speech fallback.
- Public-source summary could truncate to an isolated English character: replaced it with stable Chinese source headlines.
- Compact GitHub narration could exceed a 30-second target: limited the short-form
  script to one representative topic and verified a 25.57-second final render.
- macOS Python ignored hidden editable-install `.pth` files: deployed the project as
  a regular local wheel so the CLI also starts outside the repository directory.
- Homebrew's default FFmpeg build lacked the `drawtext` filter used by Demo Mode:
  final verification and deployment use the installed `ffmpeg-full` build.

Unresolved blockers: none.
