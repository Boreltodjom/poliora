# Poliora Launch Plan

## Product Position

Poliora is a local-first AI spend control room. It helps teams discover where
AI money is going, test cheaper routes safely, enforce budgets, and share clear
evidence with clients or leadership. Carbon and efficient fine-tuning remain
useful differentiators, but cost control is the first commercial promise.

## Release Boundaries

### Poliora v1: Private Pilot

- Local dashboard and CLI work from a `.poliora` workspace.
- Python SDK, CSV import, provider response capture, cost reports, budget gates,
  model routing simulations, and HTML reports are usable without an account.
- Data stays on the customer's machine by default.
- The target user is an agency, small AI product team, or consultant running a
  paid pilot with one to five projects.

### Poliora Cloud: Hosted Pilot

- Only begins after v1 has been tested with real usage data.
- Requires authentication, organization and project isolation, encrypted secrets,
  rate limits, audit logging, backups, and a hosted database.
- The local dashboard must never be publicly exposed as a substitute for this.

## Phased Delivery

### Phase 1: Trusted Data Foundation

Status: substantially complete locally

- Versioned model catalog and editable pricing registry are implemented.
- Add catalog fields for provider, model ID, display name, lifecycle, capabilities,
  context limits, input/output/cache/batch pricing, source URL, and verified date.
- Keep customer pricing overrides in the local workspace; do not overwrite contract rates.
- Show unpriced models that appear in usage data and make them easy to price.
- Usage events capture cache tokens, reasoning tokens, tool charges, request status,
  trace ID, and provider request ID through the SDK, CLI, and CSV import.
- Local writes are atomic and protected for concurrent application workers.
- Automated tests cover pricing, import, provider response capture, reports, catalog sync,
  dashboard routes, and concurrent writes.

Ship criteria:

- A user can record any known model or a custom model without losing data.
- Every cost estimate shows whether it came from a catalog rate, a contract override,
  or a supplied provider cost.
- Concurrent logging cannot corrupt the usage file.

### Phase 2: Decision-Grade Dashboard

Status: substantially complete for private pilot

- Date ranges, daily spend trend, provider, project, operation, and customer views are implemented.
- Budget progress, unpriced usage alerts, trace coverage, last usage time, rate coverage,
  conservative daily-spend anomaly detection, and forecast confidence are implemented.
- The dashboard has a searchable model catalog with pricing, cache rates, lifecycle,
  capabilities, verification date, and provider-source provenance. Browser-based editing
  of custom prices remains.
- Dedicated Overview, Connections, Scenarios, and Models & Rates workspaces are implemented.
- Routing simulations preserve observed traffic assumptions and can be saved locally.
- The savings proof ledger moves decisions through proposed, testing, validated,
  rolled-out, or rejected states and counts measured value only after rollout.
- A workspace evidence grade names the next action needed before a savings claim
  becomes defensible.
- Printable HTML export, report period selection, pilot branding, and estimate disclosures are implemented.
- Data-quality indicators and CSV preflight diagnostics are implemented in the dashboard.

Ship criteria:

- A manager can answer who spent what, on which workflow, and what concrete action to take.
- An agency can prepare a credible client savings report without editing JSON files.

### Phase 3: Integrations And Automation

Status: in progress

The connector product direction is documented in `docs/COMPANION_VISION.md`.
Every connector must be opt-in, use supported provider surfaces, collect usage
metadata rather than customer prompts or code, and explain its freshness and
permission limits.

- Strengthen OpenAI and Anthropic capture for current response and cached-token fields.
- Generic OpenAI-compatible response and client wrappers support providers such as
  DeepSeek and compatible gateways without a provider-specific SDK adapter.
- Gemini Generate Content response and client capture are implemented, including cached,
  thought, and tool-prompt token accounting. Provider billing/usage import adapters
  remain for later.
- A supported Antigravity plugin records privacy-preserving invocation activity. Its
  documented hook does not expose model or token totals, so this data remains non-dollar.
- Local Companion connector registry and consent/status screen are implemented.
- Add Cursor Admin API and Claude Code Analytics imports for authorized team
  administrators.
- Add webhook or scheduled CSV ingestion for pilots that cannot change application code.
- Add GitHub Actions examples for budget gates and regression checks.
- Add budget notifications through email or Slack only after recipients and opt-in settings exist.

Ship criteria:

- First-time value can be reached through CSV import or a three-line SDK wrapper.
- A team can fail a build or receive a notification before a budget surprise.

### Phase 4: Launch Hardening

Status: in progress

- LICENSE, security policy, local privacy statement, and release notes are implemented.
- Repair encoding issues in legacy training and benchmark modules.
- The fine-tuning stack is now an optional `training` extra; the core wheel is about 81 KB.
- Add coverage for local dashboard routes, empty data, malformed imports, unknown models,
  pricing overrides, and concurrent writes.
- Source and wheel packages build successfully; a clean Windows first-run is verified.
- A GitHub Actions matrix now covers Windows and Linux on Python 3.11-3.14; the first hosted run remains.
- Create a demo workspace with realistic but non-sensitive data and screenshots.

Ship criteria:

- `pip install poliora` gives a small, documented cost-control product.
- No API key or customer prompt content is collected by default.
- A pilot customer can install, import, understand their spend, and export a report unaided.

### Phase 5: Hosted Product

Status: later, after pilot evidence

- Build a separate web/API service rather than exposing the local dashboard.
- Use organization-scoped data, PostgreSQL, migrations, encrypted secrets, server-side
  authorization, rate limiting, audit logs, and backup/retention policies.
- Make integrations push usage to an authenticated ingestion API.
- Add billing only after customers have repeated value and clear willingness to pay.

## Model Catalog Policy

- Ship only provider model IDs and rates verified from official provider documentation.
- Preserve prior catalog entries with lifecycle labels such as active, legacy, preview, and retired.
- Treat customer-entered models as custom until a trusted catalog source confirms them.
- Never silently replace a customer's negotiated price with a public list price.
- Track catalog source and verification date so users can judge freshness.

## Commercial Pilot

1. Recruit three to five design partners: agencies, AI consultancies, and small SaaS teams.
2. Import one month of their usage or wrap one workflow with the SDK.
3. Deliver a spend report and one safe routing experiment.
4. Charge for ongoing monitoring and reporting only after measured value is visible.

## Current Priority

Follow `docs/LAUNCH_CHECKLIST.md`. The launch path is a private pilot, not a public
multi-tenant service: ship one no-key local tool integration, complete import
diagnostics and client report polish, validate the wheel in clean environments,
then onboard the first three design partners manually.

## Deferred Credentials And External Accounts

Status: deliberately deferred

- Do not require any provider API key to use the local dashboard, reports, CSV import,
  routing simulator, or built-in catalog.
- Provider model discovery, billing exports, notifications, hosted deployment, and paid
  advertisements will be configured only after the core local product is ready.
- When credentials are added, use environment variables or a production secret manager;
  never write them to `.poliora`, source code, reports, or browser JavaScript.
