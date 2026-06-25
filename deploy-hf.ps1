# Deploy the current committed state to the Hugging Face Space.
# HF rejects non-Xet binaries, so we push a single clean commit WITHOUT the two
# bundled .onnx passive-liveness models (optional / off by default). Run from the
# repo root, on your normal work branch, with everything committed.
#
#   .\deploy-hf.ps1
#
# Requires the 'space' git remote to point at your Space (with a write token).

$work = (git branch --show-current)
if (git branch --list hf-space) { git branch -D hf-space | Out-Null }
git checkout --orphan hf-space
# HF Spaces reject committed binaries. Drop them from the deploy commit: the optional
# face anti-spoof models, the whole android/ app (not needed by the server), and the
# palm .task binary — the Dockerfile bakes the palm models from HF at build instead.
git rm --cached --ignore-unmatch --quiet face/models/antispoof_bin_1.5_128.onnx face/models/antispoof_print_replay_1.5_128.onnx
git rm -r --cached --ignore-unmatch --quiet android
git rm --cached --ignore-unmatch --quiet palm/models/hand_landmarker.task
# Disposable deploy artifact (pushed only to the HF Space, not GitHub history),
# so don't require GPG signing — keeps redeploys frictionless.
git -c commit.gpgsign=false commit -q -m "Deploy to Hugging Face Space"
git push space hf-space:main --force
git checkout -f $work
Write-Host "`nDeployed. Open the Space -> App tab and watch the build logs." -ForegroundColor Green
