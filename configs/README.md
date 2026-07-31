# configs/

Orbit experiment configs for the confirmatory phase. **Orbit is a dependency,
not part of this repo** — clone it beside this repo and run configs by path:

```bash
git clone https://github.com/wlanderson0/orbit   # once, as a sibling dir
cd orbit && uv sync
uv run orbit run ../mas-injection-architecture/configs/<config>.yaml -m <model>
```

Use a free local model for development (`-m ollama/qwen2:7b`) and a capable
model for the real runs. See each config's header for specifics.

## Topology coverage (code_ipi)

Orbit ships presets for standalone/star/mesh; **chain is not a first-class
preset** but is constructible via `direct_run` edges (see `code_ipi_chain.yaml`).

| Arm | Config |
|---|---|
| standalone | Orbit preset `code_ipi/presets/single_agent.yaml` |
| star (isolated specialists) | Orbit preset `code_ipi/presets/star_specialists.yaml` |
| chain (reader→fixer→tester) | `code_ipi_chain.yaml` (this repo) |
| mesh | Orbit preset `code_ipi/presets/mesh_delegation.yaml` (or `mesh_round_robin.yaml`) |

## Configs in this repo

| Config | Purpose |
|---|---|
| `code_ipi_chain.yaml` | The chain arm Orbit lacks a preset for — linear `direct_run` pipeline. Verify ordering on a capable model before trusting. |
| `smoke_indirect_star.yaml` | No-sandbox plumbing smoke (early spike). Not a result. |
