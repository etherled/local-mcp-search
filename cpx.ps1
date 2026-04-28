param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendScript = Join-Path $scriptRoot "use-local-mcp-search.ps1"

if (-not (Test-Path -LiteralPath $backendScript)) {
    throw "Cannot find backend script: $backendScript"
}

$normalized = @($Args)
$hasClientFlag = $false
foreach ($arg in $normalized) {
    if ($arg -in @("-Codex", "-Claude", "-Launch", "-LaunchCodex")) {
        $hasClientFlag = $true
        break
    }
}

if (-not $hasClientFlag) {
    $normalized = @("-Codex") + $normalized
}

& $backendScript @normalized
exit $LASTEXITCODE
