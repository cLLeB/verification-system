# Prepares the per-flavor ArcFace models so each APK bundles its own.
# fp32 = full precision (default, shipped forever). fp16 = half size, ~lossless.
# Models are gitignored (large); this regenerates them from your InsightFace cache.
#
#   .\copy-model.ps1
#
# Output:
#   app/src/fp32/assets/w600k_r50.onnx   (~174 MB)
#   app/src/fp16/assets/w600k_r50.onnx   (~87 MB, generated)

$src = Join-Path $env:USERPROFILE ".insightface\models\buffalo_l\w600k_r50.onnx"
if (-not (Test-Path $src)) {
    Write-Host "Model not found at $src. Run the Python service once (it downloads buffalo_l), then retry." -ForegroundColor Yellow
    exit 1
}
New-Item -ItemType Directory -Force -Path app\src\fp32\assets, app\src\fp16\assets | Out-Null

Copy-Item $src app\src\fp32\assets\w600k_r50.onnx -Force
Write-Host ("fp32: {0:N1} MB" -f ((Get-Item app\src\fp32\assets\w600k_r50.onnx).Length/1MB)) -ForegroundColor Green

Write-Host "Generating fp16 (keep_io_types=True; needs the project venv with onnxconverter-common)..."
& ..\venv\Scripts\python.exe -c "import onnx; from onnxconverter_common import float16; m=onnx.load(r'app\src\fp32\assets\w600k_r50.onnx'); onnx.save(float16.convert_float_to_float16(m, keep_io_types=True), r'app\src\fp16\assets\w600k_r50.onnx')" 2>$null
Write-Host ("fp16: {0:N1} MB" -f ((Get-Item app\src\fp16\assets\w600k_r50.onnx).Length/1MB)) -ForegroundColor Green

# int8 is intentionally deferred until validated on a multi-identity set —
# see experimental-int8/README.md.
