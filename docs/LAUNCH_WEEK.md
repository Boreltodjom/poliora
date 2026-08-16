# Poliora Launch Week

Target: a credible private-pilot release that a developer can install, connect to
one real data source, understand without assistance, and use to produce a savings
decision. Public cloud hosting, billing, and paid advertisements are outside this
week's critical path.

## Day 1: Product Shell And Packaging

- Complete the dashboard workspace navigation and plain-language guidance.
- Make `poliora dashboard` initialize and open the local app in one command.
- Split the large fine-tuning dependencies from the core install.
- Add privacy, security, license, and release documents.
- Build source and wheel packages and keep all automated checks green.

Exit: complete.

## Day 2: First Real Integration

- Add an `poliora codex` wrapper around the documented `codex exec --json` stream.
- Collect only thread ID, model ID, and token totals; never prompt or response text.
- Keep ChatGPT subscription activity separate from API-estimated dollar spend.
- Surface the supported setup command and privacy boundary in Connections.

Exit: complete. A pilot can create useful Poliora data without an API key or manual CSV.

## Day 3: Ingestion Confidence

- Add an import preview and clear column/error diagnostics for CSV users.
- Show connector freshness, latest event, and incomplete pricing warnings.
- Add a compact setup checklist for empty workspaces.

Exit: complete. A non-expert can preview all row errors before any data is written.

## Day 4: Decision And Report Polish

- Compare multiple routing candidates and label assumptions clearly.
- Add pilot/client name, reporting period, executive summary, and print review.
- Ensure every savings claim distinguishes estimate from measured savings.

Exit: complete. An agency can brand and share a report without manually editing HTML.

## Day 5: Release Validation

- Install the built wheel into a clean Windows environment and run the full journey. Complete.
- Validate clean Linux and Windows installs in CI. Workflow added; first hosted run remains.
- Test empty, sample, imported, unpriced, over-budget, and malformed-data states.
- Capture desktop and mobile screenshots and check overflow and keyboard navigation.

Exit: local checks complete; hosted Linux/Windows matrix must pass before publishing.

## Day 6: Private Pilot Onboarding

- Prepare one five-minute setup guide and a realistic fictional demo workspace.
- Onboard up to three agencies, consultants, or small AI teams personally.
- Observe installation and first interpretation without coaching unless blocked.

Exit: at least one external user reaches a credible cost or savings insight.

## Day 7: Release And Learn

- Fix only launch-blocking defects found during onboarding.
- Publish the private-pilot package and release notes.
- Post a concrete before/after analysis on the channels already available.
- Record activation, imported requests, report exports, and pilot conversations
  manually; do not add invasive telemetry for launch.

Exit: pilot released with evidence for the next product decision.

## Not This Week

- A publicly exposed local dashboard.
- Multi-tenant cloud hosting, subscriptions, or payment processing.
- Paid ads before one external user completes the value journey.
- Unsupported scraping of Cursor, Claude Code, Antigravity, or Codex internals.
- Claims that model routing preserves quality without an evaluation result.
