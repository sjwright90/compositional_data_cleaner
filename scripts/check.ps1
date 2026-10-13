$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$steps = @(
    @('ruff', 'check', '.'),
    @('ruff', 'format', '--check', '.'),
    @('mypy', 'core', 'app', 'tests'),
    @('pytest', '-q')
)

foreach ($step in $steps) {
    Write-Host ">> python -m $($step -join ' ')"
    & python -m @step
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $($step -join ' ')"
        exit $LASTEXITCODE
    }
}
Write-Host 'All checks passed.'
