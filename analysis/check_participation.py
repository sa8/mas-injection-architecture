"""Participation check for a topology arm — read from the eval EVENTS.

Orbit's run summary cannot be used to decide whether a topology arm actually
ran. It reported `Status: SUCCESS`, `Errors: 0/1` and a full score line for a
chain dry run in which two of three configured agents never executed and the
second agent's only model call returned HTTP 400; the failure appeared solely
as a `logger` event inside the `.eval` file. Run this before believing any
multi-agent arm.

Checks, per the chain verification bar:
  1. which agents executed, in what order (agent `span_begin` events)
  2. real bash/python calls attributed to each agent, and its submit calls
  3. division of labour: writes and test runs per agent, so an "analysis only"
     agent that edits files or runs the suite is visible
  4. distinct system prompts per agent's model calls -- more than one means
     contexts are bleeding across agents
  5. `logger` events at error level

Agent attribution walks the span tree, so a sub-agent's own shell commands are
counted (they never reach the top-level message stream). Usage:

    uv run inspect log dump logs/<dir>/*.eval > /tmp/d.json
    uv run python ../mas-injection-pilot/analysis/check_participation.py /tmp/d.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict

SHELL_FNS = {"bash", "python"}

# A write is a shell redirection into a path or an in-place editor invocation.
_WRITE_RE = re.compile(
    r">\s*/|>>\s*/|\btee\b|\bsed\s+-i\b|\b(?:cp|mv|touch|patch)\b"
    r"|\bopen\([^)]*['\"][wa]",
)
_TEST_RE = re.compile(r"\bpytest\b|\bunittest\b|\bnose2\b|python\s+-m\s+pytest\b")


def _resolve(val, attachments: dict) -> str:
    """Resolve an `attachment://<hash>` reference to its stored content."""
    s = val if isinstance(val, str) else json.dumps(val)
    if s.startswith("attachment://"):
        return str(attachments.get(s[len("attachment://"):], s))
    return s


def _spans(sample: dict) -> dict:
    return {
        e["id"]: e for e in sample.get("events", [])
        if (e.get("event") or e.get("type")) == "span_begin"
    }


def _owning_agent(span_id, spans: dict) -> str | None:
    """Walk up to the enclosing agent span. None under a scorer span (the
    grader's own model calls) or outside any agent."""
    seen: set = set()
    while span_id and span_id in spans and span_id not in seen:
        seen.add(span_id)
        event = spans[span_id]
        stype = event.get("type")
        if stype == "agent":
            return event.get("name")
        if stype == "scorer":
            return None
        span_id = event.get("parent_id")
    return None


def _command(event: dict, attachments: dict) -> str:
    args = event.get("arguments") or {}
    raw = args.get("command") or args.get("code") or args.get("cmd") or ""
    return _resolve(raw, attachments)


def main(path: str) -> int:
    log = json.load(open(path))
    samples = log["samples"]

    ran_counts: Counter = Counter()
    per_sample_distinct: Counter = Counter()
    orders: Counter = Counter()
    shell = Counter()
    submits = Counter()
    writes = Counter()
    tests = Counter()
    sysprompts: dict[str, set] = defaultdict(set)
    silent: Counter = Counter()  # ran but issued no shell call
    logger_errors = 0

    for sample in samples:
        spans = _spans(sample)
        attachments = sample.get("attachments") or {}
        order = [
            e.get("name") for e in sample.get("events", [])
            if (e.get("event") or e.get("type")) == "span_begin"
            and e.get("type") == "agent"
        ]
        orders[" -> ".join(order)] += 1
        for name in set(order):
            ran_counts[name] += 1
        per_sample_distinct[len(set(order))] += 1

        sample_shell: Counter = Counter()
        for event in sample.get("events", []):
            kind = event.get("event") or event.get("type")
            if kind == "logger" and "error" in str(event.get("message", "")):
                logger_errors += 1
                continue
            agent = _owning_agent(event.get("span_id"), spans)
            if agent is None:
                continue
            if kind == "tool":
                fn = event.get("function")
                if fn in SHELL_FNS:
                    command = _command(event, attachments)
                    sample_shell[agent] += 1
                    shell[agent] += 1
                    if _WRITE_RE.search(command):
                        writes[agent] += 1
                    if _TEST_RE.search(command):
                        tests[agent] += 1
                elif fn == "submit":
                    submits[agent] += 1
            elif kind == "model":
                for message in event.get("input") or []:
                    if message.get("role") != "system":
                        continue
                    content = message.get("content")
                    text = (
                        _resolve(content, attachments) if isinstance(content, str)
                        else " ".join(
                            _resolve(p.get("text", ""), attachments)
                            for p in content or []
                        )
                    )
                    sysprompts[agent].add(text)
        for name in set(order):
            if not sample_shell[name]:
                silent[name] += 1

    print(f"samples: {len(samples)}   logger errors: {logger_errors}")
    print(f"distinct agents per sample: {dict(sorted(per_sample_distinct.items()))}")
    print("execution orders:")
    for order, count in orders.most_common():
        print(f"  {count:>4}x  {order or '(none)'}")
    print(f"\n{'agent':<16}{'ran':>6}{'shell':>8}{'submit':>8}"
          f"{'writes':>8}{'testruns':>10}{'silent':>8}{'sysprompts':>12}")
    for name in ran_counts:
        print(f"{name:<16}{ran_counts[name]:>6}{shell[name]:>8}{submits[name]:>8}"
              f"{writes[name]:>8}{tests[name]:>10}{silent[name]:>8}"
              f"{len(sysprompts[name]):>12}")
    print("\nsilent = ran but issued no bash/python call (not participating).")
    print("sysprompts > 1 per agent means contexts are bleeding across agents.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
