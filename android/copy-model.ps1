# Copies the ArcFace recognition model into the Android app's assets so it ships
# inside the APK (the app is 100% offline — the model must be bundled).
# The model is downloaded by InsightFace the first time the Python service runs;
# it lives in your user profile's .insightface cache.

$src = Join-Path $env:USERPROFILE ".insightface\models\buffalo_l\w600k_r50.onnx"
$dstDir = Join-Path $PSScriptRoot "app\src\main\assets"
$dst = Join-Path $dstDir "w600k_r50.onnx"

New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
if (Test-Path $src) {
    Copy-Item $src $dst -Force
    $mb = [math]::Round((Get-Item $dst).Length / 1MB, 1)
    Write-Host "Copied ArcFace model ($mb MB) -> app/src/main/assets/w600k_r50.onnx" -ForegroundColor Green
} else {
    Write-Host "Model not found at: $src" -ForegroundColor Yellow
    Write-Host "Run the Python service once (it downloads buffalo_l), then re-run this script." -ForegroundColor Yellow
}
