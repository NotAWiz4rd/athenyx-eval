"""
Evaluate testcases against a locally-running Athenyx backend.

Usage:
    python run_eval.py [OPTIONS] [PATHS...]

    PATHS can be individual YAML files or directories (searched recursively).
    Defaults to ./testcases/ if nothing is specified.

Options:
    --base-url URL    Athenyx backend URL  (default: http://localhost:8000)
    --verbose, -v     Chatty per-testcase logging (request/response details)
    --api-key KEY     API key for the backend (or set ATHENYX_API_KEY env var)
    --tag TAG         Only run testcases that have this tag (repeatable)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml


# ── Testcase loading ─────────────────────────────────────────────────────────

def load_testcases(paths: list[Path], tags: set[str] | None) -> list[dict]:
    """Load testcases from files/directories. Each YAML can be a single dict or a list."""
    files = _collect_yaml_files(paths)
    if not files:
        print("No YAML files found.", file=sys.stderr)
        sys.exit(1)

    cases: list[dict] = []
    for f in sorted(files):
        try:
            docs = list(yaml.safe_load_all(f.read_text()))
        except yaml.YAMLError as exc:
            print(f"  SKIP {f}: YAML parse error: {exc}", file=sys.stderr)
            continue

        for doc in docs:
            if doc is None:
                continue
            if isinstance(doc, list):
                cases.extend(doc)
            else:
                cases.append(doc)

    # Attach source-file info and validate
    valid: list[dict] = []
    for i, tc in enumerate(cases):
        tc.setdefault("id", f"anon-{i}")
        if "expected" not in tc or "model_output" not in tc:
            print(f"  SKIP {tc['id']}: missing 'expected' or 'model_output'", file=sys.stderr)
            continue
        if tags and not (tags & set(tc.get("tags", []))):
            continue
        valid.append(tc)

    return valid


def _collect_yaml_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("*.yaml")))
            files.extend(sorted(p.rglob("*.yml")))
    return files


# ── API interaction ──────────────────────────────────────────────────────────

def build_request_body(tc: dict) -> dict:
    """Build an InterveneRequest JSON body from a testcase dict."""
    body: dict = {
        "previous_messages": tc.get("previous_messages", []),
        "mode": tc.get("mode", "monitor_only"),
        "model_output": tc["model_output"],
    }
    if "agent_id" in tc:
        body["agent_id"] = tc["agent_id"]
    if "metadata" in tc:
        body["metadata"] = tc["metadata"]
    return body


def call_intervene(
    client: httpx.Client,
    base_url: str,
    body: dict,
) -> tuple[dict | None, float, str | None]:
    """POST to /v0/intervene. Returns (response_json, latency_seconds, error_string)."""
    url = f"{base_url}/v0/intervene"
    t0 = time.monotonic()
    try:
        resp = client.post(url, json=body)
        latency = time.monotonic() - t0
        if resp.status_code != 200:
            return None, latency, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return resp.json(), latency, None
    except httpx.RequestError as exc:
        return None, time.monotonic() - t0, str(exc)


# ── Evaluation ───────────────────────────────────────────────────────────────

@dataclass
class Result:
    tc_id: str
    passed: bool
    expected_decision: str
    actual_decision: str | None = None
    latency: float = 0.0
    error: str | None = None
    coaching_prompt: str | None = None


@dataclass
class Summary:
    results: list[Result] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def errors(self) -> int:
        return sum(r.error is not None for r in self.results)

    @property
    def avg_latency(self) -> float:
        latencies = [r.latency for r in self.results if r.error is None]
        return sum(latencies) / len(latencies) if latencies else 0.0


def evaluate_response(tc: dict, resp: dict | None, latency: float, error: str | None) -> Result:
    expected = tc["expected"]
    expected_decision = expected["decision"]

    if error is not None:
        return Result(tc["id"], passed=False, expected_decision=expected_decision, error=error, latency=latency)

    assert resp is not None
    actual_decision = resp["decision"]
    passed = actual_decision == expected_decision

    return Result(
        tc_id=tc["id"],
        passed=passed,
        expected_decision=expected_decision,
        actual_decision=actual_decision,
        latency=latency,
        coaching_prompt=resp.get("coaching_prompt"),
    )


# ── Output formatting ────────────────────────────────────────────────────────

def print_result(r: Result, verbose: bool) -> None:
    status = "PASS" if r.passed else "FAIL"
    line = f"  [{status}] {r.tc_id}"

    if r.error:
        line += f"  ERROR: {r.error}"
    else:
        line += f"  expected={r.expected_decision}  got={r.actual_decision}  ({r.latency:.3f}s)"

    print(line)

    if verbose and not r.error:
        if r.coaching_prompt:
            print(f"         coaching_prompt: {r.coaching_prompt[:120]}")


def print_summary(summary: Summary) -> None:
    print()
    print("=" * 60)
    print(f"  Total: {summary.total}   Passed: {summary.passed}   Failed: {summary.failed}   Errors: {summary.errors}")
    if summary.total:
        print(f"  Pass rate: {summary.passed / summary.total:.1%}   Avg latency: {summary.avg_latency:.3f}s")

    if summary.failed:
        print()
        print("  Failed testcases:")
        for r in summary.results:
            if not r.passed:
                reason = r.error or f"expected {r.expected_decision}, got {r.actual_decision}"
                print(f"    - {r.tc_id}: {reason}")

    print("=" * 60)


# ── Main ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Athenyx eval testcases")
    p.add_argument("paths", nargs="*", type=Path, default=[Path("testcases")])
    p.add_argument("--base-url", default=os.environ.get("ATHENYX_BASE_URL", "http://localhost:8000"))
    p.add_argument("--api-key", default=os.environ.get("ATHENYX_API_KEY"))
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--tag", action="append", dest="tags")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    tags = set(args.tags) if args.tags else None

    cases = load_testcases(args.paths, tags)
    print(f"Loaded {len(cases)} testcase(s)\n")

    if not cases:
        sys.exit(0)

    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    summary = Summary()
    with httpx.Client(headers=headers, timeout=30.0) as client:
        for tc in cases:
            body = build_request_body(tc)
            if args.verbose:
                print(f"  >>> {tc['id']}: POST /v0/intervene  mode={body['mode']}")

            resp, latency, error = call_intervene(client, args.base_url, body)
            result = evaluate_response(tc, resp, latency, error)
            summary.results.append(result)
            print_result(result, args.verbose)

    print_summary(summary)
    sys.exit(0 if summary.failed == 0 else 1)


if __name__ == "__main__":
    main()
