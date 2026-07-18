# 0004. Ship the agent skill in the repo, installed by the install prompt

Status: Accepted

## Context

The CLI is designed to be used by AI agents, but installing it only puts a
binary on PATH: nothing tells an agent in a *future* session that the tool
exists, when to reach for it, or how to map "read the work chat" to a
numeric chat ID. That knowledge has to live in something the agent loads
every session — an agent skill.

Until now the skill existed only as hand-maintained copies on the author's
machine (`~/.claude/skills/tg-reader/`, `~/.codex/skills/tg-reader/`), so
other users installing from the README got a tool their agents never
discover on their own.

Alternatives considered:

- **Claude Code plugin + marketplace** — proper install/update channel, but
  covers only Claude Code (Codex is left out) and adds a second distribution
  mechanism to maintain next to the README install prompt.
- **Skill text inlined in the README, agent recreates it** — no artifact to
  ship, but every user gets a slightly different generated skill, and there
  is no way to push a fix.
- **A note in per-project AGENTS.md** — project-scoped, while the tool is
  per-user and cross-project.

A related shaping decision: the skill deliberately does **not** restate the
CLI contract (flags, JSON schema, exit codes) — it defers to `--help`, which
is written to be agent-sufficient. The skill carries only what the CLI
cannot document about itself: the alias registry (`aliases.json` location,
matching rules, never guess an ID) and presentation behavior. This keeps the
installed copies from going stale, because that orchestration knowledge
changes far more rarely than the CLI surface.

## Decision

We will keep the canonical skill in the repo at `skills/tg-reader/` (one
agent-agnostic `SKILL.md` plus Codex-only `agents/openai.yaml`), and the
README install prompt will instruct the installing agent to copy that folder
into the per-user skill directory of whichever agent is running (Claude
Code: `~/.claude/skills/`, Codex: `~/.codex/skills/`).

## Consequences

- One source of truth, versioned together with the CLI; both supported
  agents are covered with zero extra infrastructure, and the step fits the
  existing "give this prompt to your agent" install flow.
- Installed skill copies do not auto-update on `uv tool upgrade`; accepted
  because the skill defers the volatile parts (CLI contract) to `--help`.
- We give up the plugin marketplace's update mechanism and discoverability.
