# Contributing

## Scope

This project is currently:

- `alpha / Windows-first`
- focused on `Codex` and `Claude Code`
- optimized for local `llama-server` embedding and reranking workflows

Contributions are welcome, but changes should preserve the current priorities:

- keep the local-search core stable
- avoid breaking `cpx`
- prefer practical improvements over feature sprawl

## Before Opening A PR

Please do these first:

1. Read [README.md](/D:/trae_prj/mcp_sd/README.md) and confirm the change fits the current project direction.
2. If the change affects indexing, launcher behavior, or MCP registration, test on a real Windows workspace.
3. Keep changes focused. Avoid bundling unrelated refactors with a bug fix or feature.

## Development Notes

- Python: `>=3.10`
- Package entrypoints are defined in [pyproject.toml](/D:/trae_prj/mcp_sd/pyproject.toml)
- Main code lives under [src/local_mcp_search](/D:/trae_prj/mcp_sd/src/local_mcp_search)

Useful local checks:

```powershell
python -m local_mcp_search.cli status
python -m local_mcp_search.cli reindex --mode auto
python .\scripts\run_benchmark.py --task-ids repo-overview-entrypoints --clients claude --modes baseline
```

## Pull Request Guidance

- Explain the user-visible problem clearly.
- Describe the tradeoff if behavior changes.
- Include reproduction or validation steps.
- Do not commit local secrets, local session files, or machine-specific config.

## What Is Usually Most Helpful

- bug fixes around launcher stability
- Windows usability improvements
- MCP tool quality improvements
- diagnostics and failure clarity
- benchmark reproducibility improvements

## What To Avoid

- speculative abstractions with no concrete workflow benefit
- new search tools that overlap heavily with existing ones
- features that weaken current Windows-first stability
