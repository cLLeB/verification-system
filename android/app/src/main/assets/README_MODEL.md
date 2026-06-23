# Place the ArcFace model here

This app is **100% offline**, so the face-recognition model is bundled in the APK
(not downloaded at runtime). Put the InsightFace `buffalo_l` recognition model here:

    app/src/main/assets/w600k_r50.onnx     (~174 MB)

Easiest way (from the `android/` folder):

    .\copy-model.ps1

That copies it from your InsightFace cache
(`%USERPROFILE%\.insightface\models\buffalo_l\w600k_r50.onnx`). If you don't have it
yet, run the Python service once — InsightFace downloads it on first start.

The model is git-ignored (large binary). The build will fail to run face matching
until this file is present. Face *detection* uses ML Kit's bundled model and needs
nothing here.

Future size optimization: quantize to int8 (~45 MB) and swap the filename.
