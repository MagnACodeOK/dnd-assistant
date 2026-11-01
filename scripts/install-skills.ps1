<#
.SYNOPSIS
  Copy the /dnd and /dnd-recap Claude Code skills to %USERPROFILE%\.claude\skills\, overwriting.
#>
$ErrorActionPreference = 'Stop'
$source = Join-Path (Split-Path $PSScriptRoot -Parent) 'skills'
$target = Join-Path $env:USERPROFILE '.claude\skills'
New-Item -ItemType Directory -Force $target | Out-Null
Get-ChildItem $source -Directory | ForEach-Object {
    $dest = Join-Path $target $_.Name
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $_.FullName '*') $dest -Recurse -Force
    Write-Host "Installed $($_.Name) -> $dest"
}
