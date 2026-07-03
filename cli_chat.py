#!/usr/bin/env python3
"""
cli_chat.py
-----------
Quick one-off testing from the terminal: send a single message to a specific
relationship and see a readable response, without going through Streamlit or
writing a Python one-liner by hand.

Usage:
    export DEEPSEEK_API_KEY="sk-..."
    python3 cli_chat.py Aisha "she replied haha to my long message"
    python3 cli_chat.py Raj "he cancelled plans again and said sure no worries"

    # no key needed:
    python3 cli_chat.py Aisha "she loves hiking" --mock

    # keep conversation history across calls (off by default here, since
    # each CLI invocation is a fresh process — pass --history to opt in and
    # it reads/writes conversations.json like the real chat does):
    python3 cli_chat.py Aisha "morning!" --history
    python3 cli_chat.py Aisha "she didn't reply all day" --history   # remembers the above

    # list available personas:
    python3 cli_chat.py --list
"""

import argparse
import json
import os
import sys

from wingman import get_wingman_response
from memory import MemoryManager


def print_response(relationship: str, message: str, result: dict):
    print(f"\n{'─' * 60}")
    print(f"  {relationship}  <-  \"{message}\"")
    print(f"{'─' * 60}")

    if not result["schema_valid"]:
        print(f"  ❌ FAILED: {result.get('error')}")
        if result.get("raw"):
            print(f"  Raw output: {result['raw'][:300]}")
        return

    out = result["output"]
    if result.get("warning"):
        print(f"  ⚠️  {result['warning']}")
    if result.get("cached"):
        print("  ⚡ served from cache")

    print(f"  Mode:        {out['mode']}")
    print(f"  Energy read: {out['energy_read']}")
    print(f"  Response:    {out['wingman_response']}")
    if out["suggested_messages"]:
        print("  Suggested messages:")
        for m in out["suggested_messages"]:
            print(f"    - {m}")
    if out["follow_up_question"]:
        print(f"  Follow-up:   {out['follow_up_question']}")
    if out["memory_candidates"]:
        print("  Memory candidates noticed:")
        for c in out["memory_candidates"]:
            print(f"    - [{c['category']}] {c['value']}")
    if out["safety_flag"]:
        print("  🚩 safety flag raised")
    print(f"  Confidence:  {out['confidence']}")
    print(f"  Time:        {result.get('response_time_sec')}s")


def main():
    parser = argparse.ArgumentParser(description="Quick single-message Wingman test from the CLI")
    parser.add_argument("relationship", nargs="?", help="Who this message is about, e.g. Aisha or Raj")
    parser.add_argument("message", nargs="?", help="The situation/message to send")
    parser.add_argument("--mock", action="store_true", help="Force mock mode even if a key is set")
    parser.add_argument("--history", action="store_true", help="Use/update real conversation history (off by default for CLI)")
    parser.add_argument("--list", action="store_true", help="List available relationships and exit")
    args = parser.parse_args()

    mm = MemoryManager()

    if args.list:
        print("Available relationships:")
        for r in mm.list_relationships():
            print(f"  - {r}")
        return

    if not args.relationship or not args.message:
        parser.print_help()
        sys.exit(1)

    if args.relationship not in mm.list_relationships():
        print(f"Note: '{args.relationship}' isn't in memory.json yet — will be treated as a fresh profile.\n", file=sys.stderr)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    mock = args.mock or not api_key
    if mock and not args.mock:
        print("Note: DEEPSEEK_API_KEY not set — running in --mock mode.\n", file=sys.stderr)

    result = get_wingman_response(
        relationship=args.relationship,
        message=args.message,
        api_key=api_key,
        mock=mock,
        memory_manager=mm,
        use_history=args.history,
    )
    print_response(args.relationship, args.message, result)


if __name__ == "__main__":
    main()
