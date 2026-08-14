# Build every shippable APK and collect them under android/ with names a person can
# read. Five variants, all installable side by side (each has its own applicationId):
#
#   Verify-offline-fp32.apk   on-device, airgapped (no INTERNET permission), full model
#   Verify-offline-fp16.apk   the same, half-size model
#   Verify-hybrid-fp32.apk    on-device matching + opt-in server sync
#   Verify-hybrid-fp16.apk    the same, half-size model
#   Verify-online.apk         server-side matching, no model bundled (small download)
#
# Finished APKs land in your Downloads folder, not in the repo: they are build
# output, they are large, and a signed binary sitting in a working tree is one
# careless `git add -A` away from being committed.
#
# Usage:
#   .\build-apks.ps1                       # all five
#   .\build-apks.ps1 -Only online          # just the online build
#   .\build-apks.ps1 -ServerUrl https://…  # bake a different default server in
#   .\build-apks.ps1 -OutDir D:\somewhere  # somewhere other than Downloads
#
# Requires JDK 17 and the signing config in keystore.properties.

param(
    [string]$Only = "",
    [string]$ServerUrl = "",
    [string]$OutDir = (Join-Path $env:USERPROFILE "Downloads")
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$OutDir = (Resolve-Path $OutDir).Path

# variant task suffix -> output apk name
$variants = [ordered]@{
    "OfflineFp32"   = "Verify-offline-fp32.apk"
    "OfflineFp16"   = "Verify-offline-fp16.apk"
    "HybridFp32"    = "Verify-hybrid-fp32.apk"
    "HybridFp16"    = "Verify-hybrid-fp16.apk"
    "OnlineNomodel" = "Verify-online.apk"
}

if ($Only) {
    $match = $variants.Keys | Where-Object { $_ -like "*$Only*" }
    if (-not $match) { throw "No variant matches '$Only'. Options: $($variants.Keys -join ', ')" }
    $selected = [ordered]@{}
    foreach ($k in $match) { $selected[$k] = $variants[$k] }
    $variants = $selected
}

$gradleArgs = @()
if ($ServerUrl) { $gradleArgs += "-PserverUrl=$ServerUrl" }

foreach ($variant in $variants.Keys) {
    $apkName = $variants[$variant]
    Write-Host "==> assembling $variant" -ForegroundColor Cyan
    & .\gradlew.bat ":app:assemble$($variant)Release" --console=plain @gradleArgs
    if ($LASTEXITCODE -ne 0) { throw "assemble$($variant)Release failed" }

    # AGP writes to app/build/outputs/apk/<flavor>/release/ with a generated name.
    $dir = Join-Path "app\build\outputs\apk" ("{0}{1}" -f $variant.Substring(0,1).ToLower(), $variant.Substring(1))
    $dir = Join-Path $dir "release"
    $built = Get-ChildItem -Path $dir -Filter *.apk -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $built) { throw "No APK found in $dir" }

    Copy-Item $built.FullName (Join-Path $OutDir $apkName) -Force
    $mb = [math]::Round($built.Length / 1MB, 1)
    Write-Host "    $apkName  ($mb MB)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. APKs in $OutDir" -ForegroundColor Green
Get-ChildItem -Path $OutDir -Filter "Verify-*.apk" |
    Sort-Object Length |
    Format-Table Name, @{ Name = "Size"; Expression = { "{0:N1} MB" -f ($_.Length / 1MB) } }
