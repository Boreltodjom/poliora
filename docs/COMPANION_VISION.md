# Poliora Companion Vision

## Product Promise

Poliora Companion is an optional local integration service. With a user's
explicit permission, it collects AI usage metadata from the coding tools and
provider accounts they already use, then sends that metadata into the local
Poliora workspace or a future hosted Poliora organization.

The dashboard is the control room. The Companion is the connection layer that
makes the dashboard useful without manual token entry.

## Privacy Boundary

The Companion collects only what is needed for cost and operations reporting:

- provider and tool name
- model ID
- input, output, cache, and reasoning token counts when available
- provider-reported cost and separate tool charges when available
- timestamp, project/workspace label, user label, and request/session ID
- non-content operational signals such as latency, tool acceptance, commits,
  or agent sessions when a connector provides them

It must not collect prompts, source code, model responses, screenshots, or
terminal commands by default. Every connector has a clear permission screen,
shows exactly what it reads, and can be disconnected or deleted locally.

## Connector Strategy

### Ready For Official Account Integrations

- Cursor Team Admin API: usage events, model usage, tokens, costs, user and
  team data. Requires a team administrator API key.
- Claude Code Analytics and Usage/Cost APIs: daily per-user Claude Code model,
  token, estimated-cost, session, and tool metrics. Requires an Anthropic
  organization Admin API key.
- Gemini and Antigravity API/SDK workloads: response usage metadata through
  the existing Gemini capture adapter, then an official Antigravity SDK adapter
  where the installed product exposes supported usage observability.

### Direct Runtime Capture

- OpenAI API and OpenAI-compatible clients: Poliora SDK wrappers capture
  responses while the customer application runs. This already supports OpenAI,
  DeepSeek, compatible gateways, and Gemini response capture.
- Claude API applications: the existing Anthropic response wrapper captures
  request usage without reading prompts.

### Codex And Other Agent Products

- Codex API-backed usage can be captured at the API/runtime layer.
- A ChatGPT or product-subscription session must not be presented as exact
  token spend unless an official product usage export or API provides it.
- Poliora will prefer an official Codex plugin, export, or account API when
  one provides cost-quality data. Until then, it can report verified API usage
  and clearly label subscription/quota signals separately from dollar spend.

## Consent Flow

1. The user chooses a connector in Poliora.
2. Poliora explains the exact fields it will read and the connector's data
   freshness limits.
3. The user grants the minimum required provider permission or locally adds
   their own admin key.
4. Credentials stay in the OS credential store or a future hosted secret
   manager, never in reports, source code, or browser JavaScript.
5. The user can pause, remove, or delete the connector and local connector
   data at any time.

## Delivery Order

1. Build the Companion connector registry and consent/status screen.
2. Add Cursor Admin API import and Claude Code Analytics import behind
   user-provided admin credentials.
3. Add scheduled local refresh, normalized event ingestion, and data-source
   freshness indicators.
4. Add supported Antigravity SDK integration and any official Codex integration
   that exposes trustworthy usage or costs.
5. Carry the same connector contract into the hosted product with organization
   isolation, encrypted secrets, audit logs, and explicit data retention rules.
