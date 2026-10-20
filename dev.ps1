# Small launcher; the commands live in scripts/dev.py.
$ErrorActionPreference = 'Stop'
$forwardedArgs = @($args)
$projectDir = $PSScriptRoot
$versionCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'

function Invoke-Python([string]$Executable, [string[]]$PythonArgs) {
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.UseShellExecute = $false
    # Windows PowerShell 5.1 has no ArgumentList. Quote each argument ourselves
    # so empty strings, spaces, quotes, and trailing backslashes survive.
    $quoted = foreach ($value in $PythonArgs) {
        '"' + ($value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
    }
    $start.Arguments = $quoted -join ' '
    $process = [System.Diagnostics.Process]::Start($start)
    try {
        $process.WaitForExit()
        return $process.ExitCode
    } finally {
        $process.Dispose()
    }
}

$candidates = if ($env:SETUP_SSH_PYTHON) { @($env:SETUP_SSH_PYTHON) } else {
    @('py', 'python3', 'python')
}
foreach ($candidate in $candidates) {
    $command = Get-Command -Name $candidate -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $command) { continue }
    $prefix = if ($command.Name -in @('py', 'py.exe')) { @('-3') } else { @() }
    try {
        $code = Invoke-Python $command.Source ($prefix + @('-c', $versionCheck))
    } catch {
        continue
    }
    if ($code -eq 0) {
        try {
            $code = Invoke-Python $command.Source (
                $prefix + @((Join-Path $projectDir 'scripts/dev.py')) + $forwardedArgs
            )
            exit $code
        } catch {
            [Console]::Error.WriteLine("Couldn't start the development helper: $_")
            exit 1
        }
    }
}

if ($forwardedArgs.Count -eq 0 -or $forwardedArgs[0] -in @('help', '-h', '--help')) {
    Write-Output 'Usage: .\dev.ps1 <command> [args]'
    Write-Output 'Commands: help doctor install reinstall uninstall develop run check fmt test build'
    Write-Output 'Install Python 3.11+ and uv to use these commands. See docs/development.md.'
    exit 0
}
[Console]::Error.WriteLine(
    'Python 3.11+ is required. Install it or set SETUP_SSH_PYTHON to its executable.'
)
exit 1
