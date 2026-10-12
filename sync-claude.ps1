# Copy changed files from claude\ into .claude\, overwriting what is there.
# Files that exist only in .claude\ are left alone.
#
# Usage: .\sync-claude.ps1 [-DryRun]

param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$src = Join-Path $PSScriptRoot 'claude'
$dst = Join-Path $PSScriptRoot '.claude'

if (-not (Test-Path $src -PathType Container)) {
    Write-Error "Source folder not found: $src"
    exit 1
}

$srcFull = (Resolve-Path $src).Path.TrimEnd('\')
$copied = 0

Get-ChildItem -Path $srcFull -Recurse -File -Force | ForEach-Object {
    $rel = $_.FullName.Substring($srcFull.Length + 1)
    $target = Join-Path $dst $rel

    if (Test-Path $target -PathType Leaf) {
        $same = (Get-FileHash $_.FullName).Hash -eq (Get-FileHash $target).Hash
        if ($same) { return }
        $status = 'updated'
    } else {
        $status = 'added  '
    }

    Write-Host "$status $rel"

    if (-not $DryRun) {
        New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
        Copy-Item -Path $_.FullName -Destination $target -Force
    }
    $copied++
}

if ($DryRun) {
    Write-Host "Dry run: $copied file(s) would be copied."
} else {
    Write-Host "$copied file(s) copied to .claude\."
}
