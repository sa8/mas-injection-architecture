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

| Config | Purpose |
|---|---|
| `smoke_indirect_star.yaml` | No-sandbox plumbing smoke — confirms a composed indirect-injection × star-topology experiment executes and produces judge output. Not a result. |
