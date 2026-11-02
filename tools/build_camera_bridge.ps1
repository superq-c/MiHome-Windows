# Builds the optional Windows camera runtime from pinned, reviewable sources.
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceRevision = 'b5948cfb25404cc5cb37b166ecaa2dca20b11d4b' # go2rtc v1.9.14
$Patch = Join-Path $PSScriptRoot 'camera_bridge\go2rtc-xiaomi-control.patch'
$ExpectedPatchHash = 'CCF99E77CA9E9A8C90C864FBFB2C41DB0CFF76EDDB51090689CE53E29BFAAEE3'
if ((Get-FileHash -LiteralPath $Patch -Algorithm SHA256).Hash -ne $ExpectedPatchHash) {
    throw 'Camera patch checksum mismatch.'
}
$RelayFiles = @{
    'relay.go' = 'DB2C5FFEBF8BDBDC7B7ACBA1898ED916D9885AF04A142BA20AC793F516A8EBB4'
    'relay_test.go' = '85C41CD2DFDBFCB92F6AE4A24057A0144CF750732E165037F284AA107BE3288A'
    'go2rtc-xiaomi-relay.patch' = 'AD0E2416C52B5ECDEA66CC6655A78D4614974330546B10792A7892C98F4168FC'
    'go2rtc-xiaomi-ptz.patch' = '362D5E7D1F9939821C32978F1C943B3858C878E992DC7E7A7AA217A3FC48FEA3'
    'ptz_test.go' = '2622564B5A9830EC5DB26248560E7A7E412A16C706F39148FAC222C16CEBE1E8'
}
foreach ($File in $RelayFiles.Keys) {
    if ((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot "camera_bridge/$File") -Algorithm SHA256).Hash -ne $RelayFiles[$File]) {
        throw "Camera relay source checksum mismatch: $File"
    }
}
Get-Command git, go -ErrorAction Stop | Out-Null
$SourceDir = Join-Path $ProjectRoot ('.tmp\camera-bridge-' + [guid]::NewGuid().ToString('N'))
$OutputDir = Join-Path $ProjectRoot 'runtime\camera'
New-Item -ItemType Directory -Force -Path $SourceDir, $OutputDir | Out-Null
git -C $SourceDir init --quiet
git -C $SourceDir remote add origin https://github.com/AlexxIT/go2rtc.git
git -C $SourceDir fetch --depth 1 origin $SourceRevision
if ($LASTEXITCODE -ne 0) { throw 'Cannot fetch pinned go2rtc source.' }
git -C $SourceDir checkout --detach --quiet FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot check out go2rtc source.' }
if ((git -C $SourceDir rev-parse HEAD) -ne $SourceRevision) { throw 'Source revision mismatch.' }
git -C $SourceDir apply --check $Patch
if ($LASTEXITCODE -ne 0) { throw 'Camera patch cannot be applied.' }
git -C $SourceDir apply $Patch
if ($LASTEXITCODE -ne 0) { throw 'Camera patch failed.' }
$RelayPatch = Join-Path $PSScriptRoot 'camera_bridge/go2rtc-xiaomi-relay.patch'
git -C $SourceDir apply $RelayPatch
if ($LASTEXITCODE -ne 0) { throw 'Camera relay integration patch failed.' }
$PtzPatch = Join-Path $PSScriptRoot 'camera_bridge/go2rtc-xiaomi-ptz.patch'
git -C $SourceDir apply $PtzPatch
if ($LASTEXITCODE -ne 0) { throw 'Camera PTZ response patch failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'camera_bridge/ptz_test.go') -Destination (Join-Path $SourceDir 'pkg/xiaomi/miss/ptz_test.go')
foreach ($File in @('relay.go', 'relay_test.go')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "camera_bridge/$File") -Destination (Join-Path $SourceDir "pkg/xiaomi/miss/cs2/$File")
}

$SavedEnv = @{}
foreach ($Name in @('GOARCH', 'GOOS', 'GOTOOLCHAIN', 'GOCACHE', 'GOMODCACHE')) {
    $SavedEnv[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}
try {
    $env:GOARCH = 'amd64'
    $env:GOOS = 'windows'
    $env:GOTOOLCHAIN = 'go1.24.0+auto'
    $env:GOCACHE = Join-Path $ProjectRoot '.tmp\go-cache'
    $env:GOMODCACHE = Join-Path $ProjectRoot '.tmp\go-mod'
    $Binary = Join-Path $OutputDir 'go2rtc.exe'
    & go -C $SourceDir test ./pkg/xiaomi/miss/cs2 ./pkg/xiaomi/miss
    if ($LASTEXITCODE -ne 0) { throw 'Camera relay protocol regression failed.' }
    & go -C $SourceDir build -trimpath -o $Binary .
    if ($LASTEXITCODE -ne 0) { throw 'Camera runtime build failed.' }
    Copy-Item -LiteralPath (Join-Path $SourceDir 'LICENSE') -Destination (Join-Path $OutputDir 'LICENSE.go2rtc')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'camera_bridge\LICENSE.control') -Destination (Join-Path $OutputDir 'LICENSE.control')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'camera_bridge\README.md') -Destination (Join-Path $OutputDir 'SOURCE.md')
    Write-Host "Camera runtime ready: $Binary"
} finally {
    foreach ($Name in $SavedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $SavedEnv[$Name], 'Process')
    }
}
