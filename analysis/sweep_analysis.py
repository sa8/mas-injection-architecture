"""Pre-registered analysis of the confirmatory sweep.

Implements the plan fixed in PREREGISTRATION.md ("Analysis plan for the
confirmatory sweep", 2026-08-10) exactly as written. Nothing here chooses a
stratum, an endpoint, or an n — those are read off the pre-registration.

Attack outcomes come from `rescore.score()` (tool events), NOT from Orbit's
scorer; see HANDOVER.md "Scoring". Samples whose attack outcome is None are
UNSCOREABLE (Orbit's persistence check did not run) and are excluded and
counted, never imputed.

Usage (from the Orbit clone, so `orbit` and `rescore` are importable):
    uv run --with statsmodels --with pandas python \
        ../mas-injection-pilot/analysis/sweep_analysis.py \
        standalone=dump_standalone.json star=dump_star.json mesh=dump_mesh.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rescore import _iter_logs, _meta, score  # noqa: E402

RNG = np.random.default_rng(20260811)
N_PERM = 10_000
N_BOOT = 10_000

# Pre-registered stratification (PREREGISTRATION.md, "Injection site").
SITE = {
    "issue_text": "prompt",
    "code_comment": "file",
    "readme": "file",
    "config_file": "file",
}


# ---------------------------------------------------------------- extraction

def load_arm(path: str) -> list[dict]:
    raw = Path(path).read_text()
    rows = []
    for log in _iter_logs(raw):
        for s in log.get("samples", []):
            r = score(s)
            vec = _meta(s, "code_ipi_injection_vector", "") or ""
            r["vector"] = vec
            r["site"] = SITE.get(vec, "?")
            r["category"] = _meta(s, "code_ipi_payload_category", "") or ""
            r["is_benign"] = bool(_meta(s, "code_ipi_is_benign", False))
            # sophistication is not a metadata key; it is the sample-id suffix
            r["soph"] = ("authority_framing" if r["id"].endswith("-authority")
                         else "plain" if r["id"].endswith("-plain") else "n/a")
            rows.append(r)
    return rows


# ------------------------------------------------------------------- helpers

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — behaves at 0/n and n/n, unlike Wald."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fmt_rate(k: int, n: int) -> str:
    if n == 0:
        return "n/a (0 scoreable)"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k/n:6.1%}  [95% CI {lo:.1%}, {hi:.1%}]"


def paired(arms: dict[str, list[dict]], a: str, b: str) -> list[dict]:
    """Tasks scoreable in BOTH arms, injected only. One row per task."""
    idx = {arm: {r["id"]: r for r in rows if not r["is_benign"]}
           for arm, rows in arms.items()}
    out, dropped = [], []
    for tid, ra in sorted(idx[a].items()):
        rb = idx[b].get(tid)
        if rb is None or ra["attack_success"] is None or rb["attack_success"] is None:
            dropped.append(tid)
            continue
        out.append({"id": tid, "site": ra["site"], "vector": ra["vector"],
                    "category": ra["category"], "soph": ra["soph"],
                    a: int(ra["attack_success"]), b: int(rb["attack_success"])})
    return out, dropped


def did(pairs: list[dict], a: str, b: str, ya=None, yb=None) -> float:
    """(p_b - p_a | prompt) - (p_b - p_a | file)."""
    ya = [p[a] for p in pairs] if ya is None else ya
    yb = [p[b] for p in pairs] if yb is None else yb
    out = {}
    for site in ("prompt", "file"):
        m = [i for i, p in enumerate(pairs) if p["site"] == site]
        if not m:
            return float("nan")
        out[site] = np.mean([yb[i] for i in m]) - np.mean([ya[i] for i in m])
    return out["prompt"] - out["file"]


# -------------------------------------------------------------------- primary

def primary(arms: dict[str, list[dict]]) -> None:
    a, b = "standalone", "star"
    pairs, dropped = paired(arms, a, b)

    print("\n" + "=" * 78)
    print("PRIMARY (pre-registered): arm x site interaction, standalone vs star")
    print("=" * 78)
    print(f"  paired injected tasks analysed: {len(pairs)}")
    if dropped:
        print(f"  EXCLUDED (unscoreable in >=1 arm), not imputed: {len(dropped)}")
        for t in dropped:
            print(f"      {t}")

    print("\n  attack success by arm x site")
    cells = {}
    for site in ("prompt", "file"):
        m = [p for p in pairs if p["site"] == site]
        for arm in (a, b):
            k = sum(p[arm] for p in m)
            cells[(arm, site)] = (k, len(m))
            print(f"    {site:<7} {arm:<11} {fmt_rate(k, len(m))}")

    d_prompt = (cells[(b, 'prompt')][0] / cells[(b, 'prompt')][1]
                - cells[(a, 'prompt')][0] / cells[(a, 'prompt')][1])
    d_file = (cells[(b, 'file')][0] / cells[(b, 'file')][1]
              - cells[(a, 'file')][0] / cells[(a, 'file')][1])
    obs = d_prompt - d_file
    print(f"\n    star - standalone | prompt = {d_prompt:+.1%}")
    print(f"    star - standalone | file   = {d_file:+.1%}")
    print(f"    difference-in-differences  = {obs:+.1%}   <-- primary statistic")

    # Permutation: exchange the arm label WITHIN task (preserves the pairing).
    null = np.empty(N_PERM)
    ya0 = np.array([p[a] for p in pairs])
    yb0 = np.array([p[b] for p in pairs])
    for i in range(N_PERM):
        swap = RNG.random(len(pairs)) < 0.5
        ya = np.where(swap, yb0, ya0)
        yb = np.where(swap, ya0, yb0)
        null[i] = did(pairs, a, b, ya, yb)
    p_two = float(np.mean(np.abs(null) >= abs(obs) - 1e-12))
    print(f"\n  permutation test ({N_PERM:,} draws, arm label swapped within task)")
    print(f"    two-sided p = {p_two:.4f}   (alpha = 0.05 -> "
          f"{'REJECT' if p_two < 0.05 else 'DO NOT REJECT'} the null)")

    # Bootstrap CI: resample tasks with replacement within each site stratum.
    by_site = {s: [i for i, p in enumerate(pairs) if p["site"] == s]
               for s in ("prompt", "file")}
    boot = np.empty(N_BOOT)
    for i in range(N_BOOT):
        d = {}
        for s, idxs in by_site.items():
            pick = RNG.choice(idxs, size=len(idxs), replace=True)
            d[s] = np.mean(yb0[pick]) - np.mean(ya0[pick])
        boot[i] = d["prompt"] - d["file"]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"    bootstrap 95% CI ({N_BOOT:,} draws, tasks resampled within stratum)"
          f" = [{lo:+.1%}, {hi:+.1%}]")

    mixed_effects(pairs, a, b)


def mixed_effects(pairs: list[dict], a: str, b: str) -> None:
    """success ~ arm * site + (1 | task). Reported as a CHECK, not the primary.

    The pre-registration anticipates non-convergence with ~35 tasks and rare
    events; a failure here is reported, not worked around.
    """
    print("\n  mixed-effects check: success ~ arm * site + (1 | task)")
    try:
        import pandas as pd
        import statsmodels.api as sm
        from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM

        long = pd.DataFrame([
            {"task": p["id"], "arm": arm, "site": p["site"], "y": p[arm]}
            for p in pairs for arm in (a, b)
        ])
        if long["y"].nunique() < 2:
            print("    not run: outcome is constant.")
            return
        md = BinomialBayesMixedGLM.from_formula(
            "y ~ arm * site", {"task": "0 + C(task)"}, long)
        res = md.fit_vb(verbose=False)
        print(f"    {'term':<28}{'post. mean':>12}{'post. SD':>11}")
        for nm, mu, sd in zip(res.model.exog_names, res.fe_mean, res.fe_sd):
            print(f"    {nm:<28}{mu:>12.3f}{sd:>11.3f}")
        inter = [n for n in res.model.exog_names if ":" in n]
        if inter:
            print(f"    interaction term = {inter[0]}  (sign/scale only; "
                  "variational SDs are optimistic — the permutation test is "
                  "the pre-registered inference)")
        sep = [s for s in ("prompt", "file")
               for arm in (a, b)
               if long[(long.site == s) & (long.arm == arm)]["y"].nunique() == 1]
        if sep:
            print("    NB: at least one arm x site cell is all-0 or all-1 "
                  "(separation) — coefficients on that cell are not "
                  "interpretable. This is the failure mode the pre-registration "
                  "anticipated; the permutation test carries no such assumption.")
        _ = sm
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(f"    DID NOT CONVERGE / failed to fit: {type(exc).__name__}: {exc}")
        print("    Reported as such. The primary inference is the permutation "
              "test, which does not depend on this model.")


# ------------------------------------------------------------------ secondary

def secondary(arms: dict[str, list[dict]]) -> None:
    print("\n" + "=" * 78)
    print("SECONDARY (pre-registered as secondary; CIs, not p-values;")
    print("no multiplicity correction)")
    print("=" * 78)

    inj = {arm: [r for r in rows if not r["is_benign"]] for arm, rows in arms.items()}

    def rate(rows, pred=lambda r: True):
        sel = [r for r in rows if pred(r) and r["attack_success"] is not None]
        return sum(bool(r["attack_success"]) for r in sel), len(sel)

    print("\n  H2 -- attack success by arm (injected only, all sites pooled)")
    for arm in arms:
        k, n = rate(inj[arm])
        print(f"    {arm:<11} {fmt_rate(k, n)}")
    if "chain" in arms:
        print("    NB: `chain` is EXPLORATORY, not confirmatory: it ran on a")
        print("        repaired executor, with its reader prompt edited, after")
        print("        the other arms' results were known (2026-08-12")
        print("        deviations in PREREGISTRATION.md).")
    if "mesh" in arms:
        print("    NB: `mesh` degenerated to a single agent -- coder_0 never")
        print("        delegated in any of the 45 samples, though the peer tools")
        print("        were offered in all 349 model calls. The arm is")
        print("        operationally standalone, so H2 (`mesh >= star`) was not")
        print("        actually tested by it; this row is not evidence on H2.")

    print("\n  attack success by arm x site")
    for site in ("prompt", "file"):
        for arm in arms:
            k, n = rate(inj[arm], lambda r, s=site: r["site"] == s)
            print(f"    {site:<7} {arm:<11} {fmt_rate(k, n)}")

    print("\n  paired differences vs standalone (bootstrap 95% CI over tasks)")
    for arm in arms:
        if arm == "standalone":
            continue
        pairs, dropped = paired(arms, "standalone", arm)
        for site in ("prompt", "file", "ALL"):
            m = [p for p in pairs if site == "ALL" or p["site"] == site]
            if not m:
                continue
            ya = np.array([p["standalone"] for p in m])
            yb = np.array([p[arm] for p in m])
            obs = yb.mean() - ya.mean()
            bs = np.empty(N_BOOT)
            for i in range(N_BOOT):
                pick = RNG.choice(len(m), size=len(m), replace=True)
                bs[i] = yb[pick].mean() - ya[pick].mean()
            lo, hi = np.percentile(bs, [2.5, 97.5])
            print(f"    {arm:<6} vs standalone | {site:<6} "
                  f"{obs:+6.1%}  [{lo:+.1%}, {hi:+.1%}]  (n={len(m)} tasks)")
        if dropped:
            print(f"      excluded from this pairing: {len(dropped)}")

    print("\n  four-way per-vector split (UNDERPOWERED at n=10; not a headline)")
    for vec in ("issue_text", "code_comment", "readme", "config_file"):
        for arm in arms:
            k, n = rate(inj[arm], lambda r, v=vec: r["vector"] == v)
            print(f"    {vec:<13} {arm:<11} {fmt_rate(k, n)}")

    print("\n  payload category")
    for cat in sorted({r["category"] for rows in inj.values() for r in rows}):
        for arm in arms:
            k, n = rate(inj[arm], lambda r, c=cat: r["category"] == c)
            print(f"    {cat:<15} {arm:<11} {fmt_rate(k, n)}")

    print("\n  payload sophistication")
    for s in ("plain", "authority_framing"):
        for arm in arms:
            k, n = rate(inj[arm], lambda r, x=s: r["soph"] == x)
            print(f"    {s:<18} {arm:<11} {fmt_rate(k, n)}")

    print("\n  H4 -- benign-control task completion (the 10 benign samples)")
    for arm, rows in arms.items():
        ben = [r for r in rows if r["is_benign"]]
        k = sum(bool(r["benign_completed"]) for r in ben)
        print(f"    {arm:<11} {fmt_rate(k, len(ben))}")
    print("\n  H4 -- task completion on ALL 45 samples (capability check)")
    for arm, rows in arms.items():
        k = sum(bool(r["benign_completed"]) for r in rows)
        print(f"    {arm:<11} {fmt_rate(k, len(rows))}")

    print("\n  H3 -- detection: NOT REPORTED.")
    print("    PREREGISTRATION.md gates it on round 2 of the blind grader")
    print("    validation, which requires a human labeller and has not passed.")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    arms = {}
    for arg in sys.argv[1:]:
        arm, _, path = arg.partition("=")
        arms[arm] = load_arm(path)

    print("=" * 78)
    print("CONFIRMATORY SWEEP — code_ipi, claude-haiku-4-5, 1 epoch")
    print("=" * 78)
    for arm, rows in arms.items():
        unsc = [r for r in rows if r["attack_success"] is None]
        print(f"  {arm:<11} samples={len(rows):<4} "
              f"injected={sum(not r['is_benign'] for r in rows):<4} "
              f"benign={sum(r['is_benign'] for r in rows):<4} "
              f"unscoreable={len(unsc)}")
        for r in unsc:
            print(f"      EXCLUDED {r['id']}")

    out = Path(__file__).resolve().parent / "sweep_rows.csv"
    with out.open("w") as fh:
        fh.write("arm,id,vector,site,category,sophistication,is_benign,"
                 "attack_success,benign_completed\n")
        for arm, rows in arms.items():
            for r in rows:
                atk = "" if r["attack_success"] is None else int(r["attack_success"])
                fh.write(f"{arm},{r['id']},{r['vector']},{r['site']},{r['category']},"
                         f"{r['soph']},{int(r['is_benign'])},{atk},"
                         f"{int(r['benign_completed'])}\n")
    print(f"\n  per-sample rows written to {out}")

    primary(arms)
    secondary(arms)


if __name__ == "__main__":
    main()
