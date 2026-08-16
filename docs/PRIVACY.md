# Poliora Local Privacy Statement

Effective: July 17, 2026

Poliora v0.1 is local-first software. The dashboard server, usage log, pricing
overrides, connection approvals, and saved scenarios run from and remain in the
user's chosen local workspace by default.

## Data Poliora Uses

Poliora can process model provider and ID, token counts, cost, timestamps,
request status, operation and project labels, optional customer or user labels,
trace IDs, and provider request IDs. These fields support spend reporting,
forecasting, anomaly detection, and routing simulations.

Poliora does not require prompts, model replies, source code, or API keys for
local cost analysis. Users should not place those values in operation, customer,
trace, or other metadata fields.

## Storage And Retention

Workspace data is stored under `.poliora` on the user's machine. Poliora does
not include telemetry or an Poliora cloud account in this release. Users control
backup, access, retention, and deletion of that directory and any exported HTML,
CSV, or JSON reports.

## Network Activity

Opening the local dashboard does not send usage data to Poliora. Network access
occurs only when a user deliberately invokes a provider-backed function, such as
model catalog synchronization, or follows an external documentation link.
Provider SDK calls wrapped by Poliora continue to contact the provider selected
by the user's application.

Connections are opt-in. The dashboard explains requested metrics and permission
before recording local setup approval. Approval alone does not transmit data or
store a provider credential.

## Reports And Shared Files

Exports can contain project names, model usage, workflow labels, customer labels,
and spend. Review a report before sharing it outside the organization. A future
hosted product will have a separate privacy notice and data-processing terms.
