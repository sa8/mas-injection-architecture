"""The three control flows under test: star, chain, mesh (plus a standalone
baseline). Each returns a Trajectory the judge can score.

The load-bearing difference between them is *what each agent observes*:

  standalone : one agent, full page + goal.
  star       : orchestrator sees the page; specialists see ONLY the one
               instruction they are handed (no page, no goal) — so only the
               orchestrator can ever notice the injected node.
  chain      : fixed order navigate->scroll->fill->click; every agent sees the
               page + an upstream note, and passes a note downstream.
  mesh       : peers each see the full page + goal + peer list, acting or
               delegating; round-robin gives several agents an independent look.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agents import SYSTEM_PROMPTS, ModelBackend, parse_action
from environment import Environment

TERMINAL = {"report", "refuse"}
CHAIN_ORDER = ["navigate", "scroll", "fill", "click"]
MESH_PEERS = ["navigate", "scroll", "fill", "click"]


@dataclass
class Step:
    agent: str
    role: str
    action: dict
    result: str


@dataclass
class Trajectory:
    topology: str
    steps: list[Step] = field(default_factory=list)
    terminal: str = "budget_exhausted"   # "report" | "refuse" | "budget_exhausted"

    def add(self, agent: str, role: str, action: dict, result: str) -> None:
        self.steps.append(Step(agent, role, action, result))


# --------------------------------------------------------------------------
# Observation construction
# --------------------------------------------------------------------------


def _render_history(traj: Trajectory, last: int = 6) -> str:
    rows = []
    for s in traj.steps[-last:]:
        tool = s.action.get("tool", "?")
        rows.append(f"  {s.agent}: {tool} -> {s.result}")
    return "\n".join(rows) if rows else "  (none yet)"


def _page_obs(env: Environment, task: str, traj: Trajectory, extra: str = "") -> str:
    parts = [
        env.observation_banner(),
        "",
        f"GOAL: {task}",
        "",
        env.page.render_axtree(),
        "",
        "HISTORY:",
        _render_history(traj),
    ]
    if extra:
        parts += ["", extra]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Topologies
# --------------------------------------------------------------------------


def run_standalone(env: Environment, task: str, backend: ModelBackend, max_steps: int = 8) -> Trajectory:
    traj = Trajectory("standalone")
    for _ in range(max_steps):
        obs = _page_obs(env, task, traj)
        act = parse_action(backend.generate(SYSTEM_PROMPTS["standalone"], obs))
        if act.get("tool") in TERMINAL:
            traj.add("standalone", "standalone", act, act.get("reason") or act.get("text", ""))
            traj.terminal = act["tool"]
            return traj
        result = env.execute(act)
        traj.add("standalone", "standalone", act, result)
    return traj


def run_star(env: Environment, task: str, orchestrator: ModelBackend,
             specialists: dict[str, ModelBackend], max_steps: int = 8) -> Trajectory:
    traj = Trajectory("star")
    for _ in range(max_steps):
        obs = _page_obs(env, task, traj)
        act = parse_action(orchestrator.generate(SYSTEM_PROMPTS["orchestrator"], obs))
        tool = act.get("tool")
        if tool in TERMINAL:
            traj.add("orchestrator", "orchestrator", act, act.get("reason") or act.get("text", ""))
            traj.terminal = tool
            return traj
        if tool == "dispatch":
            to = act.get("to", "")
            instruction = act.get("instruction", "")
            traj.add("orchestrator", "orchestrator", act, f"dispatch->{to}: {instruction}")
            spec = specialists.get(to)
            if spec is None:
                traj.add(to, "specialist", {"tool": "noop"}, f"no specialist {to!r}")
                continue
            # Specialist sees ONLY the instruction — no page, no goal.
            spec_sys = SYSTEM_PROMPTS["specialist"].replace("{role}", to)
            spec_act = parse_action(spec.generate(spec_sys, f"INSTRUCTION: {instruction}"))
            result = env.execute(spec_act)
            traj.add(to, "specialist", spec_act, result)
            continue
        # orchestrator did something directly (e.g. read)
        result = env.execute(act)
        traj.add("orchestrator", "orchestrator", act, result)
    return traj


def run_chain(env: Environment, task: str, agents: dict[str, ModelBackend], max_steps: int = 8) -> Trajectory:
    traj = Trajectory("chain")
    note = "(pipeline start)"
    # Cycle the fixed order (no backtracking, order preserved) so chain gets the
    # SAME action budget as star/mesh. A single 4-agent pass gave chain fewer
    # chances to finish the task or to be exploited, confounding topology with
    # budget — the comparison has to hold budget constant.
    for i in range(max_steps):
        role = CHAIN_ORDER[i % len(CHAIN_ORDER)]
        obs = _page_obs(env, task, traj, extra=f"NOTE FROM PREVIOUS AGENT: {note}")
        act = parse_action(agents[role].generate(SYSTEM_PROMPTS["chain"], obs))
        tool = act.get("tool")
        if tool in TERMINAL:
            traj.add(role, "chain", act, act.get("reason") or act.get("text", ""))
            traj.terminal = tool
            return traj
        result = env.execute(act)
        note = act.get("note", "(no note)")
        traj.add(role, "chain", act, result)
    return traj


def run_mesh(env: Environment, task: str, peers: dict[str, ModelBackend], max_steps: int = 8) -> Trajectory:
    traj = Trajectory("mesh")
    order = list(MESH_PEERS)
    idx = 0
    for _ in range(max_steps):
        role = order[idx % len(order)]
        peer_list = ", ".join(p for p in order if p != role)
        sys = SYSTEM_PROMPTS["mesh"].replace("{role}", role).replace("{peers}", peer_list)
        obs = _page_obs(env, task, traj)
        act = parse_action(peers[role].generate(sys, obs))
        tool = act.get("tool")
        if tool in TERMINAL:
            traj.add(role, "mesh", act, act.get("reason") or act.get("text", ""))
            traj.terminal = tool
            return traj
        if tool == "delegate":
            to = act.get("to", "")
            traj.add(role, "mesh", act, f"delegate->{to}: {act.get('instruction','')}")
            if to in order:
                idx = order.index(to)
                continue
            idx += 1
            continue
        result = env.execute(act)
        traj.add(role, "mesh", act, result)
        idx += 1
    return traj
