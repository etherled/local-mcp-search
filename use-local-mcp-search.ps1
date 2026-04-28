param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$ModelConfigPath = "C:\Users\yyyx\Documents\models-setting\my-embd-bge-zh.json",
    [string]$RerankerConfigPath = "C:\Users\yyyx\Documents\models-setting\qwen3-reranker_lingya.json",
    [string]$ServerName = "local-search",
    [ValidateSet("auto", "full", "incremental")]
    [string]$ReindexMode = "auto",
    [switch]$LaunchCodex,
    [switch]$DisableReranker,
    [switch]$RegisterClaude,
    [switch]$WriteClaudeProjectConfig,
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
$resolvedRerankerConfigPath = ""
if ($RerankerConfigPath -ne "" -and (Test-Path -LiteralPath $RerankerConfigPath)) {
    $resolvedRerankerConfigPath = (Resolve-Path -LiteralPath $RerankerConfigPath).Path
}

$cfg = Get-Content -Raw -LiteralPath $resolvedModelConfigPath | ConvertFrom-Json
if (-not $cfg.base_url -or -not $cfg.model_name -or -not $cfg.api_key) {
    throw "Model config must include base_url, model_name, and api_key."
}

$env:EMBEDDING_BASE_URL = $cfg.base_url
$env:EMBEDDING_MODEL = $cfg.model_name
$env:EMBEDDING_API_KEY = $cfg.api_key
$env:MCP_SEARCH_WORKSPACE_ROOT = $resolvedProjectRoot
if (-not $DisableReranker.IsPresent -and $resolvedRerankerConfigPath -ne "") {
    $rerankerCfg = Get-Content -Raw -LiteralPath $resolvedRerankerConfigPath | ConvertFrom-Json
    if (-not $rerankerCfg.base_url -or -not $rerankerCfg.model_name -or -not $rerankerCfg.api_key) {
        throw "Reranker config must include base_url, model_name, and api_key."
    }
    $env:MCP_SEARCH_RERANKER_ENABLED = "true"
    $env:RERANKER_BASE_URL = $rerankerCfg.base_url
    $env:RERANKER_MODEL = $rerankerCfg.model_name
    $env:RERANKER_API_KEY = $rerankerCfg.api_key
}
else {
    $env:MCP_SEARCH_RERANKER_ENABLED = "false"
}

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

if (-not $DisableReranker.IsPresent -and $resolvedRerankerConfigPath -ne "") {
    $addArgs += @("-RerankerConfigPath", $resolvedRerankerConfigPath)
}
else {
    $addArgs += @("-DisableReranker")
}

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

$serverArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $runScriptPath,
    "-WorkspaceRoot", $resolvedProjectRoot,
    "-ModelConfigPath", $resolvedModelConfigPath
)
if (-not $DisableReranker.IsPresent -and $resolvedRerankerConfigPath -ne "") {
    $serverArgs += @("-RerankerConfigPath", $resolvedRerankerConfigPath)
}
else {
    $serverArgs += "-DisableReranker"
}
if ($EnableAutoReindex) {
    $serverArgs += @("-AutoReindex", "-AutoReindexIntervalSeconds", "$AutoReindexIntervalSeconds")
}

if ($RegisterClaude) {
    & claude mcp remove $ServerName | Out-Null
    $claudeAddArgs = @("mcp", "add", $ServerName, "powershell.exe", "--") + $serverArgs
    Write-Host "Updating Claude Code MCP server: $ServerName"
    & claude @claudeAddArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to add Claude Code MCP server configuration."
    }
    Write-Host "Current Claude Code MCP config:"
    & claude mcp get $ServerName
}

if ($WriteClaudeProjectConfig) {
    $mcpJson = @{
        mcpServers = @{
            $ServerName = @{
                command = "powershell.exe"
                args = $serverArgs
            }
        }
    } | ConvertTo-Json -Depth 10
    $mcpJsonPath = Join-Path $resolvedProjectRoot ".mcp.json"
    Set-Content -LiteralPath $mcpJsonPath -Value $mcpJson -Encoding UTF8
    Write-Host "Wrote Claude Code project MCP config: $mcpJsonPath"
}

if ($LaunchCodex) {
    Write-Host "Launching Codex at: $resolvedProjectRoot"
    & codex -C $resolvedProjectRoot
}
