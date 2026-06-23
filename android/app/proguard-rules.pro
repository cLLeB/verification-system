# ONNX Runtime
-keep class ai.onnxruntime.** { *; }
-dontwarn ai.onnxruntime.**
# ML Kit
-keep class com.google.mlkit.** { *; }
-dontwarn com.google.mlkit.**
# Room generated
-keep class * extends androidx.room.RoomDatabase { *; }
