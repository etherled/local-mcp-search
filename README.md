# local-mcp-search

Chinese README: [README.zh-CN.md](/D:/trae_prj/mcp_sd/README.zh-CN.md)

`local-mcp-search` is a Windows-first local `STDIO MCP Server` for `Codex` and `Claude Code`.
It provides codebase and knowledge-base retrieval, local reranking, and context compression so the remote model can spend more of its budget on reasoning and editing instead of blind scanning.

Current public positioning:

- `alpha / 0.1.x`
- `Windows-first`
- built for `Codex` and `Claude Code`
- optimized for local `llama-server` deployment

## Highlights

- exact code search backed by local `ripgrep`
- semantic search backed by local embeddings
- local reranker served through `llama-server`
- local vector index powered by `LanceDB`
- `cpx` launcher for model startup, reindex, MCP registration, and session resume

Current MCP tools:

- `code_exact_search`
- `symbol_search`
- `code_semantic_search`
- `code_context_pack`
- `kb_search`
- `doc_answer_context`
- `file_outline`
- `symbol_context`
- `change_context`
- `dependency_overview`
- `repo_overview`
- `open_spans`
- `index_status`
- `reindex`

## Status

As of `2026-05-09`, the current repository state includes:

- embedding and reranking moved to local `llama-server` deployment, started or reused by `launcher` / `cpx`
- `cpx` with no arguments now starts `Codex` by default and resumes the latest workspace session
- `cpx -Claude` resumes the latest Claude session for the current workspace
- `doctor` can verify embedding, reranker, and whether MCP registration matches the current workspace
- `repo://overview`, `repo://dependency-summary`, and `repo://changes` are available as stable MCP resources
- `change_context` has been hardened for Windows MCP hosts to fail fast instead of hanging for long periods

The repo also includes an automated benchmark harness for the `Codex x Claude x baseline x local-search` matrix.
At the moment, the most reliable controlled benchmark conclusions come from `Claude + Xiaomi Mimo 2.5 Pro`, plus an earlier verified compatible `Codex` route.

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Local Models

The default setup expects local `llama-server` and launches two services through the Python launcher:

- embedding: `bge-base-zh`
- reranker: `bge-reranker-v2-m3`

The launcher reads model paths from environment variables first. If they are not configured, it uses:

- `llama-server` as the executable name
- empty GGUF paths, which causes startup to fail explicitly instead of silently falling back to a machine-specific path

Private local fallback config is supported and should not be committed:

- workspace-level: `<repo>/.local-search.env`
- user-level: `%USERPROFILE%/.local-mcp-search.env`

Resolution order:

- CLI arguments
- environment variables
- workspace private config
- user private config
- safe defaults

Recommended environment setup:

```powershell
$env:LOCAL_SEARCH_LLAMA_SERVER="D:\path\to\llama-server.exe"
$env:LOCAL_SEARCH_EMBED_GGUF="D:\models\bge-base-zh.f16.gguf"
$env:LOCAL_SEARCH_RERANK_GGUF="D:\models\bge-reranker-v2-m3-Q8_0.gguf"
```

Or put the same values in a private dotenv file:

```dotenv
LOCAL_SEARCH_LLAMA_SERVER=D:\path\to\llama-server.exe
LOCAL_SEARCH_EMBED_GGUF=D:\models\bge-base-zh.f16.gguf
LOCAL_SEARCH_RERANK_GGUF=D:\models\bge-reranker-v2-m3-Q8_0.gguf
```

Default ports:

```text
embedding port: 8887
reranker port: 8888
```

The launcher probes ports first:

- if a healthy service is already running, it is reused
- if the port is occupied but the endpoint is unhealthy, startup fails loudly
- if no service is running, local `llama-server` is started automatically

Logs are written to:

```text
%TEMP%\llama-logs\
```

## Quick Smoke Test

Recommended minimal validation flow:

1. Set model paths

```powershell
$env:LOCAL_SEARCH_LLAMA_SERVER="D:\path\to\llama-server.exe"
$env:LOCAL_SEARCH_EMBED_GGUF="D:\models\bge-base-zh.f16.gguf"
$env:LOCAL_SEARCH_RERANK_GGUF="D:\models\bge-reranker-v2-m3-Q8_0.gguf"
```

2. Reindex

```powershell
python -m local_mcp_search.cli reindex --mode auto
```

3. Check status

```powershell
python -m local_mcp_search.cli status
```

4. Launch and resume the latest Codex session

```powershell
cpx
```

5. Launch and resume the latest Claude session

```powershell
cpx -Claude
```

6. Verify MCP registration

```powershell
codex mcp get local-search --json
claude mcp get local-search
```

7. Run diagnostics

```powershell
python -m local_mcp_search.cli doctor
```

## Environment Variables

In most cases you do not need to export embedding or reranker variables manually; the launcher injects them.

Project-specific search and indexing config can live at the workspace root as `.local-search.json`.
See [.local-search.example.json](/D:/trae_prj/mcp_sd/.local-search.example.json:1).

If you want to run `python -m local_mcp_search` directly, you need at least:

```powershell
$env:MCP_SEARCH_WORKSPACE_ROOT="D:\your_repo"
$env:EMBEDDING_BASE_URL="http://127.0.0.1:8887/v1"
$env:EMBEDDING_MODEL="bge-base-zh"
$env:EMBEDDING_API_KEY=""
$env:MCP_SEARCH_RERANKER_ENABLED="true"
$env:RERANKER_BASE_URL="http://127.0.0.1:8888"
$env:RERANKER_MODEL="bge-reranker-v2-m3"
$env:RERANKER_API_KEY=""
```

Common optional variables:

```powershell
$env:MCP_SEARCH_INDEX_DIR="D:\your_repo\.mcp-index"
$env:MCP_SEARCH_MAX_FILE_BYTES="300000"
$env:MCP_SEARCH_AUTO_REINDEX="false"
$env:MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS="5"
$env:MCP_SEARCH_RERANKER_CANDIDATE_MULTIPLIER="6"
$env:MCP_SEARCH_RERANKER_MAX_CANDIDATES="80"
$env:MCP_SEARCH_RERANKER_CACHE_ENABLED="true"
$env:MCP_SEARCH_RERANKER_CACHE_MAX_ENTRIES="5000"
$env:MCP_SEARCH_CONTEXT_PACK_MAX_CHARS="20000"
$env:MCP_SEARCH_QUERY_DEBUG="false"
$env:EMBEDDING_TIMEOUT_SECONDS="10"
$env:RERANKER_TIMEOUT_SECONDS="30"
$env:MCP_SEARCH_CODE_CHUNK_LINES="120"
$env:MCP_SEARCH_KB_CHUNK_CHARS="1600"
```

Notes:

- the default workspace root is the current directory; if the current directory is inside a git repository, it is promoted to the git root
- the default index directory is `<workspace>\.mcp-index`
- project-level `.local-search.json` can override part of the indexing and retrieval behavior
- `reindex` uses batch embedding requests to avoid oversized single requests
- if you switch embedding models or dimensions, run `reindex full`
- with `MCP_SEARCH_QUERY_DEBUG=true`, `code_exact_search`, `code_semantic_search`, `kb_search`, and `code_context_pack` include an extra `debug` field in their JSON payload
- if model paths are missing, launcher startup fails explicitly instead of falling back to the author's machine-specific paths

Currently supported `.local-search.json` keys:

- `ignore_dirs`
- `doc_dirs`
- `max_file_bytes`
- `languages`

Example:

```json
{
  "ignore_dirs": [".openhands", "vendor", "tmp"],
  "doc_dirs": ["docs", "notes", "runbooks"],
  "max_file_bytes": 200000,
  "languages": ["python", "typescript", "javascript"]
}
```

## CLI

Reindex:

```powershell
python -m local_mcp_search.cli reindex --mode auto
```

Force a full rebuild:

```powershell
python -m local_mcp_search.cli reindex --mode full
```

Incremental only:

```powershell
python -m local_mcp_search.cli reindex --mode incremental
```

Status:

```powershell
python -m local_mcp_search.cli status
```

Build a compact context pack:

```powershell
python -m local_mcp_search.cli context-pack "authentication-related implementation" --max-results 6 --max-chars 12000
```

Enable query debug output:

```powershell
$env:MCP_SEARCH_QUERY_DEBUG="true"
python -m local_mcp_search.cli context-pack "authentication-related implementation" --max-results 6 --max-chars 12000
```

## Start The MCP Server Directly

If you already prepared the required environment variables:

```powershell
python -m local_mcp_search
```

The more common entrypoint is the launcher:

```powershell
python -m local_mcp_search.launcher
```

It will:

1. ensure local embedding and reranker services are available
2. inject workspace-specific environment variables
3. run `reindex`
4. refresh the `local-search` MCP registration
5. launch `Codex` by default and resume the latest session

## `cpx` Unified Entry Point

[cpx.ps1](/D:/trae_prj/mcp_sd/cpx.ps1:1) is the PowerShell wrapper. By default it is equivalent to:

```powershell
python -m local_mcp_search.launcher --client codex
```

Running:

```powershell
cpx
```

automatically:

1. resolves the target workspace
2. starts or reuses local llama services
3. refreshes the index
4. registers `local-search`
5. launches `Codex`
6. resumes the latest workspace session, or starts a new one if none exists

If you want `cpx` available globally in PowerShell, add a thin wrapper to your profile:

```powershell
function cpx {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )

    & "D:\trae_prj\mcp_sd\cpx.ps1" @Args
}
```

## Common `cpx` Examples

Start in the current directory and resume the latest Codex session:

```powershell
cpx
```

Explicitly start Codex:

```powershell
cpx -Codex
```

Explicitly start Claude Code:

```powershell
cpx -Claude
```

Specify a project root:

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Codex
```

Resume the latest Claude session:

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Claude
```

Ignore history and force a fresh session:

```powershell
cpx -Codex -Fresh
cpx -Claude -Fresh
```

Interactively pick a session:

```powershell
cpx -Codex -Pick
cpx -Claude -Pick
```

Fork the latest session:

```powershell
cpx -Codex -Fork
cpx -Claude -Fork
```

Force full reindex:

```powershell
cpx -Codex -ReindexMode full
```

Update MCP only and do not launch a client:

```powershell
python -m local_mcp_search.launcher --client none
```

Disable reranking:

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Claude -DisableReranker
```

Register Claude MCP at the same time:

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Codex -RegisterClaude
```

Write project-level Claude `.mcp.json`:

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -WriteClaudeProjectConfig
```

## MCP Registration

The launcher refreshes MCP configuration automatically.

Current Codex registration is equivalent to:

```powershell
codex mcp add local-search -- C:\Program Files\Python311\python.exe D:\your_repo\.mcp-index\_mcp_server_wrapper.py
```

Current Claude Code registration is equivalent to:

```powershell
claude mcp add local-search C:\Program Files\Python311\python.exe D:\your_repo\.mcp-index\_mcp_server_wrapper.py
```

The wrapper is written to:

```text
<workspace>\.mcp-index\_mcp_server_wrapper.py
```

It prepares:

- `PYTHONPATH/src`
- `MCP_SEARCH_WORKSPACE_ROOT`
- embedding and reranker endpoints

## Verify MCP

Codex:

```powershell
codex mcp list
codex mcp get local-search --json
```

Claude:

```powershell
claude mcp list
claude mcp get local-search
```

You can also run a health check directly:

```powershell
python -m local_mcp_search.cli status
```

Under normal conditions, `index_status` should show:

```text
reranker_enabled: true
reranker_model: bge-reranker-v2-m3
embedding_model: bge-base-zh
health.status: healthy
```

If `Codex` or `Claude Code` still reports MCP startup failures, or tools such as `change_context` / `repo://changes` misbehave:

- first confirm that `codex mcp get local-search --json` / `claude mcp get local-search` points to the current workspace `.mcp-index/_mcp_server_wrapper.py`
- if the path is already correct but the session still uses an old connection, restart the client session
- then run `python -m local_mcp_search.cli doctor` and inspect `codex_mcp_matches_workspace`, embedding, and reranker status

## How To Use It Inside Codex / Claude

Examples of direct prompts:

```text
Use local-search to inspect the project structure first.
```

```text
Call index_status and confirm reranker_enabled and reranker_model.
```

```text
Use code_exact_search to find a concrete function name, then open the key spans.
```

```text
Use code_context_pack to inspect authentication-related implementation, then continue from the returned spans.
```

```text
Use kb_search to find deployment notes, then open the most relevant spans.
```

If the client prefers MCP resources, these stable resources are also available:

- `repo://overview`
- `repo://dependency-summary`
- `repo://changes`

## Recommended Tool Order

- use `repo_overview` first for repository structure
- use `symbol_search` first for concrete symbols
- use `code_exact_search` first for concrete strings
- use `code_semantic_search` for similar logic or implementation patterns
- prefer `code_context_pack` for implementation or debugging work
- use `file_outline` before reading a file
- use `open_spans` for precise spans
- use `symbol_context` before editing a function or class
- use `change_context` for current worktree changes
- use `dependency_overview` for dependency and build configuration
- use `index_status` for index and backend health

`change_context` currently provides:

- change types such as added / modified / deleted / renamed / untracked
- grouping into `docs` / `code` / `config` / `tests` / `high_attention`
- risk levels based on file type and change scale
- git `numstat` summary

## Benchmark

The repository includes a minimal automated benchmark harness for:

- `Codex`
- `Claude`
- `baseline`
- `local-search`

The default task count is `4`, so a full run is `16 runs`. Entry script:

- [scripts/run_benchmark.py](/D:/trae_prj/mcp_sd/scripts/run_benchmark.py:1)

Run the full matrix:

```powershell
python .\scripts\run_benchmark.py
```

Default behavior:

- each case stores `summary.json`, `result.json`, and raw client output
- waits `12` seconds between cases by default to reduce non-interactive throttling
- retries obvious `429 / rate limit` failures with backoff, up to `2` retries by default
- `Codex` uses structured output by default; if your provider is only partially compatible, try `--codex-output-mode plain`

Single-task smoke test:

```powershell
python .\scripts\run_benchmark.py --task-ids repo-overview-entrypoints --clients codex --modes baseline
```

Fallback for weaker Codex structured-output compatibility:

```powershell
python .\scripts\run_benchmark.py --clients codex --codex-output-mode plain
```

Adjust pacing:

```powershell
python .\scripts\run_benchmark.py --pause-seconds 0 --max-retries 0
python .\scripts\run_benchmark.py --pause-seconds 20 --retry-backoff-seconds 45
```

More details:

- [benchmark/README.md](/D:/trae_prj/mcp_sd/benchmark/README.md:1)

Current benchmark notes:

- the repository already includes automated benchmark scripts, tasks, and result persistence
- two benchmark result groups are currently treated as valid: `Claude + Xiaomi Mimo 2.5 Pro`, and an earlier verified compatible `Codex` route
- some third-party or non-official routes may work for interactive chat but still fail specifically on `Codex exec` `Responses API` or structured-output paths; those routes should not be used for official `Codex` benchmark conclusions

Current controlled results:

- `Claude`
- run: `benchmark/results/20260509-204132-f33bdb48`
- sample: `4 tasks`, `baseline vs local-search`
- pass rate: `baseline 4/4`, `local-search 4/4`
- total duration: `baseline 66.653s`, `local-search 56.259s`
- total cost: `baseline 0.651079`, `local-search 0.443238`
- total turns: `baseline 27`, `local-search 18`
- token pattern: `baseline 366078`, `local-search 379791`
- conclusion: for `Claude`, the primary value is preserving success while reducing cost, latency, and turn count; token is more diagnostic than primary

- `Codex`
- run: `benchmark/results/20260509-170327-d1209b40`
- sample: `4 tasks`, `baseline vs local-search`
- pass rate: `baseline 4/4`, `local-search 4/4`
- total duration: `baseline 215.693s`, `local-search 207.573s`
- total token: `baseline 730540`, `local-search 570248`
- token reduction: about `21.94%`
- conclusion: for `Codex`, `local-search` shows a mild latency win and a clear token reduction signal

Current benchmark interpretation should stay agent-specific:

- for `Claude`, the primary framing is `success rate + cost + latency + turn count`
- for `Codex`, the primary framing is `success rate + latency + token`

## Known Limits

- currently `Windows-first`
- depends on local `llama-server` deployment and local model files
- third-party `Codex` provider compatibility is uneven
- if interactive `Codex` works but benchmark `schema` mode fails, the provider is often incomplete on `exec` / `Responses API` / structured output rather than the benchmark being wrong
- on some Windows MCP hosts, `change_context` may still prefer a fast timeout fallback; for stable resume / review flows, `repo://changes` may be the safer first step

## Good Fit

- medium or large repositories
- workflows that frequently resume prior context
- projects that need both code and knowledge retrieval
- daily `Codex` / `Claude Code` usage

## Not A Good Fit

- very small repositories
- workflows with little need for semantic retrieval
- users unwilling to maintain local models
- users expecting cross-platform zero-config setup

## Why Not Just `grep` / `Read`

- `code_exact_search` is more natural for agent workflows than manual `rg`
- `file_outline` lets the agent inspect structure before blind full-file reading
- `open_spans` keeps context narrow and precise
- `code_context_pack` combines search, span retrieval, and compression into one local step

## Notes

- the project has moved from older OpenAI-compatible embedding/reranker JSON config bootstrapping to local `llama-server` deployment plus Python launcher startup
- older `run-local-mcp-search.ps1` / `use-local-mcp-search.ps1` are no longer the primary entrypoints
- the recommended entrypoint is now `cpx.ps1` or `python -m local_mcp_search.launcher`
