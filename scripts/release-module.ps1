<#
.SYNOPSIS
  Build the Foundry module zip and publish it as a GitHub release.
.EXAMPLE
  scripts\release-module.ps1 -Version 0.1.0
  scripts\release-module.ps1 -Version 0.1.0 -NoUpload   # build only, for checking
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [switch]$NoUpload
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem

$repo = 'MagnACodeOK/dnd-assistant'
$moduleDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'foundry-module'
$manifestPath = Join-Path $moduleDir 'module.json'
$distDir = Join-Path $moduleDir 'dist'
$zipPath = Join-Path $distDir 'module.zip'
$utf8 = New-Object System.Text.UTF8Encoding($false)  # no BOM: JSON parsers reject it

# Set version and download URL, keeping the rest of the manifest as written.
$json = [IO.File]::ReadAllText($manifestPath, $utf8)
$json = $json -replace '"version":\s*"[^"]*"', "`"version`": `"$Version`""
$json = $json -replace '"download":\s*"[^"]*"', "`"download`": `"https://github.com/$repo/releases/download/v$Version/module.zip`""
[IO.File]::WriteAllText($manifestPath, $json, $utf8)

# Zip with forward-slash entry names (Compress-Archive on PowerShell 5.1 writes
# backslashes, which break on a Linux Foundry server). Skip test/ and dist/.
New-Item -ItemType Directory -Force $distDir | Out-Null
if (Test-Path $zipPath) { Remove-Item $zipPath }
$zip = [IO.Compression.ZipFile]::Open($zipPath, 'Create')
try {
    Get-ChildItem $moduleDir -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($moduleDir.Length + 1).Replace('\', '/')
        if ($rel -notmatch '^(test|dist)/') {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $rel) | Out-Null
        }
    }
}
finally { $zip.Dispose() }
Write-Host "Built $zipPath (version $Version)"

if ($NoUpload) { return }
$branch = git -C $moduleDir branch --show-current
gh release create "v$Version" $zipPath $manifestPath --repo $repo --target $branch --title "Foundry module v$Version" --notes "Foundry module dnd-assistant v$Version."
if ($LASTEXITCODE -ne 0) { throw "gh release create failed" }
Write-Host "Manifest URL: https://github.com/$repo/releases/latest/download/module.json"
