# Five-Minute Private Pilot

## Goal

Reach one credible spend or savings insight using the customer's own metadata,
without collecting prompts, code, or provider credentials.

## Setup

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
