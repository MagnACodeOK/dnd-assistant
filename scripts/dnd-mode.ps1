<#
.SYNOPSIS
  D&D mode: start LM Studio with the scribe model, then run the table assistant in this console.
.PARAMETER New
  Start a separate session folder for today instead of continuing today's.
#>
param([switch]$New)

$Host.UI.RawUI.WindowTitle = 'D&D Assistant'
$Lms = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'
$Model = 'qwen/qwen3.5-9b'
$Project = Split-Path $PSScriptRoot -Parent

Write-Host 'Starting LM Studio (AI mode)...'
& (Join-Path $env:USERPROFILE 'GamingMode\GamingMode.ps1') ai

# Whisper and the 9B scribe share the 4080 with Discord and the browser. Unload anything else
# first (for example a model Hermes loaded on demand) so they fit in video memory.
Write-Host "Loading $Model (16K context)..."
& $Lms unload --all | Out-Null
& $Lms load $Model --context-length 16384 --gpu max -y
if ($LASTEXITCODE -ne 0) { Write-Warning "lms load failed; the scribe will skip runs until $Model is loaded." }

# A console started before the variable was set doesn't see it, so read the user environment.
$key = [Environment]::GetEnvironmentVariable('LMSTUDIO_API_KEY', 'User')
if ($key) {
    $env:LMSTUDIO_API_KEY = $key
} else {
    Write-Warning "LMSTUDIO_API_KEY isn't set, so the scribe can't take notes. Fix: [Environment]::SetEnvironmentVariable('LMSTUDIO_API_KEY', '<LM Studio API token>', 'User')"
}

$runArgs = @('run', '--project', $Project, 'dnd-assistant', 'run')
if ($New) { $runArgs += '--new' }
& uv @runArgs
