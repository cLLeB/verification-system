import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.faceverify.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.faceverify.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
        vectorDrawables { useSupportLibrary = true }
    }

    // Each flavor bundles a different ArcFace model (same asset filename, different
    // per-flavor source set) and produces a distinctly-named, side-by-side-installable
    // APK. fp32 = full precision (default, shipped forever). fp16 = half size, ~lossless.
    // (int8 is intentionally NOT a flavor yet — add one here once validated; see
    //  app/src/int8-experimental/README.)
    // Two dimensions:
    //  * connectivity: "offline" (no INTERNET permission — provably airgapped) vs
    //    "hybrid" (adds INTERNET + opt-in server sync; BuildConfig.HYBRID gates the code).
    //  * model: fp32 (full) vs fp16 (~lossless, half size).
    // => 4 side-by-side-installable variants: offline/hybrid × fp32/fp16.
    flavorDimensions += listOf("connectivity", "model")
    productFlavors {
        create("offline") {
            dimension = "connectivity"
            buildConfigField("boolean", "HYBRID", "false")
        }
        create("hybrid") {
            dimension = "connectivity"
            applicationIdSuffix = ".hybrid"
            versionNameSuffix = "-hybrid"
            buildConfigField("boolean", "HYBRID", "true")
        }
        create("fp32") {
            dimension = "model"
            applicationIdSuffix = ".fp32"
            versionNameSuffix = "-fp32"
            resValue("string", "app_name", "Face Verify f32")
        }
        create("fp16") {
            dimension = "model"
            applicationIdSuffix = ".fp16"
            versionNameSuffix = "-fp16"
            resValue("string", "app_name", "Face Verify f16")
        }
    }

    signingConfigs {
        create("release") {
            val ksProps = rootProject.file("keystore.properties")
            if (ksProps.exists()) {
                val p = Properties().apply { ksProps.inputStream().use { load(it) } }
                storeFile = rootProject.file(p.getProperty("storeFile"))
                storePassword = p.getProperty("storePassword")
                keyAlias = p.getProperty("keyAlias")
                keyPassword = p.getProperty("keyPassword")
            }
        }
    }
    buildTypes {
        release {
            // R8 off for a dependable first release (heavy reflection in ONNX/ML Kit/Room,
            // and the size is dominated by the model + native libs anyway). Can enable
            // later once keep-rules are validated on a device.
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true; buildConfig = true }
    // The ArcFace .onnx in assets is already compressed-ish; don't let AAPT recompress it.
    androidResources { noCompress += "onnx" }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.03")
    implementation(composeBom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.navigation:navigation-compose:2.8.1")

    // CameraX
    val camerax = "1.3.4"
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.camera:camera-view:$camerax")

    // On-device face detection (bundled model — no network, no download).
    implementation("com.google.mlkit:face-detection:16.1.7")

    // On-device ArcFace embedding inference.
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.19.2")

    // Encrypted local storage.
    val room = "2.6.1"
    implementation("androidx.room:room-runtime:$room")
    implementation("androidx.room:room-ktx:$room")
    ksp("androidx.room:room-compiler:$room")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.8.1")
}
