# LLM Providers — PKA

PKA is LLM-agnostic by design. Work happens through two surfaces: **interactive frontends** that read/write the markdown corpus directly, and **API calls** from tools routed through `tools/llm.py`. Under federation (see `docs/federation.md`) each machine chooses its own providers and credentials.

## Interactive frontends (no plumbing required)

The PKA corpus is plain markdown on disk. Any agent frontend can read and write it directly by pointing at the repo root:

- **Claude app / Claude Code** — native; the `.claude/agents/` specialist definitions are already in the repo.
- **ChatGPT app / Codex** — point at the repo; `.codex-work/` artifacts are already present.
- **OpenWorker** — point at the repo root as the working directory.
- **Antigravity** — same; treat the markdown corpus as the working set.
- **Cursor / other coding agents** — point at `active/PKA/` or the relevant subproject.

These frontends do not need `llm.py`. They edit files directly; durable writes to the DB go through the `tools/` CLIs as always.

## API calls from tools — `tools/llm.py`

A thin provider router. Callers pick a provider explicitly or fall back to `LLM_PROVIDER`.

```
chat_text(prompt, *, system=None, provider=None, model=None, ...) -> str
chat_json(prompt, *, system=None, provider=None, model=None, ...) -> Any
chat(messages, *, provider=None, model=None, ...) -> dict
```

### Currently supported providers

| Provider | Module | Key env var | Notes |
|----------|--------|-------------|-------|
| `glm` | `tools/glm.py` | `GLM_API_KEY` | Zhipu/Z.AI. Set on this machine. |
| `xai` | `tools/xai.py` | `XAI_API_KEY` | Grok "builder lane" for cheap drafts. |
| `lmstudio` | `tools/lmstudio.py` | none (local) | OpenAI-compatible local server. Default provider. |

### Env vars

- `LLM_PROVIDER` — default provider when a caller doesn't specify (default `lmstudio`).
- `LLM_FALLBACK` — optional provider to try if the primary raises (default: none).

### Adding a provider (e.g. Anthropic, OpenAI)

When an API key becomes available, adding a provider follows the established pattern:

1. Create `tools/<provider>.py` exposing `chat(messages, *, model, temperature, max_tokens, response_format, label) -> dict` (mirror `tools/xai.py` or `tools/glm.py`).
2. Add the provider name to `SUPPORTED_PROVIDERS` in `tools/llm.py`.
3. Add a dispatch branch in `chat()` (and the `_call_text`/`_call_json` helpers) in `tools/llm.py`.
4. Put the key in `discord-bridge/.env` (gitignored) or the environment. Never commit keys.
5. Add a `health` subcommand to the new helper for the auth doctor.

**Anthropic** (`ANTHROPIC_API_KEY`) and **OpenAI** (`OPENAI_API_KEY`) adapters are not yet wired because no keys are present on this machine. They are additive and low-risk to add when needed — the router and module pattern are already in place.

## Standalone helper CLIs

Some providers also have a direct CLI wrapper usable independently of `llm.py`:

- `tools/glm.py chat --prompt "..."` / `tools/glm.py health`
- `tools/xai.py chat --prompt "..."` / `tools/xai.py draft --file ... --task ...`
- `tools/lmstudio.py models` / `tools/lmstudio.py chat --prompt "..."`
- `tools/notebooklm.py ask --notebook <name> "<question>"` (separate auth profile)

## Credential safety

- Keys live in gitignored `discord-bridge/.env` or the environment — never in `config/machine.json` (tracked) or committed files.
- Rotate Discord tokens with `tools/rotate_discord_token.py`; never hand-edit `.env` in a text editor that may leak the value.
- Per `AGENTS.md`, never commit secrets.
