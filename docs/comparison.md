# How muselab compares

> [简体中文](comparison_zh.md)

These tables are provided to help you determine quickly whether muselab fits
your use case, or whether one of the alternatives is a better match.

**Review date: 2026-09-08.** This is a comparison of product scope, not a
cross-product benchmark. This update checked CloudCLI's public repository and
package dependencies; other product entries have not received a complete
version-specific verification. Features and subscription access can vary by
version, plan, provider, and model. Configuring a model does not establish full
SDK feature compatibility; validate the tools and workflows you need.

CloudCLI (formerly Claude Code UI, repository `claudecodeui`) documents a
one-command self-hosted start, `npx @cloudcli-ai/cloudcli`, with Node.js 22+.
Its package declares both `@anthropic-ai/claude-agent-sdk` and
`@openai/codex-sdk`; dependencies alone do not prove which runtime every
execution path uses. Sources: [official repository](https://github.com/siteboon/claudecodeui)
and [package.json](https://github.com/siteboon/claudecodeui/blob/main/package.json).

## vs. general chat UIs

|  | muselab | CloudCLI / claudecodeui | LobeChat | AnythingLLM | Claude Code CLI |
|---|---|---|---|---|---|
| Primary purpose | Local workspaces + executable Agent | Web/mobile UI for coding agents | Multi-model chat + plugin store | Document chat; broader agent scope not verified | Terminal coding agent |
| Self-hosted | ✅ | ✅ | ✅ | ✅ | N/A (runs locally) |
| Browser access | ✅ | ✅ | ✅ | ✅ | ❌ |
| HTML / PDF / image preview | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ |
| Real PTY terminal in browser | ✅ multi-terminal + profiles | ✅ multi-tab | ❌ | ❌ | n/a (runs in your terminal) |
| Agent / SDK integration | Claude Agent SDK; verify capabilities per provider/model | Claude Agent SDK and Codex SDK dependencies; verify capabilities per provider/model | Not verified | Not verified | N/A (CLI) |
| Reuse Claude Pro subscription | ✅ | ✅ | ❌ | ❌ | ✅ |
| Setup entry point | curl \| bash | npx (one command; Node.js 22+) | docker compose | docker | brew / npm |

For **IDE breadth**, consider claudecodeui or code-server.
For a **plugin marketplace**, consider LobeChat.
For **chat over crawled documents**, consider AnythingLLM.

muselab terminals share the same working directory as files, previews, and
conversations. Multiple real PTY sessions can stay alive at once, while
profiles run a fixed command whenever a terminal is created. Switching pages
does not stop their processes.

Other names that often come up in the same search:

- [Open WebUI](https://github.com/open-webui/open-webui) — the go-to
  self-hosted chat UI for local models (Ollama) and OpenAI-compatible
  endpoints, with its own RAG and tool system. Choose it when local-model
  chat is the centerpiece; choose muselab when you want the Claude Code
  agent loop (Read / Grep / Edit / Bash, Skills, MCP) over your own files.
- [LibreChat](https://github.com/danny-avila/LibreChat) — multi-provider
  chat with multi-user auth and an agents framework. Choose it for a shared,
  team-facing chat portal; muselab is deliberately single-user
  (see [Scope boundaries](#scope-boundaries)).
- **Obsidian / Logseq AI plugins** — AI inside a note-taking app. They focus on
  a notes vault; muselab's Agent works on registered local workspaces (any file
  type) and can execute multi-step tasks with tools and terminals, not just
  write text.

## vs. other Claude harnesses

|  | muselab | Claude Code CLI | Claude Desktop | CloudCLI / claudecodeui | claude-code-router |
|---|---|---|---|---|---|
| Official **Claude Agent SDK** integration | Direct | N/A (CLI) | Internal SDK use not publicly verified | Declared dependency; also includes Codex SDK | Not verified |
| Web UI in browser | ✅ | ❌ TTY | ❌ desktop | ✅ | ❌ |
| Files + previews + real terminal | ✅ integrated | ⚠️ terminal-first | ⚠️ no real terminal | ✅ | ❌ |
| Non-Claude model compatibility | Via provider compatibility layer; validate required SDK capabilities per provider/model | Not verified | Not verified | Verify per provider/model; SDK dependencies do not establish feature parity | Verify per provider/model |
| Self-host friendly | ✅ | N/A (runs locally) | N/A (desktop app) | ✅ | ✅ |
| Open source | ✅ MIT | ❌ | ❌ | ✅ AGPL-3.0 | ✅ MIT |

muselab puts the Agent loop in a self-hosted local workspace that is accessible
from a browser.

Any authorized local directory can be a workspace. The installer collects no
personal profile and creates no predefined directory structure.

## Scope boundaries

- Single-user, single-token — two people sharing one instance share
  everything; use separate instances or a multi-user product for team sharing
- Not a full IDE — the built-in terminal is useful for commands and
  agent-assisted work in the active workspace, but muselab does not provide
  full code navigation, debugging, or an IDE extension ecosystem. Use
  claudecodeui or Claude Code for heavyweight software development
- Not a RAG service — files are read on demand via Read / Grep, never
  pre-embedded; for crawl-style document chat use
  [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm)
- No plugin marketplace — user-installed and external Claude Code plugin
  skills are auto-discovered, but muselab ships no task-specific presets and
  has no in-app store; use [LobeChat](https://github.com/lobehub/lobe-chat)
  if you need one
