param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$ModelConfigPath = "C:\Users\yyyx\Documents\models-setting\my-embd-bge-zh.json",
    [string]$RerankerConfigPath = "C:\Users\yyyx\Documents\models-setting\qwen3-reranker_lingya.json",
    [string]$ServerName = "local-search",
    [ValidateSet("auto", "full", "incremental")]
    [string]$ReindexMode = "auto",
    [switch]$DisableReranker,
    [switch]$RegisterClaude,
    [switch]$WriteClaudeProjectConfig,
    [bool]$EnableAutoReindex = $true,
    [int]$AutoReindexIntervalSeconds = 5,
    [switch]$Launch,
    [switch]$LaunchCodex,
    [switch]$Codex,
    [switch]$Claude,
    [switch]$Fresh,
    [switch]$Pick,
    [switch]$Fork
)

$ErrorActionPreference = "Stop"

function Resolve-WorkspaceRoot {
    param(
        [string]$Path
    )

    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    $gitRoot = ""
    try {
        $gitRoot = (& git -C $resolvedPath rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -eq 0 -and $gitRoot) {
            return $gitRoot.Trim()
        }
    }
    catch {
    }

    return $resolvedPath
}

function Read-OpenAICompatConfig {
    param(
        [string]$Path,
        [string]$Name
    )

    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    $cfg = Get-Content -Raw -LiteralPath $resolvedPath | ConvertFrom-Json
    if (-not $cfg.base_url -or -not $cfg.model_name -or -not $cfg.api_key) {
        throw "$Name config must include base_url, model_name, and api_key."
    }

    return [pscustomobject]@{
        Path = $resolvedPath
        BaseUrl = $cfg.base_url
        ModelName = $cfg.model_name
        ApiKey = $cfg.api_key
    }
}

function Set-EmbeddingEnvironment {
    param(
        [string]$WorkspaceRoot,
        $EmbeddingConfig,
        $RerankerConfig,
        [bool]$DisableRerankerMode
    )

    $env:EMBEDDING_BASE_URL = $EmbeddingConfig.BaseUrl
    $env:EMBEDDING_MODEL = $EmbeddingConfig.ModelName
    $env:EMBEDDING_API_KEY = $EmbeddingConfig.ApiKey
    $env:MCP_SEARCH_WORKSPACE_ROOT = $WorkspaceRoot

    if (-not $DisableRerankerMode -and $null -ne $RerankerConfig) {
        $env:MCP_SEARCH_RERANKER_ENABLED = "true"
        $env:RERANKER_BASE_URL = $RerankerConfig.BaseUrl
        $env:RERANKER_MODEL = $RerankerConfig.ModelName
        $env:RERANKER_API_KEY = $RerankerConfig.ApiKey
    }
    else {
        $env:MCP_SEARCH_RERANKER_ENABLED = "false"
        Remove-Item Env:RERANKER_BASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:RERANKER_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:RERANKER_API_KEY -ErrorAction SilentlyContinue
    }
}

function Invoke-Reindex {
    param(
        [string]$WorkspaceRoot,
        [string]$Mode
    )

    Push-Location $WorkspaceRoot
    try {
        Write-Host "Reindexing project: $WorkspaceRoot (mode=$Mode)"
        & python -m local_mcp_search.cli reindex --mode $Mode
        if ($LASTEXITCODE -ne 0) {
            throw "Reindex failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Get-McpServerArgs {
    param(
        [string]$RunScriptPath,
        [string]$WorkspaceRoot,
        [string]$ModelConfigPath,
        [string]$ResolvedRerankerConfigPath,
        [bool]$DisableRerankerMode,
        [bool]$EnableAutoReindexMode,
        [int]$AutoReindexIntervalSecondsValue
    )

    $serverArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $RunScriptPath,
        "-WorkspaceRoot", $WorkspaceRoot,
        "-ModelConfigPath", $ModelConfigPath
    )

    if (-not $DisableRerankerMode -and $ResolvedRerankerConfigPath) {
        $serverArgs += @("-RerankerConfigPath", $ResolvedRerankerConfigPath)
    }
    else {
        $serverArgs += "-DisableReranker"
    }

    if ($EnableAutoReindexMode) {
        $serverArgs += @("-AutoReindex", "-AutoReindexIntervalSeconds", "$AutoReindexIntervalSecondsValue")
    }

    return $serverArgs
}

function Register-CodexMcp {
    param(
        [string]$ServerNameValue,
        [string[]]$ServerArgs,
        [string]$WorkspaceRoot
    )

    & codex mcp remove $ServerNameValue | Out-Null

    $codexAddArgs = @("mcp", "add", $ServerNameValue, "--", "powershell.exe") + $ServerArgs
    Write-Host "Updating Codex MCP server: $ServerNameValue"
    & codex @codexAddArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to add Codex MCP server configuration."
    }

    Write-Host "Current Codex MCP config:"
    & codex mcp get $ServerNameValue
}

function Register-ClaudeMcp {
    param(
        [string]$ServerNameValue,
        [string[]]$ServerArgs,
        [string]$WorkspaceRoot
    )

    Push-Location $WorkspaceRoot
    try {
        & claude mcp remove $ServerNameValue | Out-Null
        $claudeAddArgs = @("mcp", "add", $ServerNameValue, "powershell.exe", "--") + $ServerArgs
        Write-Host "Updating Claude Code MCP server: $ServerNameValue"
        & claude @claudeAddArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to add Claude Code MCP server configuration."
        }

        Write-Host "Current Claude Code MCP config:"
        & claude mcp get $ServerNameValue
    }
    finally {
        Pop-Location
    }
}

function Write-ClaudeProjectMcpConfig {
    param(
        [string]$WorkspaceRoot,
        [string]$ServerNameValue,
        [string[]]$ServerArgs
    )

    $mcpJson = @{
        mcpServers = @{
            $ServerNameValue = @{
                command = "powershell.exe"
                args = $ServerArgs
            }
        }
    } | ConvertTo-Json -Depth 10

    $mcpJsonPath = Join-Path $WorkspaceRoot ".mcp.json"
    Set-Content -LiteralPath $mcpJsonPath -Value $mcpJson -Encoding UTF8
    Write-Host "Wrote Claude Code project MCP config: $mcpJsonPath"
}

function Get-LatestCodexSessionForWorkspace {
    param(
        [string]$WorkspaceRoot
    )

    $sessionsRoot = Join-Path $HOME ".codex\sessions"
    if (-not (Test-Path -LiteralPath $sessionsRoot)) {
        return $null
    }

    $candidate = Get-ChildItem -LiteralPath $sessionsRoot -Recurse -File -Filter *.jsonl |
        Sort-Object LastWriteTime -Descending

    foreach ($file in $candidate) {
        $firstLine = Get-Content -LiteralPath $file.FullName -TotalCount 1 -ErrorAction SilentlyContinue
        if (-not $firstLine) {
            continue
        }

        try {
            $entry = $firstLine | ConvertFrom-Json
        }
        catch {
            continue
        }

        if ($entry.type -ne "session_meta") {
            continue
        }
        if ($entry.payload.cwd -ne $WorkspaceRoot) {
            continue
        }

        return [pscustomobject]@{
            Id = $entry.payload.id
            WorkspaceRoot = $entry.payload.cwd
            LastWriteTime = $file.LastWriteTime
            Path = $file.FullName
        }
    }

    return $null
}

function Get-ClaudeProjectSlug {
    param(
        [string]$WorkspaceRoot
    )

    $trimmed = $WorkspaceRoot.TrimEnd('\', '/')
    $slug = $trimmed -replace '[:\\/ ]', '-'
    $slug = $slug -replace '[^A-Za-z0-9._-]', '-'
    $slug = $slug -replace '-{2,}', '--'
    return $slug
}

function Get-LatestClaudeSessionForWorkspace {
    param(
        [string]$WorkspaceRoot
    )

    $historyPath = Join-Path $HOME ".claude\history.jsonl"
    if (-not (Test-Path -LiteralPath $historyPath)) {
        return $null
    }

    $latest = $null
    foreach ($line in Get-Content -LiteralPath $historyPath) {
        if (-not $line) {
            continue
        }

        try {
            $entry = $line | ConvertFrom-Json
        }
        catch {
            continue
        }

        if ($entry.project -ne $WorkspaceRoot) {
            continue
        }
        if (-not $entry.sessionId) {
            continue
        }

        if ($null -eq $latest -or [int64]$entry.timestamp -gt [int64]$latest.Timestamp) {
            $latest = [pscustomobject]@{
                Id = $entry.sessionId
                WorkspaceRoot = $entry.project
                Timestamp = [int64]$entry.timestamp
                Display = $entry.display
            }
        }
    }

    if ($null -eq $latest) {
        return $null
    }

    $projectSlug = Get-ClaudeProjectSlug -WorkspaceRoot $WorkspaceRoot
    $sessionPath = Join-Path (Join-Path $HOME ".claude\projects") "$projectSlug\$($latest.Id).jsonl"
    if (Test-Path -LiteralPath $sessionPath) {
        $latest | Add-Member -NotePropertyName Path -NotePropertyValue $sessionPath
    }

    return $latest
}

function Select-SessionInteractively {
    param(
        [object[]]$Sessions,
        [string]$ClientName
    )

    if (-not $Sessions -or $Sessions.Count -eq 0) {
        return $null
    }

    Write-Host ""
    Write-Host "$ClientName sessions:"
    for ($i = 0; $i -lt $Sessions.Count; $i++) {
        $session = $Sessions[$i]
        $timestamp = if ($session.PSObject.Properties["Timestamp"]) {
            [DateTimeOffset]::FromUnixTimeMilliseconds([int64]$session.Timestamp).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss")
        }
        elseif ($session.PSObject.Properties["LastWriteTime"]) {
            ([datetime]$session.LastWriteTime).ToString("yyyy-MM-dd HH:mm:ss")
        }
        else {
            ""
        }

        $suffix = if ($session.PSObject.Properties["Display"] -and $session.Display) {
            " | $($session.Display)"
        }
        else {
            ""
        }
        Write-Host ("[{0}] {1} | {2}{3}" -f ($i + 1), $session.Id, $timestamp, $suffix)
    }

    $raw = Read-Host "Select $ClientName session number"
    $selectedIndex = 0
    if (-not [int]::TryParse($raw, [ref]$selectedIndex)) {
        throw "Invalid selection: $raw"
    }
    if ($selectedIndex -lt 1 -or $selectedIndex -gt $Sessions.Count) {
        throw "Selection out of range: $selectedIndex"
    }

    return $Sessions[$selectedIndex - 1]
}

function Get-RecentCodexSessionsForWorkspace {
    param(
        [string]$WorkspaceRoot,
        [int]$MaxCount = 10
    )

    $sessionsRoot = Join-Path $HOME ".codex\sessions"
    if (-not (Test-Path -LiteralPath $sessionsRoot)) {
        return @()
    }

    $results = @()
    foreach ($file in Get-ChildItem -LiteralPath $sessionsRoot -Recurse -File -Filter *.jsonl | Sort-Object LastWriteTime -Descending) {
        $firstLine = Get-Content -LiteralPath $file.FullName -TotalCount 1 -ErrorAction SilentlyContinue
        if (-not $firstLine) {
            continue
        }

        try {
            $entry = $firstLine | ConvertFrom-Json
        }
        catch {
            continue
        }

        if ($entry.type -ne "session_meta" -or $entry.payload.cwd -ne $WorkspaceRoot) {
            continue
        }

        $results += [pscustomobject]@{
            Id = $entry.payload.id
            WorkspaceRoot = $entry.payload.cwd
            LastWriteTime = $file.LastWriteTime
            Path = $file.FullName
        }

        if ($results.Count -ge $MaxCount) {
            break
        }
    }

    return $results
}

function Get-RecentClaudeSessionsForWorkspace {
    param(
        [string]$WorkspaceRoot,
        [int]$MaxCount = 10
    )

    $historyPath = Join-Path $HOME ".claude\history.jsonl"
    if (-not (Test-Path -LiteralPath $historyPath)) {
        return @()
    }

    $latestById = @{}
    foreach ($line in Get-Content -LiteralPath $historyPath) {
        if (-not $line) {
            continue
        }

        try {
            $entry = $line | ConvertFrom-Json
        }
        catch {
            continue
        }

        if ($entry.project -ne $WorkspaceRoot -or -not $entry.sessionId) {
            continue
        }

        $existing = $latestById[$entry.sessionId]
        if ($null -eq $existing -or [int64]$entry.timestamp -gt [int64]$existing.Timestamp) {
            $latestById[$entry.sessionId] = [pscustomobject]@{
                Id = $entry.sessionId
                WorkspaceRoot = $entry.project
                Timestamp = [int64]$entry.timestamp
                Display = $entry.display
            }
        }
    }

    return $latestById.Values | Sort-Object Timestamp -Descending | Select-Object -First $MaxCount
}

function Start-CodexClient {
    param(
        [string]$WorkspaceRoot,
        [switch]$FreshMode,
        [switch]$PickMode,
        [switch]$ForkMode
    )

    $session = $null
    if (-not $FreshMode) {
        if ($PickMode) {
            $session = Select-SessionInteractively -Sessions (Get-RecentCodexSessionsForWorkspace -WorkspaceRoot $WorkspaceRoot) -ClientName "Codex"
        }
        else {
            $session = Get-LatestCodexSessionForWorkspace -WorkspaceRoot $WorkspaceRoot
        }
    }

    if ($session) {
        if ($ForkMode) {
            Write-Host "Launching Codex fork for session: $($session.Id)"
            & codex fork $session.Id
        }
        else {
            Write-Host "Launching Codex resume for session: $($session.Id)"
            & codex resume $session.Id
        }
        return
    }

    Write-Host "Launching fresh Codex session at: $WorkspaceRoot"
    & codex -C $WorkspaceRoot
}

function Start-ClaudeClient {
    param(
        [string]$WorkspaceRoot,
        [switch]$FreshMode,
        [switch]$PickMode,
        [switch]$ForkMode
    )

    $session = $null
    if (-not $FreshMode) {
        if ($PickMode) {
            $session = Select-SessionInteractively -Sessions (Get-RecentClaudeSessionsForWorkspace -WorkspaceRoot $WorkspaceRoot) -ClientName "Claude"
        }
        else {
            $session = Get-LatestClaudeSessionForWorkspace -WorkspaceRoot $WorkspaceRoot
        }
    }

    if ($session) {
        $resumeArgs = @("--resume", $session.Id)
        if ($ForkMode) {
            $resumeArgs += "--fork-session"
        }
        Write-Host "Launching Claude resume for session: $($session.Id)"
        & claude @resumeArgs
        return
    }

    Write-Host "Launching fresh Claude session at: $WorkspaceRoot"
    Push-Location $WorkspaceRoot
    try {
        & claude
    }
    finally {
        Pop-Location
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runScriptPath = Join-Path $scriptRoot "run-local-mcp-search.ps1"
if (-not (Test-Path -LiteralPath $runScriptPath)) {
    throw "Cannot find run-local-mcp-search.ps1 at: $runScriptPath"
}

$resolvedProjectRoot = Resolve-WorkspaceRoot -Path $ProjectRoot
$embeddingConfig = Read-OpenAICompatConfig -Path $ModelConfigPath -Name "Embedding"
$resolvedRerankerConfigPath = ""
$rerankerConfig = $null
if (-not $DisableReranker.IsPresent -and $RerankerConfigPath -and (Test-Path -LiteralPath $RerankerConfigPath)) {
    $rerankerConfig = Read-OpenAICompatConfig -Path $RerankerConfigPath -Name "Reranker"
    $resolvedRerankerConfigPath = $rerankerConfig.Path
}

Set-EmbeddingEnvironment -WorkspaceRoot $resolvedProjectRoot -EmbeddingConfig $embeddingConfig -RerankerConfig $rerankerConfig -DisableRerankerMode $DisableReranker.IsPresent
Invoke-Reindex -WorkspaceRoot $resolvedProjectRoot -Mode $ReindexMode

$serverArgs = Get-McpServerArgs `
    -RunScriptPath $runScriptPath `
    -WorkspaceRoot $resolvedProjectRoot `
    -ModelConfigPath $embeddingConfig.Path `
    -ResolvedRerankerConfigPath $resolvedRerankerConfigPath `
    -DisableRerankerMode $DisableReranker.IsPresent `
    -EnableAutoReindexMode $EnableAutoReindex `
    -AutoReindexIntervalSecondsValue $AutoReindexIntervalSeconds

Register-CodexMcp -ServerNameValue $ServerName -ServerArgs $serverArgs -WorkspaceRoot $resolvedProjectRoot

$shouldRegisterClaude = $RegisterClaude.IsPresent -or $Claude.IsPresent -or $Launch.IsPresent
if ($shouldRegisterClaude) {
    Register-ClaudeMcp -ServerNameValue $ServerName -ServerArgs $serverArgs -WorkspaceRoot $resolvedProjectRoot
}

if ($WriteClaudeProjectConfig.IsPresent) {
    Write-ClaudeProjectMcpConfig -WorkspaceRoot $resolvedProjectRoot -ServerNameValue $ServerName -ServerArgs $serverArgs
}

$launchCodexMode = $Launch.IsPresent -or $LaunchCodex.IsPresent -or $Codex.IsPresent -or (-not $Claude.IsPresent -and -not $Launch.IsPresent -and $false)
$launchClaudeMode = $Claude.IsPresent

if ($Launch.IsPresent -and -not $Codex.IsPresent -and -not $Claude.IsPresent) {
    $launchCodexMode = $true
}

if ($launchCodexMode) {
    Start-CodexClient -WorkspaceRoot $resolvedProjectRoot -FreshMode:$Fresh.IsPresent -PickMode:$Pick.IsPresent -ForkMode:$Fork.IsPresent
}
elseif ($launchClaudeMode) {
    Start-ClaudeClient -WorkspaceRoot $resolvedProjectRoot -FreshMode:$Fresh.IsPresent -PickMode:$Pick.IsPresent -ForkMode:$Fork.IsPresent
}
