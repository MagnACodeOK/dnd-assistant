<#
.SYNOPSIS
  Create the "D&D Mode" desktop shortcut for dnd-mode.ps1.
  GetFolderPath('Desktop') follows OneDrive folder redirection, like the Gaming Mode shortcut.
#>
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$script = Join-Path $PSScriptRoot 'dnd-mode.ps1'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop 'D&D Mode.lnk'))
$link.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$link.Arguments = "-NoExit -ExecutionPolicy Bypass -File `"$script`""
$link.WorkingDirectory = Split-Path $PSScriptRoot -Parent
$link.Description = 'Start LM Studio and the D&D table assistant'
$link.Save()
Write-Host "Created $(Join-Path $desktop 'D&D Mode.lnk')"
