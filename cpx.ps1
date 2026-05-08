param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = Join-Path $scriptRoot "src"

if (-not (Test-Path -LiteralPath $srcDir)) {
    throw "Cannot find src directory: $srcDir"
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            Exe = $python.Source
            PrefixArgs = @()
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            Exe = $py.Source
            PrefixArgs = @("-3")
        }
    }

    throw "Cannot find Python launcher. Install Python or add python.exe / py.exe to PATH."
}

$normalized = @($Args)
$hasClientFlag = $false
foreach ($arg in $normalized) {
    if ($arg -in @("-Codex", "-codex", "-Claude", "-claude", "--client")) {
        $hasClientFlag = $true
        break
    }
}

if (-not $hasClientFlag) {
    $normalized = @("-Codex") + $normalized
}

$pythonCmd = Get-PythonCommand

$oldPythonPath = $env:PYTHONPATH
if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
    $env:PYTHONPATH = $srcDir
} else {
    $env:PYTHONPATH = "$srcDir;$oldPythonPath"
}

try {
    & $pythonCmd.Exe @($pythonCmd.PrefixArgs) -m local_mcp_search.launcher @normalized
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $oldPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $oldPythonPath
    }
}
