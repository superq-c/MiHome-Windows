# SPDX-License-Identifier: GPL-3.0-or-later
# Uses fake executable/DLL bytes; never launches an application or touches real dist/.
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Fixture = '.tmp/package-test-' + [guid]::NewGuid().ToString('N')
$DistRelative = "$Fixture/dist"
$Dist = Join-Path $ProjectRoot $DistRelative
$Required = @('MiHome-Windows.exe', 'runtime/camera/go2rtc.exe', 'runtime/camera/LICENSE.go2rtc',
              'runtime/camera/LICENSE.control', 'runtime/camera/SOURCE.md',
              'PySide6/qt-plugins/multimedia/ffmpegmediaplugin.dll', 'dependency.dll')
foreach ($Name in $Required) {
    $Path = Join-Path $Dist $Name
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [IO.File]::WriteAllText($Path, 'test fixture only')
}
$Packager = Join-Path $ProjectRoot 'tools/package_release.ps1'
$OutputRelative = "$Fixture/output"
& $Packager -Version '0.5.0' -Commit ('a' * 40) -DistDirectory $DistRelative -OutputDirectory $OutputRelative
$OutputPath = Join-Path $ProjectRoot $OutputRelative
$Archive = Join-Path $OutputPath 'MiHome-Windows-0.5.0-windows-x64.zip'
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not (Get-Content (Join-Path $OutputPath 'SHA256SUMS.txt') -Raw).StartsWith($ArchiveHash)) {
    throw 'ZIP checksum does not match.'
}
$Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    foreach ($Name in @('MiHome-Windows.exe', 'dependency.dll', 'runtime/camera/go2rtc.exe', 'LICENSE.txt', 'BUILD-INFO.json')) {
        if (-not $Zip.GetEntry($Name)) { throw "ZIP is missing $Name" }
    }
} finally { $Zip.Dispose() }
$Info = Get-Content (Join-Path $OutputPath 'BUILD-INFO.json') -Raw | ConvertFrom-Json
if ($Info.commit -ne ('a' * 40) -or $Info.version -ne '0.5.0') { throw 'Build metadata mismatch.' }

function Expect-PackagingFailure([string]$Message) {
    $Failed = $false
    try {
        & $Packager -Version '0.5.0' -Commit ('a' * 40) -DistDirectory $DistRelative -OutputDirectory "$Fixture/rejected"
    } catch {
        if ($_.Exception.Message -notmatch $Message) { throw }
        $Failed = $true
    }
    if (-not $Failed) { throw 'Unsafe or incomplete package was accepted.' }
}
$FakeAuth = Join-Path $Dist 'auth.json'
[IO.File]::WriteAllText($FakeAuth, '{"test_fixture":true}')
Expect-PackagingFailure 'Personal configuration'
Remove-Item -LiteralPath $FakeAuth
Remove-Item -LiteralPath (Join-Path $Dist 'PySide6/qt-plugins/multimedia/ffmpegmediaplugin.dll')
Expect-PackagingFailure 'multimedia plugins'
Remove-Item -LiteralPath (Join-Path $Dist 'runtime/camera/go2rtc.exe')
Expect-PackagingFailure 'Missing packaged component'
Write-Output 'RELEASE PACKAGE REGRESSION ALL PASS'
