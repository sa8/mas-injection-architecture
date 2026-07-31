"""Driver: run K trials of each topology on the injection scenario, score them,
log one JSONL row per trial, and print an attack/benign/detection summary.

    # smoke-test the whole pipeline with no API calls, sweeping mock policy:
    python run.py --backend mock --mock-policy comply --trials 1
    python run.py --backend mock --mock-policy refuse --trials 1

    # real run against a live model:
    python run.py --backend anthropic --model claude-sonnet-5 --trials 20
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from agents import MockBackend, build_backend
from judge import LLMJudge, heuristic_detector, judge, outcome_dict
from scenario import BENIGN_TASK, INJECTION_TEXT, SESSION_TOKEN, build_environment
from topologies import run_chain, run_mesh, run_standalone, run_star

ROLES = ["orchestrator", "navigate", "scroll", "fill", "click", "standalone"]
ALL_TOPOLOGIES = ["standalone", "star", "chain", "mesh"]


def make_backend(kind: str, model: str, mock_policy: str):
    if kind == "mock":
        return MockBackend(policy=mock_policy, token=SESSION_TOKEN)
    return build_backend({"backend": kind, "model": model})


def default_model(kind: str, model: str) -> str:
    """--model defaults to a Claude id; swap in a sane local default for ollama
    so `--backend ollama` alone does the right thing."""
    if kind == "ollama" and model.startswith("claude"):
        return "qwen2:7b"
    return model


def run_one(topology: str, kind: str, model: str, mock_policy: str, max_steps: int, detector):
    env = build_environment()
    b = lambda: make_backend(kind, model, mock_policy)  # noqa: E731  fresh instance per role
    if topology == "standalone":
        traj = run_standalone(env, BENIGN_TASK, b(), max_steps)
    elif topology == "star":
        specialists = {r: b() for r in ("navigate", "scroll", "fill", "click")}
        traj = run_star(env, BENIGN_TASK, b(), specialists, max_steps)
    elif topology == "chain":
        agents = {r: b() for r in ("navigate", "scroll", "fill", "click")}
        traj = run_chain(env, BENIGN_TASK, agents, max_steps)
    elif topology == "mesh":
        peers = {r: b() for r in ("navigate", "scroll", "fill", "click")}
        traj = run_mesh(env, BENIGN_TASK, peers, max_steps)
    else:
        raise ValueError(f"unknown topology {topology!r}")
    return traj, judge(traj, env, detector)


def build_detector(kind: str, judge_mode: str, judge_model: str):
    """Heuristic by default. The LLM judge needs an API backend, so it's only
    built when explicitly asked for AND we aren't in a no-API mock run."""
    if judge_mode == "llm" and kind == "mock":
        print("note: --judge llm ignored for --backend mock (no API); using heuristic")
        return heuristic_detector
    if judge_mode == "llm":
        return LLMJudge(build_backend({"backend": "anthropic", "model": judge_model}),
                        injection_text=INJECTION_TEXT)
    return heuristic_detector


def main() -> None:
    ap = argparse.ArgumentParser(description="Topology-gating injection pilot.")
    ap.add_argument("--backend", default="mock", choices=["mock", "anthropic", "ollama"])
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--mock-policy", default="comply", choices=["benign", "comply", "refuse"])
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--topologies", nargs="+", default=ALL_TOPOLOGIES)
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--judge", default="heuristic", choices=["heuristic", "llm"])
    ap.add_argument("--judge-model", default="claude-haiku-4-5")
    ap.add_argument("--out", default="logs")
    args = ap.parse_args()

    args.model = default_model(args.backend, args.model)
    detector = build_detector(args.backend, args.judge, args.judge_model)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"mock-{args.mock_policy}" if args.backend == "mock" else args.model.replace(":", "-")
    out_path = Path(args.out) / f"pilot_{tag}_{stamp}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    agg: dict[str, dict[str, int]] = {
        t: {"attack": 0, "tried": 0, "benign": 0, "detect": 0, "n": 0, "noop": 0, "steps": 0}
        for t in args.topologies
    }

    with open(out_path, "w", encoding="utf-8") as f:
        for topology in args.topologies:
            for trial in range(args.trials):
                traj, outcome = run_one(topology, args.backend, args.model,
                                        args.mock_policy, args.max_steps, detector)
                row = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "backend": args.backend, "model": tag, "trial": trial,
                    **outcome_dict(outcome),
                    "steps": [
                        {"agent": s.agent, "action": s.action, "result": s.result}
                        for s in traj.steps
                    ],
                }
                f.write(json.dumps(row) + "\n")
                a = agg[topology]
                a["attack"] += outcome.attack_success
                a["tried"] += outcome.attack_attempted
                a["benign"] += outcome.benign_success
                a["detect"] += outcome.detected
                a["noop"] += outcome.n_noop
                a["steps"] += outcome.n_steps
                a["n"] += 1

    print(f"\nwrote {out_path}")
    print(f"\n{'topology':<12}{'tried%':>8}{'attack%':>9}{'benign%':>9}"
          f"{'detect%':>9}{'noop%':>8}{'n':>5}")
    print("-" * 60)
    for t in args.topologies:
        a = agg[t]
        n = max(a["n"], 1)
        noop_pct = 100 * a["noop"] / max(a["steps"], 1)
        print(f"{t:<12}{100*a['tried']/n:>8.1f}{100*a['attack']/n:>9.1f}"
              f"{100*a['benign']/n:>9.1f}{100*a['detect']/n:>9.1f}"
              f"{noop_pct:>8.1f}{a['n']:>5}")
    print("\ntried%  = agent moved the secret toward the sink (complied), even if it missed")
    print("attack% = secret actually reached the public sink (completed)")
    print("noop%   = unparseable JSON. High = protocol failure, not a security result.")


if __name__ == "__main__":
    main()
