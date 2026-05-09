# Support

## Before Asking For Help

Please check these first:

1. Read [README.md](/D:/trae_prj/mcp_sd/README.md).
2. Run the documented smoke test steps.
3. Run `doctor` or `python -m local_mcp_search.cli status`.
4. Confirm your local embedding and reranker services are actually reachable.

## When Opening An Issue

Include:

- Windows version
- Python version
- whether you are using `Codex` or `Claude Code`
- whether the problem is in `baseline`, `launcher`, `MCP registration`, `reindex`, or `search`
- the exact command you ran
- the exact error text

## Compatibility Notes

- This project is currently `Windows-first`.
- Not every OpenAI-compatible provider works with the current `Codex CLI` `Responses API` path.
- For `Codex`, provider compatibility should be verified before treating benchmark results as meaningful.
