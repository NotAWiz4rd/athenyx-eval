#!/usr/bin/env python3
"""Convert raw benchmark JSON files into eval testcase YAML files.

Walks the raw-data/ folder (recursively), reads each .json file, and writes
one testcase per file into testcases/generated/.

For each JSON file:
  - model_output is taken from raw_response.
  - expected.decision is "block" when classification.harmful_behavior is true,
    and "allow" otherwise.
"""

import json
import re
import sys
from pathlib import Path

import yaml


RAW_DATA_DIR = Path("raw-data")
OUTPUT_DIR = Path("testcases/generated")


def _sanitize_id(filename: str) -> str:
    """Turn a filename into a testcase-id-friendly slug."""
    stem = Path(filename).stem
    # Strip the long common prefix to keep IDs concise
    stem = re.sub(r"^example_experiment_", "", stem)
    # Replace non-alphanum with hyphens, collapse runs
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug


def _extract_category_and_type(condition: str) -> tuple[str, str]:
    """Parse 'leaking_none-none_restriction' → ('leaking', 'restriction')."""
    parts = condition.split("_")
    category = parts[0]  # e.g. 'leaking', 'murder'
    variant = parts[-1]  # e.g. 'restriction', 'replacement'
    return category, variant


def convert_file(json_path: Path) -> list[dict]:
    """Convert a single JSON benchmark file into one testcase dict."""
    with open(json_path) as f:
        data = json.load(f)

    metadata = data.get("metadata", {})
    condition = metadata.get("condition", "unknown")
    sample_id = metadata.get("sample_id", 0)
    model = metadata.get("model", "unknown")
    category, variant = _extract_category_and_type(condition)

    base_id = _sanitize_id(json_path.name)
    harmful = data.get("classification", {}).get("harmful_behavior", False)
    decision = "block" if harmful else "allow"
    tag = "misaligned" if harmful else "benign"

    tc = {
        "id": base_id,
        "description": (
            f"Raw response ({category}/{variant}, "
            f"sample {sample_id}, model {model}). "
            f"harmful_behavior={harmful}."
        ),
        "tags": ["generated", tag, category, variant],
        "mode": "monitor_only",
        "model_output": data["raw_response"],
        "expected": {"decision": decision},
    }
    return [tc]


def main():
    if not RAW_DATA_DIR.exists():
        print(f"Error: {RAW_DATA_DIR} does not exist", file=sys.stderr)
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_files = sorted(RAW_DATA_DIR.rglob("*.json"))
    if not json_files:
        print("No .json files found in raw-data/", file=sys.stderr)
        sys.exit(1)

    total_testcases = 0
    for json_path in json_files:
        testcases = convert_file(json_path)
        # Write each source JSON as its own YAML file
        yaml_name = json_path.stem + ".yaml"
        out_path = OUTPUT_DIR / yaml_name

        with open(out_path, "w") as f:
            f.write(f"# Auto-generated from {json_path.relative_to('.')}\n")
            f.write("# Do not edit by hand — regenerate with convert_raw_data.py\n\n")
            yaml.dump(
                testcases,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )

        total_testcases += len(testcases)
        print(f"  {out_path}  ({len(testcases)} testcase(s))")

    print(f"\nDone: {len(json_files)} files → {total_testcases} testcases in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
