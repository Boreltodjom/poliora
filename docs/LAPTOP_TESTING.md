# Laptop Testing

This guide tests the current source checkout on Windows with one Command Prompt
window. It does not require an AI provider key for the dashboard or Codex
subscription test.

## Prepare Once

Open Command Prompt and run:

```bat
cd /d C:\Poliora
.venv\Scripts\activate
python -m pip install -e .
```

The prompt should now begin with `(.venv)`. Keep this same window open for all
steps below.

## Test The Dashboard

To open the dashboard and public website preview together, run:

```bat
run-local-previews.cmd
```

This opens two titled Command Prompt windows. Keep them open while testing.
Press `Ctrl+C` in each window when finished.

To open only the dashboard, run:

```bat
poliora dashboard
```

Your browser should open `http://127.0.0.1:8787`. Check these flows:

1. Overview: load guided sample data only if the workspace is empty.
2. Connections: click **Scan this computer**. It should show whether the Codex,
   Claude Code, and Cursor launchers are on your PATH and whether Poliora's
   Antigravity workspace helper is installed. The scan must clearly state that
   it did not open a tool or inspect chats, prompts, code, credentials, or
   account history.
3. Import: select `examples\usage_import.csv`, preview it, then import it.
4. Decision lab: calculate a model route, track it, and move it to Testing.
5. Confirm Poliora rejects Validated or Rolled out until Quality result is Pass.
6. Enter a measured monthly value, set Rolled out, and confirm it appears under
   Realized monthly savings.
7. Models & Rates: search for `gpt-5.6`, `claude-opus-4-8`,
   `claude-sonnet-5`, `claude-fable-5`, `gemini`, and `deepseek`.
8. Export: add a client name and confirm the printable report contains the
   savings proof ledger.

Press `Ctrl+C` in Command Prompt when you are ready to test Codex.

## Test With Codex

Confirm the Codex CLI is installed and signed in:

```bat
codex --version
```

The output must begin with `codex-cli`. If a Python traceback mentions a comic
archive server, an unrelated package is shadowing OpenAI Codex. Repair it with:

```bat
python -m pip uninstall -y codex
npm.cmd install -g @openai/codex
codex --version
```

Run a small read-only task through Poliora. Replace the model only if your Codex
account does not offer this one:

```bat
poliora codex --model gpt-5.6-sol --sandbox read-only "Inspect this repository and name its three main modules. Do not edit files."
```

For normal ChatGPT/Codex subscription use, do not add `--api-billed`. Poliora
records provider-reported token totals but correctly assigns zero API spend.
Use `--api-billed` only when the run is genuinely charged to an API account.

Open the dashboard again in the same window:

```bat
poliora dashboard
```

The Overview request count should increase. The Codex turn appears as
`openai/gpt-5.6-sol`, and the dashboard labels it as a subscription turn excluded
from tracked dollar spend. Press `Ctrl+C` after checking it.

## Test With Antigravity Later

Install Poliora's workspace plugin:

```bat
poliora antigravity-install
```

Open `C:\Poliora` as the workspace in Antigravity, reload its customizations or
restart Antigravity, and run one small agent task. The plugin uses Google's
documented `PreInvocation` hook and records only an anonymized session reference,
invocation number, project, and timestamp. It never reads the transcript.

Restart `poliora dashboard` and look for `google/antigravity-managed`. It is an
activity-only, zero-dollar record because Antigravity's current hook payload does
not expose model names or token totals.

To measure a Gemini API workload developed or run from Antigravity, install the
official client and set your key only when you are ready:

```bat
python -m pip install google-genai
set GEMINI_API_KEY=your_key_here
python examples\gemini_tracking.py
```

That path records the exact model and token metadata returned by Gemini and
estimates cost from `.poliora\pricing.json`. Do not put the key in source files.

## Expected Privacy Boundary

- `.poliora\usage.jsonl` contains usage metadata, not prompts or responses.
- Codex subscription and Antigravity activity are excluded from dollar spend.
- Gemini API, OpenAI API, Anthropic API, and imported billing records can produce
  dollar estimates when their model rate is present.
- The dashboard is local. Keep it bound to `127.0.0.1`.
