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
        versionCode = 2
        versionName = "1.1"
        vectorDrawables { useSupportLibrary = true }

        // Ship only the ABIs real phones use. x86/x86_64 exist for emulators and were
        // costing ~85 MB of native libraries in every APK - ONNX Runtime, MediaPipe and
        // ML Kit each carry a full copy per ABI. armeabi-v7a stays: minSdk is 26, and
        // budget 32-bit handsets on Android 8-10 are exactly the devices this has to
        // run on. Build an emulator APK with -PallAbis if you need one.
        if (project.findProperty("allAbis") == null) {
            ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
        }

        // The server the online build points at before anyone opens Settings (the
        // hybrid build uses it as the prefilled sync URL). Overridable per-build with
        // -PserverUrl=https://... so a rehosted deployment needs no code change.
        buildConfigField(
            "String", "DEFAULT_SERVER_URL",
            "\"${project.findProperty("serverUrl")
                ?: "https://verify.livelycliff-ba1a81a4.switzerlandnorth.azurecontainerapps.io"}\"",
        )
    }

    // Each flavor bundles a different ArcFace model (same asset filename, different
    // per-flavor source set) and produces a distinctly-named, side-by-side-installable
    // APK. fp32 = full precision (default, shipped forever). fp16 = half size, ~lossless.
    // (int8 is intentionally NOT a flavor yet - add one here once validated; see
    //  app/src/int8-experimental/README.)
    // Two dimensions:
    //  * connectivity: how (and whether) the device talks to a server.
    //      offline - no INTERNET permission at all, provably airgapped, matches on-device.
    //      hybrid  - adds INTERNET + opt-in server sync, but still matches ON-DEVICE.
    //      online  - matches ON THE SERVER. Bundles no recognition model, so the APK is
    //                a fraction of the size; needs a reachable server to do anything.
    //  * model: which recognition model is bundled - fp32 (full), fp16 (~lossless, half
    //    size), or nomodel (none at all; the ONLY valid pairing for "online").
    // => 5 side-by-side-installable variants: offline/hybrid × fp32/fp16, plus online.
    flavorDimensions += listOf("connectivity", "model")
    productFlavors {
        create("offline") {
            dimension = "connectivity"
            buildConfigField("boolean", "HYBRID", "false")
            buildConfigField("boolean", "ONLINE", "false")
        }
        create("hybrid") {
            dimension = "connectivity"
            applicationIdSuffix = ".hybrid"
            versionNameSuffix = "-hybrid"
            buildConfigField("boolean", "HYBRID", "true")
            buildConfigField("boolean", "ONLINE", "false")
        }
        create("online") {
            dimension = "connectivity"
            applicationIdSuffix = ".online"
            versionNameSuffix = "-online"
            buildConfigField("boolean", "HYBRID", "false")
            buildConfigField("boolean", "ONLINE", "true")
        }
        create("fp32") {
            dimension = "model"
            applicationIdSuffix = ".fp32"
            versionNameSuffix = "-fp32"
        }
        create("fp16") {
            dimension = "model"
            applicationIdSuffix = ".fp16"
            versionNameSuffix = "-fp16"
        }
        // No bundled model. Exists purely so "online" has a model flavor to pair with
        // that contributes no assets source set - that is what keeps the APK small.
        create("nomodel") {
            dimension = "model"
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
    // The .onnx (ArcFace + CCNet) and .task (MediaPipe Hands) assets must stay
    // uncompressed so they can be mmap'd at runtime; don't let AAPT recompress them.
    androidResources { noCompress += listOf("onnx", "task") }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

/** Which flavor combinations are real, and what each one is called on the home screen.
 *
 *  The two dimensions are not independent: "online" matches on the server so it must
 *  bundle NO model, and offline/hybrid match on-device so they must bundle one. The
 *  filter below keeps the 5 combinations that mean something and drops the 4 that do
 *  not (online+fp32, online+fp16, offline+nomodel, hybrid+nomodel).
 *
 *  app_name is set per-variant rather than per-flavor because it depends on BOTH
 *  dimensions. All five install side by side, so each needs a label a person can tell
 *  apart on the home screen - "Verify f32" and "Verify Hybrid f32" are different apps. */
androidComponents {
    fun flavorOf(pairs: List<Pair<String, String>>, dimension: String): String =
        pairs.first { it.first == dimension }.second

    beforeVariants { variant ->
        val connectivity = flavorOf(variant.productFlavors, "connectivity")
        val model = flavorOf(variant.productFlavors, "model")
        // "online" pairs with "nomodel" and nothing else; every other connectivity
        // pairs with a real model and never with "nomodel".
        variant.enable = (connectivity == "online") == (model == "nomodel")
    }

    onVariants { variant ->
        val connectivity = flavorOf(variant.productFlavors, "connectivity")
        val model = flavorOf(variant.productFlavors, "model")
        val label = when (connectivity) {
            "online" -> "Verify Online"
            "hybrid" -> "Verify Hybrid ${model.removePrefix("fp")}"
            else -> "Verify ${model.removePrefix("fp")}"
        }
        variant.resValues.put(
            variant.makeResValueKey("string", "app_name"),
            com.android.build.api.variant.ResValue(label, null),
        )
    }
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

    // On-device face detection (bundled model - no network, no download).
    implementation("com.google.mlkit:face-detection:16.1.7")

    // On-device palm (hand) landmark detection for the palm modality's ROI.
    implementation("com.google.mediapipe:tasks-vision:0.10.14")

    // On-device ArcFace (face) + CCNet (palm) embedding inference.
    //
    // Scoped per connectivity flavor rather than bundled everywhere: the ONLINE build
    // matches on the server, never constructs Embedder or PalmEmbedder, and so never
    // loads an ONNX class - but the runtime's native libraries are ~30 MB per APK
    // across the shipped ABIs. compileOnly keeps the classes on the COMPILE path (the
    // shared source set still references them) while leaving them out of that APK.
    val onnx = "com.microsoft.onnxruntime:onnxruntime-android:1.19.2"
    "offlineImplementation"(onnx)
    "hybridImplementation"(onnx)
    "onlineCompileOnly"(onnx)

    // Encrypted local storage.
    val room = "2.6.1"
    implementation("androidx.room:room-runtime:$room")
    implementation("androidx.room:room-ktx:$room")
    ksp("androidx.room:room-compiler:$room")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.8.1")

    // Ed25519 signature verification for offline FV1 credentials (minSdk 26 -
    // java.security only gains Ed25519 at API 33).
    implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")

    // On-device QR scanning for the credential verifier (bundled model, offline).
    implementation("com.google.mlkit:barcode-scanning:17.3.0")

    // JVM unit tests (Protect.kt golden vectors - real org.json instead of the stub).
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
