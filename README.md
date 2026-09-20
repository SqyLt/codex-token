<p align="center">
  <img src="docs/banner.png" alt="codex-token — real-time token usage and prompt-cache hit-rate monitor for Codex" width="860">
</p>

<p align="center">
  <b>Real-time token usage and prompt-cache hit-rate monitor for Codex</b><br>
  Terminal panel &nbsp;·&nbsp; Local web dashboard &nbsp;·&nbsp; Desktop companion window
</p>

<p align="center">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-3fb950?style=flat-square">
  <img alt="platform" src="https://img.shields.io/badge/platform-Linux%20%2F%20X11-4c9aff?style=flat-square">
  <img alt="node" src="https://img.shields.io/badge/node-18%2B-3fb950?style=flat-square">
  <img alt="python" src="https://img.shields.io/badge/python-3.8%2B-4c9aff?style=flat-square">
  <img alt="dependencies" src="https://img.shields.io/badge/dependencies-none-3fb950?style=flat-square">
</p>

<p align="center">
  <a href="README.zh-CN.md">中文说明</a>
</p>

---

## Why

Codex shows you this:

```
Token usage: total=138,315  input=137,998 (+ 137,728 cached)  output=187
```

What it never shows is **how much of that input was actually served from the prompt cache** — the number that decides whether you pay full price or the cached-input rate. In agentic sessions every turn re-sends the whole conversation, so that share is usually 95–99%, and it is completely invisible while it quietly decides your bill.

`codex-token` reads Codex's own session files and shows it live:

| Scope | What you get |
| --- | --- |
| **Per call** | cache hit-rate of the last model call, plus a 40-call trend sparkline |
| **Per session** | cumulative hit-rate, input / cached / uncached / output split, call count |
| **Across sessions** | hit-rate ranking of every recent session |

## Screenshots

**Desktop companion window** — follows the Codex app, and steps aside when another window would be covered:

![Desktop companion window](docs/panel.png)

**Terminal panel** — `codex-token --once`, or a live view with `codex-token`:

![Terminal panel](docs/cli.png)

**Web dashboard** — run `dashboard-server.mjs`, then open <http://127.0.0.1:8788/>:

![Web dashboard](docs/dashboard.png)

## Features

- **The metric Codex doesn't show** — prompt-cache hit-rate, per call / per turn / per session
- **Spot regressions at a glance** — a sparkline of the last 40 calls; a prompt change that invalidates the cache is visible immediately
- **Three front-ends, one parser** — terminal, local web dashboard, desktop panel
- **Zero dependencies** — Node built-ins plus the Python standard library. No npm install, no pip install, no lockfile
- **Read-only and offline** — reads `~/.codex/sessions/**/rollout-*.jsonl`. No credentials, no network calls, nothing leaves the machine
- **Scriptable** — `--json`, `--compact`, `--log calls.jsonl`, `--alert-below 90`

## Install

Requirements: Node.js 18+, Python 3 with `tkinter`, and `xdotool` / `xprop` / `xrandr` for the desktop panel.

```bash
git clone https://github.com/SqyLt/codex-token.git
cd codex-token
./install.sh              # installs the commands and the app-menu entry
./install.sh --autostart  # optional: open the panel whenever Codex starts
```

## Usage

```bash
codex-token                     # live terminal panel
codex-token --all               # hit-rate overview of every recent session
codex-token --compact           # one line, for tmux status bars
codex-token --json              # machine-readable snapshot
codex-token --alert-below 90 --log calls.jsonl
codex-token-window              # open the desktop panel
```

Extra flags: `--days N` (window for `--all`), `--session <id prefix>`, `--include-subagents`, `--interval <ms>`, `--file <rollout.jsonl>`.

## How the hit-rate is computed

```
hit-rate = cached_input_tokens / input_tokens
```

`input_tokens` is the whole prompt; `cached_input_tokens` is the slice the provider served from cache; `input − cached` is the uncached input. Do **not** use `cached / (cached + uncached)` — that double-counts.

Every value is taken from the provider's own usage report inside Codex's session files. Nothing is estimated.

### Why it is almost always 98–99%

That is simply what prompt caching looks like in an agentic loop: each call re-sends the entire context, the prefix is unchanged, so almost all of it is a cache hit and only the newly appended content is uncached. Measured across 1,099 calls in 19 real sessions: median **99.8%**, and only **6.5%** of calls below 90%. The tell-tale sign that the number is real is the **first call of a session**, which lands anywhere between 22% and 93% and then climbs.

Two caveats worth knowing:

- the session figure is **input-weighted**, so in a long session it converges to ~99% and hides regressions — watch the per-call value and the sparkline instead;
- `cache_write_input_tokens` is not reported by every provider (DeepSeek-compatible endpoints report `0`), so that card is only meaningful with OpenAI-style APIs.

## FAQ

**Does it read or upload my conversations?**
It reads usage counters (`token_usage_record`, `token_count`, `session_meta`) from local JSONL files. Nothing is uploaded, and no credentials are used.

**The desktop panel vanished — is it broken?**
No. It hides itself when another window would overlap it, because on this compositor an override-redirect window cannot be stacked *behind* a normal window. It returns when you focus Codex, or when you pick **Codex 用量窗口** from the app menu.

**Do I need the desktop panel?**
No. The CLI and the web dashboard are fully independent of it.

## Limitations

- The desktop panel is Linux/X11 specific (`xdotool`, `xprop`, `xrandr`); the CLI and the web dashboard are portable.
- Parsing targets the rollout format of Codex CLI 0.153.x — a format change needs a small update in `codex-usage.mjs`.
- Token counts come from the provider, not from this tool; it only reports what the API says.

## License

MIT — see [LICENSE](LICENSE).

---

If this saves you from flying blind on what Codex actually costs, a ⭐ helps other people find it.
