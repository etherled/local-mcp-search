param(
    [string]$WorkspaceRoot = (Get-Location).Path,
    [string]$ModelConfigPath = "C:\Users\yyyx\Documents\models-setting\my-embd-bge-zh.json",
    [string]$RerankerConfigPath = "C:\Users\yyyx\Documents\models-setting\qwen3-reranker_lingya.json",
    [string]$IndexDir = "",
    [switch]$DisableReranker,
    [switch]$AutoReindex,
    [int]$AutoReindexIntervalSeconds = 5
)

$cfg = Get-Content -Raw $ModelConfigPath | ConvertFrom-Json

$env:EMBEDDING_BASE_URL = $cfg.base_url
$env:EMBEDDING_MODEL = $cfg.model_name
$env:EMBEDDING_API_KEY = $cfg.api_key
$env:MCP_SEARCH_WORKSPACE_ROOT = (Resolve-Path $WorkspaceRoot).Path

if (-not $DisableReranker.IsPresent -and $RerankerConfigPath -ne "" -and (Test-Path -LiteralPath $RerankerConfigPath)) {
    $rerankerCfg = Get-Content -Raw -LiteralPath $RerankerConfigPath | ConvertFrom-Json
    $env:MCP_SEARCH_RERANKER_ENABLED = "true"
    $env:RERANKER_BASE_URL = $rerankerCfg.base_url
    $env:RERANKER_MODEL = $rerankerCfg.model_name
    $env:RERANKER_API_KEY = $rerankerCfg.api_key
}
else {
    $env:MCP_SEARCH_RERANKER_ENABLED = "false"
}

if ($IndexDir -ne "") {
    $env:MCP_SEARCH_INDEX_DIR = (Resolve-Path $IndexDir).Path
}

if ($AutoReindex.IsPresent) {
    $env:MCP_SEARCH_AUTO_REINDEX = "true"
    $env:MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS = "$AutoReindexIntervalSeconds"
}

$logPath = Join-Path $env:TEMP "local-mcp-search.log"
python -m local_mcp_search 2> $logPath
