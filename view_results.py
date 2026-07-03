#!/usr/bin/env python3
"""
view_results.py
----------------
Two things raw results.jsonl doesn't give you: a readable view, and a
head start on the evaluation sheet (which otherwise means manually
copy-pasting 30 prompts/responses into evaluation_template.csv by hand).

Usage:
    # readable terminal view of a batch run
    python3 view_results.py results.jsonl

    # filter to one persona
    python3 view_results.py results.jsonl --relationship Raj

    # auto-fill the objective columns (prompt, response, time, relationship)
    # into a copy of evaluation_template.csv — leaves the 1-5 score columns
    # blank for you to actually judge
    python3 view_results.py results.jsonl --fill-sheet evaluation_template.csv --out evaluation_filled.csv
"""

import argparse
import csv
import json
import sys


def load_results(path: str) -> list:
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def print_readable(results: list, relationship_filter: str = None):
    shown = 0
    for r in results:
        if relationship_filter and r.get("relationship") != relationship_filter:
            continue
        shown += 1
        rel = r.get("relationship", "?")
        cat = r.get("category", "?")
        print(f"\n[{r['id']:02d}] {cat}  →  {rel}")
        print(f"  Prompt:   {r['prompt']}")
        if not r.get("schema_valid"):
            print(f"  ❌ FAILED: {r.get('error')}")
            continue
        out = r["output"]
        if r.get("warning"):
            print(f"  ⚠️  {r['warning']}")
        print(f"  Response: {out['wingman_response']}")
        if out.get("suggested_messages"):
            print(f"  Suggested: {' | '.join(out['suggested_messages'])}")
        if out.get("follow_up_question"):
            print(f"  Follow-up: {out['follow_up_question']}")
        print(f"  Confidence: {out['confidence']}  |  Time: {r.get('response_time_sec')}s"
              + ("  |  cached" if r.get("cached") else ""))

    print(f"\n{'=' * 60}")
    valid = [r for r in results if (not relationship_filter or r.get("relationship") == relationship_filter) and r.get("schema_valid")]
    print(f"Shown: {shown}  |  Valid: {len(valid)}")
    if valid:
        avg_time = sum(r["response_time_sec"] for r in valid if r.get("response_time_sec")) / len(valid)
        avg_conf = sum(r["output"]["confidence"] for r in valid) / len(valid)
        print(f"Avg response time: {avg_time:.2f}s  |  Avg confidence: {avg_conf:.2f}")


def fill_sheet(results: list, template_path: str, out_path: str):
    by_id = {r["id"]: r for r in results}

    with open(template_path, "r", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    header = rows[0]
    # Add a relationship column if the template doesn't already have one
    if "relationship" not in header:
        header.append("relationship")
    idx = {name: i for i, name in enumerate(header)}

    filled_rows = [header]
    for row in rows[1:]:
        row = row + [""] * (len(header) - len(row))
        row_id = int(row[idx["id"]])
        r = by_id.get(row_id)
        if r:
            row[idx["prompt"]] = r["prompt"]
            if r.get("schema_valid"):
                row[idx["wingman_response"]] = r["output"]["wingman_response"]
            else:
                row[idx["wingman_response"]] = f"[FAILED: {r.get('error', 'unknown')}]"
            row[idx["response_time_sec"]] = str(r.get("response_time_sec", ""))
            row[idx["relationship"]] = r.get("relationship", "")
        filled_rows.append(row)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(filled_rows)

    print(f"Filled sheet written to {out_path}")
    print("Objective columns (prompt, response, time, relationship) are filled in.")
    print("Score columns (1-5 ratings, safety pass/fail, notes) are still yours to judge by hand.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results_path", help="Path to a results.jsonl produced by batch_eval.py")
    parser.add_argument("--relationship", help="Only show results for this relationship")
    parser.add_argument("--fill-sheet", help="Path to evaluation_template.csv to auto-fill")
    parser.add_argument("--out", default="evaluation_filled.csv", help="Output path for the filled sheet")
    args = parser.parse_args()

    results = load_results(args.results_path)

    if args.fill_sheet:
        fill_sheet(results, args.fill_sheet, args.out)
    else:
        print_readable(results, args.relationship)


if __name__ == "__main__":
    main()
