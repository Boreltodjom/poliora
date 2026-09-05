# Changelog

All notable Poliora changes are documented here.

## 0.4.0 - Capacity, Attribution, and Advice

On a flat plan the scarce resource is capacity, not money. The fee is paid
whether it is used or not; what varies is how much work fits before the window
closes. This release answers that question and the two that follow it: what is
consuming the capacity, and what to do about it.

### Added

- `poliora runway` projects when the current limit window runs out. The ceiling
  is measured from refusals the tool already recorded -- a refused request is a
  labelled observation of where the limit sits -- and combined with a median
  when several exist. With no refusal and no supplied plan limit, it reports
  consumption and burn rate and refuses to name a wall time.
- History-relative context, so the first run is useful before any limit has
  been hit: "this window is heavier than 64% of the last 30 days, and sits at
  29% of your busiest". A limit is not the only reference point, and a person's
  own history is available immediately.
- `poliora workflows` attributes usage to the projects that caused it. Only the
  project folder name and token counts are read, never file contents or code.
- `poliora advise` combines all three into what to do today, and says nothing
  when the measurements do not justify saying something.
- `poliora statusline --install` wires the capacity line into Claude Code,
  preserving any status line already configured rather than overwriting it.

### Fixed

- Project names encoded from Windows paths lost hyphens, turning "auto-doc"
  into "doc". Windows encodes a separator as "--" while POSIX uses a single
  "-", so the two encodings are now decoded separately.
- A corrupt capacity cache raised AttributeError instead of degrading, taking
  down the status line rather than rebuilding.

### Changed

- The status line is 29x faster: 17.71s to 0.61s, of which 0.60s is Python
  interpreter start-up. Achieved by skipping files untouched within the window,
  caching derived ceilings, skipping json parsing on lines that cannot hold the
  wanted field, and caching the rendered line for 60 seconds.

## 0.3.0 - Daily Companion

### Added

- A private Plan Stack in the desktop app. People can add the monthly AI plans
  they actually pay for and compare each one with their approved local usage.
- An honest subscription-value view: public API-rate equivalents are labelled as
  a comparison, never as an invoice or an automatic cancellation instruction.
- A consented in-app history refresh for Codex and Claude Code, with optional
  automatic refresh every fifteen minutes after a person has approved it.
- One-click Antigravity helper setup from the desktop app. It records only
  activity that Antigravity officially exposes; it does not claim token or
  billing visibility where none exists.

### Fixed

- Subscription-included local sessions are now counted correctly instead of
  appearing as unexplained zero-cost activity.
- Dashboard recommendations no longer advertise `$0.00/mo` savings for flat
  subscription usage; they describe the capacity or plan action instead.
- The packaged desktop app can execute its own Antigravity hook without Python
  being installed or a terminal command being required.
## 0.2.3 - Launch Copy Accuracy

### Fixed

- The homepage sample output showed an equivalent API value that the shipped
  pricing catalog cannot produce ($153.00 for 4.2M tokens on `gpt-5.6-sol`,
  whose ceiling is $126.00), and mixed one tool's request count with another
  tool's model. It now shows an engine-verified example with its token split
  and per-1M arithmetic printed alongside it, so the figure can be checked.
- The savings FAQ now states plainly that model routing saves nothing on a flat
  subscription, where the lever is right-sizing the plan instead.

### Changed

- The package summary now describes what Poliora does -- find the AI coding
  usage your tools already recorded locally -- instead of leading with the
  legacy carbon and fine-tuning framing. Keywords retargeted accordingly.
- The headline statistic no longer leads with a savings percentage that most
  visitors, being on flat subscriptions, cannot realize.
- Download cards now set expectations for the unsigned Windows installer and
  un-notarized macOS app, so the first-run security warning is not mistaken
  for a malware signal.

## 0.2.2 - Native Desktop Polish

### Added

- A native Poliora application window on Windows and macOS. The dashboard is now contained inside the installed app instead of opening a browser.
- A branded Poliora executable, installer, Start menu entry, and desktop shortcut icon.
- An immediate, consent-based local-history preview on the first desktop launch. Detected metadata is only saved when the user approves it.

### Changed

- A second launch now leaves the existing Poliora desktop window in place instead of opening a browser tab.
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
