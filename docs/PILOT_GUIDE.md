# Five-Minute Private Pilot

## Goal

Reach one credible spend or savings insight using the customer's own metadata,
without collecting prompts, code, or provider credentials.

## Setup

For tomorrow's individual testers, use the desktop app from the new GitHub
release. Pick Windows, macOS Apple Silicon, or macOS Intel to match the laptop.
The desktop downloads include their own runtime; users do not need Python.
Open Poliora from the desktop shortcut or Applications after installation.

In the app:

1. Choose the local-history option, review the permission explanation, and scan.
2. Review the Codex and Claude Code results before approving the import.
3. Add the monthly price of each subscription under Add a tool or a bill.
4. Return to My AI activity and check the plan comparison and model mix.
5. Refresh local history a second time. Existing records should not be added twice.
6. Export a report and review it locally.

For subscription users, equivalent API value is a comparison at public token
rates, not an invoice or a promise of cash savings. Quota forecasts are estimates
based on available local evidence. Other tools may require a supported export
or helper; catalog support alone does not mean automatic history access.

The command-line alternative for developers with Python is:

```bash
python -m pip install poliora
mkdir poliora-pilot
cd poliora-pilot
poliora init --project client-name --monthly-budget 1000
poliora dashboard
```

## First Data

Choose one path:

1. Import a provider or gateway CSV from the Connections page and review the preview before confirming.
2. Run one Codex task with `poliora codex --model MODEL --sandbox read-only "TASK"`.
3. Wrap one provider SDK client with Poliora's three-line client proxy.

## First Decision

Check rate coverage and forecast confidence, then open Scenarios. Move 10-25% of
one non-critical workflow to a cheaper candidate. Treat the result as a modeled
estimate until quality and production cost are measured after the change.

## Client Output

Select Export, enter the organization and client names, and generate the report.
Review the disclosure before sharing it. The report separates tracked spend,
projected run-rate, modeled savings, and subscription activity.

## Pilot Interview

Record four answers manually: what was confusing, which number they trusted,
which action they would take, and what they would pay to repeat this monthly.
