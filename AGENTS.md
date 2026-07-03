## Language Requirements
- All code comments MUST be in English only
- All logging messages MUST be in English only
- All error messages MUST be in English only
- All docstrings MUST be in English only
- All variable names, function names, and class names MUST be in English only
- All git commit messages MUST be in English only
- Project documentation (README.md, files in docs/) MUST be in English only

## Code Guidelines
Follow the behavioral rules in @CODE_GUIDELINES.md

## Architecture Decision Records
Notable and debatable architecture/design decisions are logged in `adr/`
(see `adr/README.md` for the full process). In particular:
- Before proposing or re-proposing a debatable design change, check `adr/`
  for an existing decision on the topic. If one is `Accepted`, follow it
  instead of re-litigating it, unless you have genuinely new information.
- After making a debatable or architecturally significant decision, add a
  new ADR under `adr/` using `adr/template.md`.
- Never edit an old ADR's decision in place; supersede it with a new one
  instead, so the history of *why* is preserved.

## Versioning
The tool is installed straight from git, so `--version` is only truthful if
the version is actually maintained:
- Bump `version` in `pyproject.toml` (semver) in the same commit as any
  user-visible change: new command or flag, changed output schema, changed
  behavior or error contract.
- Run `uv lock` after bumping so `uv.lock` stays in sync (CI enforces this
  via `uv sync --locked`).
- Internal refactors, docs-only and test-only changes do not need a bump.

