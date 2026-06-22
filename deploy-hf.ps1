# Deploy the current committed state to the Hugging Face Space.
# HF rejects non-Xet binaries, so we push a single clean commit WITHOUT the two
# bundled .onnx passive-liveness models (optional / off by default). Run from the
# repo root, on your normal work branch, with everything committed.
#
#   .\deploy-hf.ps1
#
# Requires the 'space' git remote to point at your Space (with a write token).
$ErrorActionPreference = "Stop"
$work = (git branch --show-current)
git branch -D hf-space 2>$null
git checkout --orphan hf-space
git rm --cached --ignore-unmatch --quiet face/models/antispoof_bin_1.5_128.onnx face/models/antispoof_print_replay_1.5_128.onnx
git commit -q -m "Deploy to Hugging Face Space"
git push space hf-space:main --force
git checkout -f $work
Write-Host "`nDeployed. Open the Space -> App tab and watch the build logs." -ForegroundColor Green
