# SPDX-License-Identifier: GPL-3.0-or-later
<#
.SYNOPSIS
    Validate the full standalone directory and create a Windows release ZIP.
#>
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit,
    [string]$DistDirectory = 'dist',
    [string]$OutputDirectory = '.tmp/release'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DistPath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $DistDirectory))
$OutputPath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputDirectory))
if ($OutputPath -eq $DistPath -or $OutputPath.StartsWith($DistPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Release output must be outside the standalone directory.'
}
$Required = @('MiHome-Windows.exe', 'runtime/camera/go2rtc.exe',
              'runtime/camera/LICENSE.go2rtc', 'runtime/camera/LICENSE.control', 'runtime/camera/SOURCE.md')
foreach ($Relative in $Required) {
    if (-not (Test-Path -LiteralPath (Join-Path $DistPath $Relative) -PathType Leaf)) {
        throw "Missing packaged component: $Relative"
    }
}
$Files = @(Get-ChildItem -LiteralPath $DistPath -File -Recurse)
$PrivateNames = @('auth.json', 'devices_cache.json', 'settings.json', 'tray.json', 'workbench.json',
                  'board.json', 'schedules.json', 'device_icons.json', 'device_names.json', 'product_names.json')
if ($Files | Where-Object { $_.Name -in $PrivateNames -or $_.Extension -eq '.log' -or $_.Name -eq 'session.yaml' }) {
    throw 'Personal configuration, credentials or logs found in the standalone directory.'
}
if (-not ($Files | Where-Object { $_.Extension -eq '.dll' -and $_.FullName -match '[\\/]multimedia[\\/]' })) {
    throw 'Qt multimedia plugins are missing from the standalone directory.'
}
New-Item -ItemType Directory -Force -Path $OutputPath | Out-Null
$ArchiveName = "MiHome-Windows-$Version-windows-x64.zip"
$Archive = Join-Path $OutputPath $ArchiveName
if (Test-Path -LiteralPath $Archive) { throw 'Release archive already exists; use a fresh output directory.' }
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'LICENSE') -Destination (Join-Path $DistPath 'LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination (Join-Path $DistPath 'README.md')
$Info = [ordered]@{
    version = $Version
    commit = $Commit
    platform = 'windows-x64'
    built_at_utc = [DateTime]::UtcNow.ToString('o')
    application_sha256 = (Get-FileHash -LiteralPath (Join-Path $DistPath 'MiHome-Windows.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
    camera_sha256 = (Get-FileHash -LiteralPath (Join-Path $DistPath 'runtime/camera/go2rtc.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
}
$Info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DistPath 'BUILD-INFO.json') -Encoding utf8
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($DistPath, $Archive, [IO.Compression.CompressionLevel]::Optimal, $false)
$Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $ArchiveName" | Set-Content -LiteralPath (Join-Path $OutputPath 'SHA256SUMS.txt') -Encoding ascii
Copy-Item -LiteralPath (Join-Path $DistPath 'BUILD-INFO.json') -Destination (Join-Path $OutputPath 'BUILD-INFO.json')
Write-Output "Release package ready: $Archive"
