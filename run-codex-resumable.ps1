function Get-LatestCodexSession {
    param(
        [string]$SessionsRoot,
        [string]$WorkspaceRoot
    )

    $sessionFile = Get-ChildItem -LiteralPath $SessionsRoot -Recurse -File -Filter *.jsonl |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 20

    foreach ($file in $sessionFile) {
        $firstLine = Get-Content -LiteralPath $file.FullName -TotalCount 1 -ErrorAction SilentlyContinue
        if (-not $firstLine) {
            continue
        }
        try {
            $entry = $firstLine | ConvertFrom-Json
        } catch {
            continue
        }
        if ($entry.type -ne "session_meta") {
            continue
        }
        if ($entry.payload.cwd -ne $WorkspaceRoot) {
            continue
        }
        return [pscustomobject]@{
            id = $entry.payload.id
            cwd = $entry.payload.cwd
            path = $file.FullName
            last_write_time = $file.LastWriteTimeUtc
        }
    }
    return $null
}

function Resolve-TargetSession {
    param(
        $BeforeSession,
        $AfterSession
    )

    if (-not $AfterSession) {
        return $null
    }
    if (-not $BeforeSession) {
        return $AfterSession
    }
    if ($BeforeSession.id -ne $AfterSession.id) {
        return $AfterSession
    }
    if ($AfterSession.last_write_time -gt $BeforeSession.last_write_time) {
        return $AfterSession
    }
    return $AfterSession
}

param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$ResumeScriptPath = "",
    [switch]$UseLastResume,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CodexArgs
)

$ErrorActionPreference = "Stop"

$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$codexHome = Join-Path $HOME ".codex"
$sessionsRoot = Join-Path $codexHome "sessions"

if (-not (Test-Path -LiteralPath $sessionsRoot)) {
    throw "Cannot find Codex sessions directory: $sessionsRoot"
}

$resumeScriptPath = if ($ResumeScriptPath -ne "") {
    $ResumeScriptPath
} else {
    Join-Path $resolvedProjectRoot "resume-codex-last.ps1"
}

$beforeNewest = Get-LatestCodexSession -SessionsRoot $sessionsRoot -WorkspaceRoot $resolvedProjectRoot

$launchArgs = @()
if ($resolvedProjectRoot -ne (Get-Location).Path) {
    $launchArgs += @("-C", $resolvedProjectRoot)
}
if ($CodexArgs) {
    $launchArgs += $CodexArgs
}

& codex @launchArgs
$codexExitCode = $LASTEXITCODE

$afterNewest = Get-LatestCodexSession -SessionsRoot $sessionsRoot -WorkspaceRoot $resolvedProjectRoot
$targetSession = Resolve-TargetSession -BeforeSession $beforeNewest -AfterSession $afterNewest

if (-not $targetSession) {
    Write-Warning "No Codex session was detected for workspace: $resolvedProjectRoot"
    exit $codexExitCode
}

$resumeCommand = if ($UseLastResume.IsPresent) {
    "codex resume --last"
} else {
    "codex resume $($targetSession.id)"
}

$scriptBody = @(
    '$ErrorActionPreference = "Stop"'
    '$projectRoot = "' + $resolvedProjectRoot.Replace('"', '""') + '"'
    'Set-Location -LiteralPath $projectRoot'
    $resumeCommand
) -join "`r`n"

Set-Content -LiteralPath $resumeScriptPath -Value $scriptBody -Encoding UTF8

Write-Host "Wrote resume script: $resumeScriptPath"
Write-Host "Session id: $($targetSession.id)"

exit $codexExitCode
