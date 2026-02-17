# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Evaluation testset runner for the Athenyx realtime agentic misalignment monitor. Testcases are YAML files that exercise the Athenyx backend's `POST /v0/intervene` endpoint, which inspects model output and decides whether to **allow**, **coach**, or **block** it.

## Commands

```bash
# Install dependencies (uses a .venv with Python 3.14)
pip install -e ".[dev]"

# Run all eval testcases against a local backend
python run_eval.py

# Run specific testcase files or directories
python run_eval.py testcases/example.yaml
python run_eval.py path/to/dir/

# Filter by tag
python run_eval.py --tag smoke

# Verbose mode (shows request/response details)
python run_eval.py -v

# Point at a different backend
python run_eval.py --base-url http://staging:8000

# Tune concurrency and retries (defaults: 10 / 3)
python run_eval.py -j 20 --retries 5

# Lint
ruff check .

# Run unit tests
pytest
```

## Architecture

Single-script eval runner (`run_eval.py`) with no package structure:

1. **Testcase loading** — Reads YAML files from `testcases/` (or user-specified paths). Each file can contain a single dict, a list, or multiple YAML documents (`---`-separated). Required fields per testcase: `model_output`, `expected.decision`. Optional: `id`, `description`, `tags`, `previous_messages`, `mode`, `agent_id`, `metadata`.
2. **API interaction** — Builds an `InterveneRequest` body and POSTs to `/v0/intervene` on the Athenyx backend. Auth via `--api-key` flag or `ATHENYX_API_KEY` env var. Requests run concurrently via `asyncio` + `httpx.AsyncClient`, bounded by `--concurrency` (default 10). Failed requests are retried up to `--retries` times (default 3) with exponential backoff (0.5s, 1s, 2s, …); HTTP 4xx errors are not retried.
3. **Evaluation** — Compares `response.decision` against `expected.decision`. Collects pass/fail/error counts and latency stats.
4. **Progress display** — Live progress bar showing `done/total (pct%)`, mean latency, and ETA (adjusted for concurrency: `avg_latency × remaining / concurrency`).
5. **Exit code** — 0 if all testcases pass, 1 otherwise.

The API schema is captured in `openapi.json` (Athenyx v0.1 API spec). Key response fields: `decision` (allow/coach/block) and optional `coaching_prompt`.

## Testcase Format

See `testcases/example.yaml` for the canonical format. Testcases map directly to the `/v0/intervene` request body plus an `expected` block for assertions.
