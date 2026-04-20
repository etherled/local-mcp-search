param(
    [string]$WorkspaceRoot = (Get-Location).Path,
    [string]$ModelConfigPath = "C:\Users\yyyx\Documents\models-setting\my-embd-bge-zh.json",
    [string]$IndexDir = "",
    [switch]$AutoReindex,
    [int]$AutoReindexIntervalSeconds = 5
)

$cfg = Get-Content -Raw $ModelConfigPath | ConvertFrom-Json

$env:EMBEDDING_BASE_URL = $cfg.base_url
$env:EMBEDDING_MODEL = $cfg.model_name
$env:EMBEDDING_API_KEY = $cfg.api_key
$env:MCP_SEARCH_WORKSPACE_ROOT = (Resolve-Path $WorkspaceRoot).Path

if ($IndexDir -ne "") {
    $env:MCP_SEARCH_INDEX_DIR = (Resolve-Path $IndexDir).Path
}

if ($AutoReindex.IsPresent) {
    $env:MCP_SEARCH_AUTO_REINDEX = "true"
    $env:MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS = "$AutoReindexIntervalSeconds"
}

python -m local_mcp_search
