param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$ModelConfigPath = "C:\Users\yyyx\Documents\models-setting\my-embd-bge-zh.json",
    [string]$ServerName = "local-search",
    [ValidateSet("auto", "full", "incremental")]
    [string]$ReindexMode = "auto",
    [switch]$LaunchCodex,
    [bool]$EnableAutoReindex = $true,
    [int]$AutoReindexIntervalSeconds = 5
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runScriptPath = Join-Path $scriptRoot "run-local-mcp-search.ps1"

if (-not (Test-Path -LiteralPath $runScriptPath)) {
    throw "Cannot find run-local-mcp-search.ps1 at: $runScriptPath"
}

$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$resolvedModelConfigPath = (Resolve-Path -LiteralPath $ModelConfigPath).Path

$cfg = Get-Content -Raw -LiteralPath $resolvedModelConfigPath | ConvertFrom-Json
if (-not $cfg.base_url -or -not $cfg.model_name -or -not $cfg.api_key) {
    throw "Model config must include base_url, model_name, and api_key."
}

$env:EMBEDDING_BASE_URL = $cfg.base_url
$env:EMBEDDING_MODEL = $cfg.model_name
$env:EMBEDDING_API_KEY = $cfg.api_key
$env:MCP_SEARCH_WORKSPACE_ROOT = $resolvedProjectRoot

Push-Location $resolvedProjectRoot
try {
    Write-Host "Reindexing project: $resolvedProjectRoot (mode=$ReindexMode)"
    python -m local_mcp_search.cli reindex --mode $ReindexMode
    if ($LASTEXITCODE -ne 0) {
        throw "Reindex failed."
    }
}
finally {
    Pop-Location
}

# Recreate MCP server config with current project root.
& codex mcp remove $ServerName | Out-Null

$addArgs = @(
    "mcp", "add", $ServerName, "--",
    "powershell", "-File", $runScriptPath,
    "-WorkspaceRoot", $resolvedProjectRoot,
    "-ModelConfigPath", $resolvedModelConfigPath
)

if ($EnableAutoReindex) {
    $addArgs += @(
        "-AutoReindex",
        "-AutoReindexIntervalSeconds", "$AutoReindexIntervalSeconds"
    )
}

Write-Host "Updating Codex MCP server: $ServerName"
& codex @addArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to add MCP server configuration."
}

Write-Host "Current MCP config:"
& codex mcp get $ServerName

if ($LaunchCodex) {
    Write-Host "Launching Codex at: $resolvedProjectRoot"
    & codex -C $resolvedProjectRoot
}
