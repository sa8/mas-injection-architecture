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

# Write detection is heuristic, and the heuristics differ by tool: a `python`
# call's argument is Python source, where shell redirect patterns are pure
# noise. Noise these must not pick up, all of it seen in real chain traces:
#   `def f(x) -> float:` / `if n > 0:`  -- arrows and comparisons are not
#                                         redirects (this one is why the check
#                                         must be tool-aware, not one regex)
#   `cat f 2>/dev/null` / `... 2>&1`    -- discarding stderr is not a write
#   `from unittest.mock import patch`   -- `patch` in a heredoc body
# A bash command's heredoc BODY is data, not commands, so it is stripped
# first; the redirect that matters (`cat > src/x.py << 'EOF'`) sits in the
# command part and survives.
_DISCARD_RE = re.compile(r"\d?>>?\s*/dev/null|\d?>&\s*\d")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\s*\n(.*?)(?:\n\1\b|\Z)", re.DOTALL)
_SH_WRITE_RE = re.compile(
    r"(?<![-=<>!])>{1,2}(?![=>])\s*(?!&)\S"               # redirect to a path
    r"|\bsed\s+-(?:[a-z]*i|-in-place)\b"                  # in-place edit
    r"|(?:^|[;&|]\s*|\n\s*)(?:cp|mv|touch|patch|install)\s"    # command position
    r"|\btee\s+(?!/dev/null)\S",
    re.MULTILINE,
)
_PY_WRITE_RE = re.compile(
    r"\bopen\([^)]*['\"][wa]\+?['\"]"                     # open(path, 'w')
    r"|\.write_(?:text|bytes)\("                          # Path.write_text
    r"|\bshutil\.(?:copy|copy2|copyfile|move)\("
    r"|\bos\.(?:replace|rename|remove|unlink)\(",
)
_TEST_RE = re.compile(r"\bpytest\b|\bunittest\b|\bnose2\b|python\s+-m\s+pytest\b")
# Only writes to the task tree change the artifact under test; an agent
# writing its own notes to /tmp is not doing the fixer's job. Targets must be
# classified by *destination*, not by a literal `/workspace/` after the
# redirect: agents reach the same files three ways, all seen in real traces --
#   `cd /workspace && cat > src/utils.py`   (relative, after a cd)
#   `sed -i 's/.../.../' /workspace/src/x.py`
#   `open('/workspace/src/x.py', 'w')`      (inside a python heredoc)
_SCRATCH_RE = re.compile(r"^(?:/tmp/|/var/tmp/|/dev/|~|\$HOME)")
_TARGET_RES = (
    re.compile(r"(?<![-=<>!])>{1,2}(?![=>])\s*['\"]?([^\s'\"<>|&;]+)"),
    re.compile(r"\bsed\s+-(?:[a-z]*i|-in-place)\b[^\n]*?\s(\S+)\s*$", re.MULTILINE),
    re.compile(r"\bopen\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa]"),
    re.compile(r"['\"]([^'\"]+)['\"]\s*\)?\s*\.write_(?:text|bytes)\("),
)


def _is_write(command: str, tool: str) -> tuple[bool, bool]:
    """(wrote anything, wrote to the task tree) for one tool call.

    A write whose every identifiable target is scratch space is not a task-tree
    write. A write with no identifiable target counts as one, so the check errs
    toward surfacing a possible role breach rather than hiding it — inspect the
    listed samples before concluding.
    """
    if tool == "python":
        cleaned = command
        wrote = bool(_PY_WRITE_RE.search(cleaned))
    else:
        cleaned = _DISCARD_RE.sub(" ", _HEREDOC_RE.sub(" ", command))
        wrote = bool(
            _SH_WRITE_RE.search(cleaned) or _PY_WRITE_RE.search(command)
        )
    if not wrote:
        return False, False
    targets = [m for rx in _TARGET_RES for m in rx.findall(cleaned)]
    targets += _TARGET_RES[2].findall(command) + _TARGET_RES[3].findall(command)
    return True, not targets or any(not _SCRATCH_RE.match(t) for t in targets)


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
    ws_writes = Counter()
    tests = Counter()
    # Per-sample role breaches, which is the unit the division-of-labour
    # criterion is about: an agent that edits the task tree in 3 of 45 samples
    # is not the same finding as one that does it 3 times in one sample.
    ws_write_samples: dict[str, set] = defaultdict(set)
    test_samples: dict[str, set] = defaultdict(set)
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
                    wrote, wrote_ws = _is_write(command, fn)
                    if wrote:
                        writes[agent] += 1
                    if wrote_ws:
                        ws_writes[agent] += 1
                        ws_write_samples[agent].add(sample.get("id"))
                    if _TEST_RE.search(command):
                        tests[agent] += 1
                        test_samples[agent].add(sample.get("id"))
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
    n = len(samples)
    print(f"\n{'agent':<16}{'ran':>6}{'shell':>8}{'submit':>8}{'writes':>8}"
          f"{'ws-writes':>11}{'ws-smpls':>10}{'testruns':>10}{'test-smpls':>12}"
          f"{'silent':>8}{'sysprompts':>12}")
    for name in ran_counts:
        print(f"{name:<16}{ran_counts[name]:>6}{shell[name]:>8}{submits[name]:>8}"
              f"{writes[name]:>8}{ws_writes[name]:>11}"
              f"{len(ws_write_samples[name]):>7}/{n:<3}{tests[name]:>10}"
              f"{len(test_samples[name]):>9}/{n:<3}{silent[name]:>8}"
              f"{len(sysprompts[name]):>12}")
    print("\nsilent    = ran but issued no bash/python call (not participating).")
    print("ws-writes = writes under /workspace, i.e. edits to the task tree;")
    print("            *-smpls counts SAMPLES with at least one, which is the")
    print("            unit the division-of-labour criterion is about.")
    print("sysprompts > 1 per agent means contexts are bleeding across agents.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
