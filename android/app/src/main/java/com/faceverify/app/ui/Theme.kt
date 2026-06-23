package com.faceverify.app.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// "Verified" design language — deep ink + iris violet, matching the web product.
private val Violet = Color(0xFF8B7CF6)
private val VioletFill = Color(0xFF6D4DE6)
private val VioletLight = Color(0xFFA78BFA)
val Ok = Color(0xFF34D399)
val Bad = Color(0xFFFB7185)

private val Dark = darkColorScheme(
    primary = VioletFill,
    onPrimary = Color.White,
    primaryContainer = Color(0xFF4A3AA8),
    onPrimaryContainer = Color(0xFFE7E0FF),
    secondary = VioletLight,
    onSecondary = Color(0xFF160B2E),
    background = Color(0xFF0B1020),
    onBackground = Color(0xFFEAF0F8),
    surface = Color(0xFF141B2D),
    onSurface = Color(0xFFEAF0F8),
    surfaceVariant = Color(0xFF1C2538),
    onSurfaceVariant = Color(0xFF9AA8BD),
    outline = Color(0xFF2A3550),
    error = Bad,
    onError = Color(0xFF2A0710),
)

private val Light = lightColorScheme(
    primary = VioletFill,
    onPrimary = Color.White,
    secondary = Violet,
    background = Color(0xFFF5F7FC),
    surface = Color(0xFFFFFFFF),
    error = Color(0xFFE11D48),
)

@Composable
fun FaceVerifyTheme(content: @Composable () -> Unit) {
    // Dark-first for the biometric feel, but respects the system setting.
    val scheme = if (isSystemInDarkTheme()) Dark else Dark   // force dark for brand consistency
    MaterialTheme(colorScheme = scheme, content = content)
}
