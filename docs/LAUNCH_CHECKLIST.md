# Poliora Release Gate

No release is approved until every required item is checked.

## Automated

```powershell
.\.venv\Scripts\python.exe -m ruff check poliora tests examples scripts
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe scripts\release_check.py
```

Start a local dashboard and run the browser gate:

```powershell
.\.venv\Scripts\poliora.exe dashboard --no-open --port 8787
$env:NODE_PATH="C:\path\to\node_modules"
node scripts\ui_smoke.cjs http://127.0.0.1:8787 artifacts\ui-smoke
```

## Clean Install

- Install the wheel in a new virtual environment.
- Run `poliora --version`, `poliora --help`, and `poliora dashboard`.
- Confirm the unrelated Python package named `codex` is absent.
- Confirm `codex --version` identifies `codex-cli` before testing the wrapper.
- Import `examples/usage_import.csv`.
- Calculate and track one decision.
- Verify that a validated or rolled-out decision requires quality `pass`.
- Export and open the HTML report.

## Browser

- Desktop width: 1440 pixels.
- Mobile width: 390 pixels.
- All five views open without a console or page error.
- No horizontal page overflow.
- Decision simulation enables Track decision.
- Report dialog opens and closes.
- Long model IDs and notes do not overlap controls.

## Security And Privacy

- No `.env`, provider key, generated secret, `.poliora` workspace, or customer
  data exists in source or distribution archives.
- Dashboard still binds to `127.0.0.1`.
- Report and integration copy do not claim prompts or code are collected.
- Public pricing changes are checked against official provider sources.

## External

- Hosted GitHub Actions passes on Windows and Linux.
- TestPyPI clean install passes before PyPI publication.
- Domain, support email, privacy page, and security contact resolve.
- One person who did not build Poliora completes the beginner flow unaided.

