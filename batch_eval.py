"""
batch_eval.py
-------------
Runs the required 30-prompt test set directly through wingman.py (no HTTP
hop needed — backend.py is a UI convenience, not a requirement for eval).
Writes results.jsonl for the record and prints a live progress line per prompt.

Usage:
    export DEEPSEEK_API_KEY="sk-..."
    python3 batch_eval.py test_prompts.json --out results.jsonl

    # offline structural check, no key/network needed:
    python3 batch_eval.py test_prompts.json --out results.jsonl --mock
"""

import argparse
import json
import os
import sys

from wingman import get_wingman_response
from memory import MemoryManager
import pricing_calendar

DEFAULT_RELATIONSHIP = "Aisha"  # used for every test prompt unless overridden per-item


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompts_path", help="Path to test_prompts.json")
    parser.add_argument("--out", default="results.jsonl")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--relationship", default=DEFAULT_RELATIONSHIP)
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    mock = args.mock or not api_key
    if mock and not args.mock:
        print("Note: DEEPSEEK_API_KEY not set — running in --mock mode.\n", file=sys.stderr)

    if not mock:
        pricing = pricing_calendar.current_rate_note()
        if pricing["peak_pricing_active"]:
            print(
                f"⚠️  {pricing['note']} — unlike live chat, this batch run is schedulable. "
                f"Consider re-running off-peak to avoid the {pricing['multiplier']}x rate.\n",
                file=sys.stderr,
            )

    with open(args.prompts_path, "r", encoding="utf-8") as f:
        prompts = json.load(f)

    mm = MemoryManager()
    results = []

    with open(args.out, "w", encoding="utf-8") as out_f:
        for item in prompts:
            relationship = item.get("relationship", args.relationship)
            result = get_wingman_response(
                relationship=relationship,
                message=item["prompt"],
                api_key=api_key,
                mock=mock,
                memory_manager=mm,
                use_history=False,  # each test prompt must be judged independently
            )
            record = {
                "id": item["id"],
                "category": item["category"],
                "prompt": item["prompt"],
                "relationship": relationship,
                **result,
            }
            results.append(record)
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")

            status = "OK " if record["schema_valid"] else "FAIL"
            rt = record.get("response_time_sec")
            print(f"[{item['id']:02d}] {status} {item['category']:32s} {rt if rt is not None else '-'}s")

    valid = [r for r in results if r["schema_valid"] and r.get("response_time_sec") is not None]
    if valid:
        avg = sum(r["response_time_sec"] for r in valid) / len(valid)
        print(f"\n{len(valid)}/{len(results)} valid. Avg response time: {avg:.2f}s")
    else:
        print("\nNo valid responses recorded.")
    print(f"Results written to {args.out}")


if __name__ == "__main__":
    main()
