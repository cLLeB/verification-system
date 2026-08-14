package com.faceverify.app.ui

import android.app.Activity
import android.content.Context
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowInsetsControllerCompat

/** "Verified" - the one design language for the whole platform.
 *
 *  These are the web client's tokens (static/app.css `:root` and its
 *  `html[data-theme="light"]` block), converted from oklch to sRGB, so the phone and the
 *  browser are the same product rather than two lookalikes. The app used to carry a
 *  violet palette that had drifted from the site's iris BLUE, and only a dark theme
 *  while the site had a toggle.
 *
 *  Anything that needs a colour takes it from [Tok] or [MaterialTheme] - never a literal
 *  at the call site, which is how the drift happened the first time. */
@Immutable
data class Palette(
    val bg0: Color,          // app background (deep ink / near-white)
    val bg1: Color,          // surfaces: cards, pills, inputs
    val bg2: Color,          // elevated / muted track
    val inset: Color,        // sunken inputs / rows
    val txt: Color,          // primary text
    val txt2: Color,         // secondary text
    val txtMuted: Color,     // placeholders, captions
    val brand: Color,        // iris blue - primary / scanner ring
    val brand2: Color,       // light - gradient pair / active glow
    val brandStrong: Color,  // pressed / deep fill
    val onBrand: Color,      // text on the bright primary fill
    val ok: Color,           // granted
    val bad: Color,          // denied
    val warn: Color,
    val info: Color,
    val line: Color,         // hairline border / divider
    val lineStrong: Color,   // hover / active / focus border
)

val DarkPalette = Palette(
    bg0 = Color(0xFF050911), bg1 = Color(0xFF0F141D), bg2 = Color(0xFF1A2029),
    inset = Color(0xFF0A1018),
    txt = Color(0xFFF7F9FA), txt2 = Color(0xFF979FAB), txtMuted = Color(0xFF6D7580),
    brand = Color(0xFF5598FF), brand2 = Color(0xFF82B6FF), brandStrong = Color(0xFF3D7EFC),
    onBrand = Color(0xFF050911),
    ok = Color(0xFF22C373), bad = Color(0xFFF04C55),
    warn = Color(0xFFEDB417), info = Color(0xFF00AEE9),
    // `--line` is white at low alpha on the web; reading it as alpha rather than a flat
    // grey is what keeps it subtle over the page AND over the camera oval.
    line = Color.White.copy(alpha = 0.08f), lineStrong = Color.White.copy(alpha = 0.16f),
)

val LightPalette = Palette(
    bg0 = Color(0xFFF9FAFC), bg1 = Color(0xFFFFFFFF), bg2 = Color(0xFFEBEFF4),
    inset = Color(0xFFEEF2F7),
    txt = Color(0xFF070B14), txt2 = Color(0xFF5C646F), txtMuted = Color(0xFF6A727E),
    brand = Color(0xFF1E64EF), brand2 = Color(0xFF427FF7), brandStrong = Color(0xFF024DD6),
    onBrand = Color(0xFFFFFFFF),
    ok = Color(0xFF00B667), bad = Color(0xFFE60016),
    warn = Color(0xFFE38F00), info = Color(0xFF007CDF),
    line = Color(0xFF0A0F1A).copy(alpha = 0.08f), lineStrong = Color(0xFF0A0F1A).copy(alpha = 0.16f),
)

val LocalPalette = staticCompositionLocalOf { DarkPalette }

/** `Tok.current.brand` at a call site, mirroring `var(--brand)` in the stylesheet. */
object Tok {
    val current: Palette
        @Composable get() = LocalPalette.current
}

enum class ThemeMode { DARK, LIGHT }

/** The light/dark switch, held where the whole app can reach it. Persisted, and
 *  dark by default - the same behaviour as the web client's toggle, which stores the
 *  choice in localStorage and falls back to dark. */
class ThemeController(initial: ThemeMode, private val persist: (ThemeMode) -> Unit) {
    var mode by mutableStateOf(initial)
        private set

    val isDark: Boolean get() = mode == ThemeMode.DARK

    fun toggle() {
        mode = if (mode == ThemeMode.DARK) ThemeMode.LIGHT else ThemeMode.DARK
        persist(mode)
    }
}

val LocalThemeController = staticCompositionLocalOf {
    ThemeController(ThemeMode.DARK) {}          // replaced by FaceVerifyTheme
}

private const val UI_PREFS = "faceverify_ui"
private const val KEY_THEME = "theme"

private fun colors(p: Palette, dark: Boolean) = if (dark) {
    darkColorScheme(
        primary = p.brand, onPrimary = p.onBrand,
        primaryContainer = p.brandStrong, onPrimaryContainer = p.txt,
        secondary = p.brand2, onSecondary = p.onBrand,
        background = p.bg0, onBackground = p.txt,
        surface = p.bg1, onSurface = p.txt,
        surfaceVariant = p.bg2, onSurfaceVariant = p.txt2,
        // Material paints navigation bars and menus from these; left at the page
        // background so no surface reads as a grey slab against it.
        surfaceContainer = p.bg0, surfaceContainerHigh = p.bg1, surfaceContainerHighest = p.bg1,
        surfaceContainerLow = p.bg0, surfaceContainerLowest = p.bg0,
        outline = p.lineStrong, outlineVariant = p.line,
        error = p.bad, onError = p.onBrand,
    )
} else {
    lightColorScheme(
        primary = p.brand, onPrimary = p.onBrand,
        primaryContainer = p.brand2, onPrimaryContainer = p.txt,
        secondary = p.brand2, onSecondary = p.onBrand,
        background = p.bg0, onBackground = p.txt,
        surface = p.bg1, onSurface = p.txt,
        surfaceVariant = p.bg2, onSurfaceVariant = p.txt2,
        surfaceContainer = p.bg0, surfaceContainerHigh = p.bg1, surfaceContainerHighest = p.bg1,
        surfaceContainerLow = p.bg0, surfaceContainerLowest = p.bg0,
        outline = p.lineStrong, outlineVariant = p.line,
        error = p.bad, onError = p.onBrand,
    )
}

@Composable
fun FaceVerifyTheme(content: @Composable () -> Unit) {
    val ctx = LocalContext.current
    val controller = remember {
        val prefs = ctx.getSharedPreferences(UI_PREFS, Context.MODE_PRIVATE)
        val saved = if (prefs.getString(KEY_THEME, "dark") == "light") ThemeMode.LIGHT else ThemeMode.DARK
        ThemeController(saved) { m ->
            prefs.edit().putString(KEY_THEME, if (m == ThemeMode.LIGHT) "light" else "dark").apply()
        }
    }
    val palette = if (controller.isDark) DarkPalette else LightPalette

    // The window chrome is painted by the framework, not by Compose: without this the
    // status and navigation bars keep the XML theme's dark colour (and its light-on-dark
    // icons) after a switch to the light theme, leaving black bands top and bottom.
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as? Activity)?.window ?: return@SideEffect
            window.statusBarColor = palette.bg0.toArgb()
            window.navigationBarColor = palette.bg0.toArgb()
            WindowInsetsControllerCompat(window, view).apply {
                isAppearanceLightStatusBars = !controller.isDark
                isAppearanceLightNavigationBars = !controller.isDark
            }
        }
    }

    CompositionLocalProvider(
        LocalPalette provides palette,
        LocalThemeController provides controller,
    ) {
        MaterialTheme(colorScheme = colors(palette, controller.isDark), content = content)
    }
}
