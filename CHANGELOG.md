# Changelog

All notable Poliora changes are documented here.

## 0.2.1 - First-Run Desktop Experience

### Added

- A consent-based local-history flow in the dashboard: review Codex and Claude Code token, model, plan, and quota metadata before adding it to the local workspace.
- A no-Python Windows installer that creates Start menu and desktop access for Poliora.

### Changed

- The public onboarding and PyPI documentation now lead with `poliora detect`, not the availability-only `poliora scan` command.
- Repeated local-history imports skip usage already recorded in the workspace.
## 0.2.0 - Local Usage Detection

### Added

- `poliora detect` reads usage that Claude Code and the Codex CLI already record
  in their own local session logs: token counts, model mix, plan type, and quota
  utilization. No account credential, no network call, and no provider contact.
- Subscription turns are recorded at zero spend alongside an
  `equivalent_api_cost_usd` figure — what the same work would have cost at
  published API rates. The gap between a flat fee and that number is the basis
  for a plan right-sizing decision.
- Time-scoped pricing: a model may carry several rate schedules, and a usage
  event is priced at the rate in effect when it happened rather than today's.
  Re-running a report no longer reprices historical spend.
- Rate provenance: every default price records the date it was verified, its
  source, and a confidence level, so an unconfirmed estimate is never mistaken
  for a published rate.
- Routing simulations warn when the target model's rate is scheduled to change
  or was never confirmed, instead of quietly projecting savings that will not
  materialize.

### Fixed

- Corrected published rates that were materially wrong: GPT-5.6 Luna was priced
  5x too high, GPT-5.6 Terra 25% too high, Gemini 3.6 Flash 2.5x too low, and
  Grok 4.5's cached-input rate 67% too high.
- Added missing current models, including Claude Opus 5, Claude Haiku 4.5,
  Grok 4.6, Gemini 3.7 Flash, and the GPT-5.4 mini/nano/pro and Cyber tiers.
- CSV imports are now priced at the rate in effect on each row's own timestamp
  rather than at the rate on the day of import.
- The local dashboard now validates `Host` and, on state-changing requests,
  `Origin`. This closes a cross-site request forgery path that let any page the
  user visited inject usage or overwrite contract rates, and a DNS-rebinding
  path against the loopback server.

### Changed

- Retired and deprecated models keep open-ended rates so historical imports
  still price correctly; a rate window is now only closed when the successor
  rate is known, because an unpriced event costs $0.00 and $0.00 reads as free.
- Test suite grown from 39 to 316 cases, covering pricing schedules, catalog
  invariants, storage integrity, report aggregation, dashboard request guards,
  and local log parsing.

## 0.1.1 - Launch Candidate

- Replaced inferred local spend and activity with consent-safe tool availability checks.
- Added recovery for damaged workspace configuration files and atomic config writes.
- Added isolated Windows and macOS package installers and reproducible browser smoke checks.
- Clarified the public calculator as a best-case planning scenario and removed unverified routing claims.

## 0.1.0 - Private Pilot

- Added a local AI spend dashboard with dedicated Overview, Connections,
  Scenarios, and Models & Rates workspaces.
- Added token, cache, reasoning, tool-charge, customer, workflow, and trace-aware
  usage accounting.
- Added an editable model catalog, price provenance, custom contract rates, and
  optional provider model discovery.
- Added spend forecasts, budget checks, anomaly detection, recommendations,
  routing simulations, saved scenarios, CSV import, and standalone HTML reports.
- Added a savings proof ledger, evidence grading, quality-gated rollout states,
  and measured-savings reporting.
- Reworked the dashboard into a responsive AI cost operations console with a
  cost fingerprint, evidence seal, and decision lab.
- Refreshed the verified catalog with current GPT-5.6 and Claude releases,
  including rate provenance and the Claude Sonnet 5 introductory-rate expiry.
- Added opt-in connection consent records and provider/client SDK capture helpers.
- Added a supported `poliora codex` wrapper for content-free Codex CLI token capture.
- Made the dashboard a one-command first run and moved the legacy machine-learning
  stack to the optional `training` install extra.
- Added a local privacy statement, security policy, and MIT license.
- Added repeatable desktop/mobile browser smoke tests and distribution-content
  checks to the release gate.
