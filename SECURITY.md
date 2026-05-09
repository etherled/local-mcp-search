# Security Policy

## Supported Scope

This repository is an early-stage local developer tool. Security reports are most relevant for issues that could:

- expose local files unintentionally
- leak tokens, credentials, or session data
- register unsafe MCP commands by default
- execute dangerous commands unexpectedly

## Reporting

Please do not open a public issue for sensitive security problems.

Instead, prepare:

- a short description of the issue
- affected version or commit
- reproduction steps
- impact assessment

If you do not have a private reporting channel configured yet, add one before broad public promotion of the repository.

## Hardening Expectations

Before public release, verify:

- no personal `.claude/`, `.mcp.json`, session dumps, or temp artifacts are committed
- no machine-specific absolute paths are used as public defaults
- launcher defaults fail safely when local models are not configured
- README examples do not encourage unsafe command execution patterns
