# Poliora

Poliora is an AI cost, carbon, and fine-tuning efficiency toolkit.

[Laptop testing](docs/LAPTOP_TESTING.md) | [Release gate](docs/LAUNCH_CHECKLIST.md) | [Hosting](docs/HOSTING_AND_LAUNCH.md) | [Private pilot](docs/PILOT_GUIDE.md) | [Privacy](docs/PRIVACY.md) | [Security](SECURITY.md) | [Changelog](CHANGELOG.md) | [License](LICENSE)

The Cloudflare Pages-ready product site is in [`site/`](site/).

The original project focused on sustainable LLM fine-tuning. The new direction
keeps that foundation, but expands the product into a practical "AI FinOps"
toolkit: track token usage, estimate spend, project monthly cost, and get
recommendations for reducing waste.

## What Poliora Does

- Records AI usage events from the CLI or Python SDK.
- Estimates token cost by provider and model.
- Produces local AI spend reports with model and workflow breakdowns.
- Projects monthly spend against a configured budget.
- Suggests savings actions like cheaper model routing, prompt trimming, and caching.
- Tracks optimization decisions from modeled opportunity through quality testing
  and measured rollout, keeping projected and realized savings separate.
- Keeps the existing LoRA, quantization, carbon tracking, and training benchmark tools.

## Install And Open

The launch install is deliberately small. Install the cost-control product, move
to the folder where its local data should live, and run one command:

```bash
pip install poliora
poliora dashboard
```

On first run, Poliora creates a `.poliora` workspace and opens
`http://127.0.0.1:8787` automatically. The same command opens the existing
workspace on future runs. Stop the local app with `Ctrl+C`.

The legacy fine-tuning toolkit is optional because its machine-learning
dependencies are much larger:

```bash
pip install "poliora[training]"
```

## Install For Development

```bash
poetry install
```

Or, with the existing virtual environment:

```bash
.venv\Scripts\python.exe -m poliora.main --help
```

## Quickstart: AI Spend Tracking

Creating a workspace explicitly is useful when you want to set its name and
budget before opening the dashboard:

```bash
poliora init --project demo-agency --monthly-budget 1000
```

Record usage:

```bash
poliora record \
  --provider openai \
  --model gpt-4o \
  --input-tokens 8000 \
  --output-tokens 2000 \
  --cached-input-tokens 3000 \
  --reasoning-tokens 800 \
  --operation agent
```

`input_tokens` includes cached tokens when your provider reports them. Poliora applies
the matching cache-read price where known, records reasoning-token usage as a separate
signal, and can also track fixed provider tool charges with `--tool-cost-usd`.

Generate a report:

```bash
poliora report --json .poliora/report.json --csv .poliora/models.csv --html poliora-report.html
```

Fail a CI/deployment check if projected spend is too high:

```bash
poliora check --max-monthly 600
```

Get recommendations:

```bash
poliora recommend --monthly-spend 1000 --target-savings 40
```

`--html` creates a polished, standalone executive report. It can be opened locally or attached to a client update; it needs no server, account, or tracking script.

## Local Dashboard

Start a browser-based control room for the current workspace:

```bash
poliora dashboard
```

Then open `http://127.0.0.1:8787`. The dashboard reads local data only, includes the routing simulator, and can export a client-ready HTML report. It binds to your computer by default; do not expose it to the public internet yet.

The catalog can also save local contract rates, while the routing simulator can
save named scenarios. Those assumptions stay in `.poliora` with the project;
they are not sent to Poliora or an AI provider.

The **Connection center** lists the AI tools Poliora can observe. It explains
the exact metrics and permission required for each connector before you approve
setup. Approval is local and does not save any provider key.

Install the supported Antigravity workspace plugin with:

```bash
poliora antigravity-install
```

The plugin records Antigravity invocation activity through its documented hook.
Google's hook payload does not expose model or token totals, so Poliora keeps
this activity separate from measured Gemini API spend.

When the workspace is empty, select **Load guided sample data** on the dashboard
to explore the charts, filters, model catalog, routing simulator, scenarios, and
report export with fictional records. It never loads over existing usage data.

## Test A Cheaper Model Route

Use recorded usage to estimate the impact of moving part of a workload to a different model:

```bash
poliora simulate \
  --source-provider openai --source-model gpt-4o \
  --target-provider openai --target-model gpt-4o-mini \
  --percentage 35
```

Poliora keeps the current recorded cost and prices the proposed route from the editable `.poliora/pricing.json` registry. That makes the assumptions visible before anyone acts on the result.

## Import Existing Usage

For a quick client pilot, import a CSV instead of changing application code first:

```bash
poliora import-csv client-usage.csv --provider openai --project client-demo
```

Poliora accepts common columns such as `model`, `prompt_tokens`, `completion_tokens`,
`cached_tokens`, `reasoning_tokens`, `tool_cost_usd`, `cost_usd`, `workflow`,
`customer`, and `timestamp`. It preserves a supplied `cost_usd`; otherwise it uses
the editable pricing registry.

Try the included example before importing a real client file:

```bash
poliora import-csv examples/usage_import.csv --project demo-agency
```

## Python SDK

```python
from poliora.cost import log_usage

event = log_usage(
    provider="openai",
    model="gpt-4o-mini",
    input_tokens=1200,
    output_tokens=400,
    cached_input_tokens=300,
    trace_id="support-ticket-1842",
    operation="support-chat",
)

print(event.cost_usd)
```

## Track A Codex CLI Task

Run a Codex task through Poliora's wrapper to collect the documented JSON usage
event without storing the task, agent reply, commands, or file changes:

```bash
poliora codex --model gpt-5.4 --sandbox workspace-write "review this repository"
```

`codex --version` must identify itself as `codex-cli`. Poliora rejects unrelated
executables that happen to use the same command name and prints the repair steps.

The default assumes Codex is using a ChatGPT subscription, so Poliora records
tokens but does not invent an API-equivalent dollar charge. Use `--api-billed`
only when Codex is authenticated for usage-based API billing and the selected
model has the correct rate in your workspace.

Wrap an OpenAI-style call:

```python
from poliora.cost import track_openai_call

captured = track_openai_call(
    client.chat.completions.create,
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Summarize this ticket."}],
    operation="support-chat",
)

response = captured.response
print(captured.event.cost_usd)
```

Or proxy common SDK calls:

```python
from poliora.cost import track_openai_client

client = track_openai_client(client, project="acme")
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Draft a reply."}],
)
```

For a provider or gateway with an OpenAI-compatible client, keep the same
response parsing but label the event correctly:

```python
from poliora.cost import track_openai_compatible_client

client = track_openai_compatible_client(client, provider="deepseek", project="acme")
```

Gemini Generate Content responses can be wrapped directly as well:

```python
from poliora.cost import track_gemini_client

client = track_gemini_client(client, project="acme")
response = client.models.generate_content(model="gemini-3.5-flash", contents="Summarize this ticket.")
```

See `examples/gemini_tracking.py` for a runnable example. The provider key is
read by Google's client from the environment; Poliora does not save it.

For OpenAI-style response objects or dictionaries:

```python
from poliora.cost import log_openai_response

response = {
    "model": "gpt-4o-mini",
    "usage": {
        "prompt_tokens": 300,
        "completion_tokens": 200,
    },
}

log_openai_response(response)
```

Anthropic-style response dictionaries are supported too:

```python
from poliora.cost import log_anthropic_response

log_anthropic_response({
    "model": "claude-3-5-haiku",
    "usage": {"input_tokens": 800, "output_tokens": 250},
})
```

## Pricing

Poliora ships with an editable starter pricing registry:

```bash
poliora pricing
```

The file lives at:

```text
.poliora/pricing.json
```

These are estimates, not vendor billing truth. Teams should update this file
with their real provider rates, discounts, and enterprise contracts.

The model catalog is separate from pricing. It includes verified provider model
IDs and lifecycle metadata, while workspace prices take precedence over public
defaults:

```bash
poliora models
poliora models --provider deepseek
poliora model-add --provider acme --model terra-v1 --name "Terra V1" --input-per-1m 1.2 --output-per-1m 4.8
```

Custom models are first-class records. Poliora will never guess a price for them.

To refresh a provider catalog from the models available to your own account,
run a local sync. The API key is only used for that request and is never saved:

```bash
poliora sync-models --provider openai --api-key YOUR_OPENAI_API_KEY
```

Supported discovery providers are OpenAI, Anthropic, Google, Mistral, and xAI.
Model discovery adds account-visible models but does not overwrite pricing; set
contract rates in `.poliora/pricing.json` or with `poliora model-add`.

## Fine-Tuning Tools

The original training workflow is still available:

```bash
poliora train \
  --model microsoft/phi-3-mini-4k-instruct \
  --dataset examples/sample_data.csv \
  --output tuned_model \
  --epochs 3 \
  --lora \
  --quantize \
  --carbon
```

Benchmark eco-optimized training against a baseline:

```bash
poliora benchmark \
  --model microsoft/phi-3-mini-4k-instruct \
  --dataset examples/sample_data.csv \
  --epochs 1 \
  --json benchmark_output/report.json \
  --csv benchmark_output/report.csv
```

## Product Direction

Poliora should become the control room for AI operating cost:

- local CLI and SDK for developers
- hosted dashboard for teams
- alerts when projected spend crosses budget
- client-ready PDF reports for agencies
- CI/CD budget gates for AI features
- integrations with OpenAI, Anthropic, Gemini, Langfuse, LiteLLM, and cloud billing exports
- carbon reporting as a useful differentiator, not the only sales message

The commercial promise is simple:

> See where AI spend is going, then reduce waste without blindly hurting quality.

## Development

Run tests:

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

Run lint:

```bash
.venv\Scripts\ruff.exe check poliora tests
```

## License

MIT
