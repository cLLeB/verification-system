package com.faceverify.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.faceverify.app.R
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import com.faceverify.app.data.AdminGate

@Composable
fun MainScreen() {
    val vm: ScannerViewModel = viewModel()
    val ctx = LocalContext.current
    val adminGate = remember { AdminGate(ctx) }
    var tab by remember { mutableIntStateOf(0) }

    val pal = Tok.current
    Scaffold(
        containerColor = pal.bg0,
        topBar = { TopBar() },
        bottomBar = {
            // The bar sits ON the page background, not on a lighter slab: Material's
            // default container is an elevated surface, which read as a grey band under
            // the tabs (and under the gesture area) that exists nowhere on the web.
            NavigationBar(containerColor = pal.bg0, tonalElevation = 0.dp) {
                val items = NavigationBarItemDefaults.colors(
                    selectedIconColor = pal.onBrand,
                    selectedTextColor = pal.brand,
                    indicatorColor = pal.brand,
                    unselectedIconColor = pal.txt2,
                    unselectedTextColor = pal.txt2,
                )
                NavigationBarItem(tab == 0, { tab = 0 }, colors = items,
                    icon = { Icon(Icons.Filled.Face, null) }, label = { Text("Scan") })
                NavigationBarItem(tab == 1, { tab = 1; vm.refreshPeople() }, colors = items,
                    icon = { Icon(Icons.Filled.People, null) }, label = { Text("People") })
                NavigationBarItem(tab == 2, { tab = 2 }, colors = items,
                    icon = { Icon(Icons.Filled.Settings, null) }, label = { Text("Settings") })
            }
        }
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when {
                vm.engineError != null -> CenterMessage(
                    "Engine not ready",
                    vm.engineError ?: "The face model is missing from assets. See android/README.",
                )
                !vm.ready -> Column(
                    Modifier.fillMaxSize(), Arrangement.Center, Alignment.CenterHorizontally
                ) { CircularProgressIndicator(); Spacer(Modifier.height(12.dp)); Text(vm.status) }
                else -> when (tab) {
                    0 -> ScanScreen(vm, adminGate)
                    1 -> PeopleScreen(vm)
                    else -> SettingsScreen(vm)
                }
            }
        }
    }
}

/** The light/dark switch, and nothing else.
 *
 *  The web's `.topbar` also carries a brand mark, but a phone app does not need to tell
 *  you which app you opened - the launcher icon and the task switcher already did - and
 *  that row was costing the capture screen height it needed for the shutter. So the bar
 *  is one control, kept as short as a touch target allows. */
@Composable
private fun TopBar() {
    val pal = Tok.current
    val theme = LocalThemeController.current
    Row(
        Modifier.fillMaxWidth().padding(end = 6.dp, top = 2.dp),
        horizontalArrangement = Arrangement.End,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = { theme.toggle() }, modifier = Modifier.size(40.dp)) {
            // The icon shows the theme you would switch TO - the same rule as the site.
            Icon(
                painterResource(if (theme.isDark) R.drawable.ic_sun else R.drawable.ic_moon),
                contentDescription = "Switch light/dark theme",
                tint = pal.txt2,
                modifier = Modifier.size(18.dp),
            )
        }
    }
}

@Composable
private fun ScanScreen(vm: ScannerViewModel, adminGate: AdminGate) {
    val pal = Tok.current
    var adminUnlocked by remember { mutableStateOf(false) }
    var showPin by remember { mutableStateOf(false) }
    var lensFacing by remember { mutableIntStateOf(androidx.camera.core.CameraSelector.LENS_FACING_FRONT) }
    val pickPhoto = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) vm.enrollFromPhoto(uri)
    }

    // The web reclaims height in enrol mode on a short viewport rather than letting
    // the shutter fall under the fold (`@media (max-height: 800px)` on
    // `html[data-mode="enroll"]`). Same rule, same trigger.
    val compact = vm.mode == Mode.ENROLL && LocalConfiguration.current.screenHeightDp <= 800
    val gap = if (compact) 8.dp else 12.dp

    Column(
        // NOT scrollable, and nothing here is allowed to reflow. Everything except the
        // camera has a fixed height and the oval absorbs whatever is left - so a longer
        // status line, a retry countdown, or the enrol dots filling in can never push
        // the shutter down (or under the navigation bar). That is the same trade the
        // web makes when it shrinks the oval instead of growing the page.
        Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        // Mode switch. "Check card" and "Glance" both match on-device, against a
        // credential's own template or a downloaded index - neither is possible on the
        // online build, which bundles no recognition model, so they are not offered.
        val modes = remember(vm.isOnline) {
            if (vm.isOnline) listOf(Mode.VERIFY, Mode.ENROLL)
            else listOf(Mode.VERIFY, Mode.ENROLL, Mode.CREDENTIAL, Mode.GLANCE)
        }
        val labels = remember(modes) {
            modes.map {
                when (it) {
                    Mode.VERIFY -> "Verify"
                    Mode.ENROLL -> "Enrol"
                    Mode.CREDENTIAL -> "Check card"
                    Mode.GLANCE -> "Glance"
                }
            }
        }
        SegmentedControl(
            options = labels,
            selectedIndex = modes.indexOf(vm.mode).coerceAtLeast(0),
            onSelect = { i ->
                val m = modes[i]
                vm.selectMode(m)
                // Cards are scanned with the BACK camera, and Glance points at OTHER
                // people - both flip automatically; the face modes flip back.
                lensFacing = if (m == Mode.CREDENTIAL || m == Mode.GLANCE)
                    androidx.camera.core.CameraSelector.LENS_FACING_BACK
                else androidx.camera.core.CameraSelector.LENS_FACING_FRONT
            },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(gap))
        if (vm.mode == Mode.CREDENTIAL && vm.credPayload != null && vm.result == null) {
            // card accepted - the live person is captured with the front camera
            LaunchedEffect(vm.credPayload) {
                lensFacing = androidx.camera.core.CameraSelector.LENS_FACING_FRONT
            }
        }

        if (vm.mode == Mode.ENROLL) {
            // Name and photo-upload on ONE line: the upload is optional, and the camera
            // is what people came to use, so it should not be pushed down the screen.
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = vm.enrollName, onValueChange = { vm.enrollName = it },
                    placeholder = { Text("Name or ID to enrol") }, singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    onClick = {
                        if (adminUnlocked)
                            pickPhoto.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                        else showPin = true
                    },
                    enabled = vm.enrollName.isNotBlank(),
                ) { Icon(Icons.Filled.Image, "Enrol from a photo") }
            }
            Spacer(Modifier.height(gap))
            // Fixed-height row: dots filling in as samples are taken must not change
            // the height of anything below them.
            Box(Modifier.fillMaxWidth().height(20.dp), contentAlignment = Alignment.Center) {
                Dots(captured = vm.captured, total = vm.enrollTarget)
            }
            Spacer(Modifier.height(gap))
        }

        // A palm matching neither of this name's enrolled hands: confirm binding it as
        // the person's OTHER hand (so either palm verifies them later).
        vm.pendingOtherHand?.let { msg ->
            AlertDialog(
                onDismissRequest = { vm.cancelOtherHand() },
                title = { Text("Different hand?") },
                text = { Text(msg) },
                confirmButton = { TextButton(onClick = { vm.confirmOtherHand() }) { Text("Add other hand") } },
                dismissButton = { TextButton(onClick = { vm.cancelOtherHand() }) { Text("Cancel") } },
            )
        }

        // The scanner stage - halo, oval, scan light, dashed guide, swap (see
        // ScannerStage.kt, ported from the web client's .scanner-stage). This is the
        // ONE flexible box on the screen: it takes the height the fixed chrome leaves,
        // is never wider than the web's 300px, and keeps its 7:9 shape either way.
        ScannerStage(
            lensFacing = lensFacing,
            busy = vm.capturing,
            capturing = vm.capturing,
            showSwap = vm.result == null,
            onSwap = {
                lensFacing = if (lensFacing == androidx.camera.core.CameraSelector.LENS_FACING_FRONT)
                    androidx.camera.core.CameraSelector.LENS_FACING_BACK
                else androidx.camera.core.CameraSelector.LENS_FACING_FRONT
            },
            shouldProcess = { vm.tryBeginFrame() },
            onBitmap = { vm.processFrame(it) },
            modifier = Modifier
                .weight(1f, fill = false)
                .sizeIn(maxWidth = 300.dp)
                .aspectRatio(7f / 9f, matchHeightConstraintsFirst = true),
        ) {
            vm.result?.let { ResultOverlay(it.ok, it.title, it.sub) { vm.scanAgain() } }
            // Glance: a live name chip instead of a frozen verdict - batch friendly
            if (vm.mode == Mode.GLANCE && vm.glanceHit != null) {
                Surface(
                    color = pal.ok, shape = MaterialTheme.shapes.large,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 18.dp),
                ) {
                    Text(
                        vm.glanceHit ?: "",
                        color = pal.onBrand,
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    )
                }
            }
        }

        Spacer(Modifier.height(gap))

        // `.hint` - the line of guidance. On the web it reserves its own line
        // (`min-height: 1.2em`) so it cannot shove the page around; two lines' worth
        // here, because "Couldn't detect a face" style copy wraps on a phone and the
        // shutter must not move for the second or two that it shows.
        Box(Modifier.fillMaxWidth().height(46.dp), contentAlignment = Alignment.Center) {
            if (vm.result == null) {
                Text(
                    if (vm.retryIn > 0) "${vm.status} - retrying in ${vm.retryIn}…" else vm.status,
                    textAlign = TextAlign.Center,
                    // `.hint` is 0.9rem/500 on the web. At the default body size two
                    // lines did not fit the reserved slot and the second one was cut.
                    fontSize = 14.4.sp,
                    lineHeight = 20.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 2,
                    color = if (vm.retryIn > 0) pal.warn else pal.txt2,
                )
            }
        }

        Spacer(Modifier.height(if (compact) 6.dp else 10.dp))

        Box(Modifier.fillMaxWidth().height(if (compact) 26.dp else 30.dp), contentAlignment = Alignment.Center) {
            if (vm.result == null && (vm.mode == Mode.VERIFY || vm.mode == Mode.ENROLL)) {
                QualityChips(vm.quality, compact = compact)
            }
        }

        Spacer(Modifier.height(if (compact) 8.dp else 12.dp))

        // The capture control keeps its slot whatever the mode, so switching modes
        // does not move the shutter either.
        Box(
            Modifier.fillMaxWidth().height(if (compact) 68.dp else 78.dp),
            contentAlignment = Alignment.Center,
        ) {
            if (vm.result == null) {
                if (vm.mode == Mode.VERIFY || vm.mode == Mode.ENROLL) {
                    Shutter(
                        ready = vm.quality.allGood,
                        busy = vm.capturing,
                        progress = vm.captureProgress,
                        enabled = vm.mode != Mode.ENROLL || vm.enrollName.isNotBlank(),
                        compact = compact,
                        onClick = {
                            // Enrolling is the operator's action and stays PIN-gated;
                            // verifying is open, exactly as on the web.
                            if (vm.mode == Mode.ENROLL && !adminUnlocked) showPin = true
                            else vm.requestCapture()
                        },
                    )
                } else if (vm.mode == Mode.CREDENTIAL) {
                    LinearProgressIndicator(
                        progress = { vm.livenessProgress },
                        modifier = Modifier.fillMaxWidth(0.6f),
                    )
                }
            }
        }
    }

    if (showPin) PinDialog(
        creating = !adminGate.isSet(),
        onDismiss = { showPin = false },
        onConfirm = { pin ->
            val ok = if (!adminGate.isSet()) { adminGate.setPin(pin); true } else adminGate.check(pin)
            if (ok) {
                adminUnlocked = true
                showPin = false
                // They tapped the shutter and were interrupted by the PIN - finish the
                // action they asked for rather than making them tap a second time.
                if (vm.mode == Mode.ENROLL) vm.requestCapture()
            }
            ok
        },
    )
}

@Composable
private fun PinDialog(creating: Boolean, onDismiss: () -> Unit, onConfirm: (String) -> Boolean) {
    var pin by remember { mutableStateOf("") }
    var error by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (creating) "Create admin PIN" else "Admin PIN") },
        text = {
            Column {
                Text(
                    if (creating) "Set a PIN to protect enrolment. You'll need it to add people."
                    else "Enter the admin PIN to enrol.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = pin, onValueChange = { pin = it; error = false },
                    label = { Text("PIN") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    isError = error,
                )
                if (error) Text("Incorrect PIN", color = MaterialTheme.colorScheme.error)
            }
        },
        confirmButton = {
            TextButton(onClick = { if (pin.length >= 4) { if (!onConfirm(pin)) error = true } }) {
                Text(if (creating) "Set PIN" else "Unlock")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun PeopleScreen(vm: ScannerViewModel) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Text("Enrolled people (${vm.people.size})", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(12.dp))
        if (vm.people.isEmpty()) {
            CenterMessage(
                if (vm.peopleMsg.isEmpty()) "No one enrolled yet" else "Can't show the roster",
                vm.peopleMsg.ifEmpty { "Use the Scan tab → Enrol to add people." },
            )
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(vm.people) { name ->
                    Card(Modifier.fillMaxWidth()) {
                        Row(
                            Modifier.fillMaxWidth().padding(14.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(name, Modifier.weight(1f))
                            IconButton(onClick = { vm.deleteUser(name) }) {
                                Icon(Icons.Filled.Delete, "Delete", tint = MaterialTheme.colorScheme.error)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsScreen(vm: ScannerViewModel) {
    val ctx = LocalContext.current
    Column(Modifier.fillMaxSize().padding(20.dp).verticalScroll(rememberScrollState())) {
        Text("Settings", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(16.dp))
        InfoRow("People enrolled", vm.people.size.toString())
        if (!vm.isOnline) {
            InfoRow("Match threshold", com.faceverify.app.Config.MATCH_THRESHOLD.toString())
        }
        InfoRow(
            "Recognition",
            if (vm.isOnline) "On the server" else "On this device",
        )
        InfoRow(
            "Storage",
            if (vm.isOnline) "None on this device" else "Encrypted, on-device only",
        )
        InfoRow(
            "Network", when {
                vm.isOnline -> "Required - frames are sent to the server"
                vm.isHybrid -> "Hybrid - optional server sync"
                else -> "None - fully offline"
            }
        )
        Spacer(Modifier.height(20.dp))

        // The online build has no local templates, so none of the on-device data
        // sections (bulk import, trust list, glance index) apply to it.
        if (vm.isOnline) {
            OnlineSection(vm)
            return@Column
        }

        if (vm.isHybrid) {
            SyncSection(vm, ctx)
        } else {
            Text(
                "Face Verify runs entirely on this device. Faces are turned into an encrypted " +
                    "mathematical template - no photos and no data ever leave the phone.",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(24.dp))
        BundleImportSection(vm, ctx)
        Spacer(Modifier.height(24.dp))
        TrustStoreSection(vm)
        Spacer(Modifier.height(24.dp))
        GlanceIndexSection(vm)
    }
}

/** The Glance mode's 1:N index: one compact vector per enrolled person (~50 MB
 *  per 100k). Hybrid refreshes from the server; any build imports the encrypted
 *  export file. Without it, Glance falls back to locally enrolled people. */
@Composable
private fun GlanceIndexSection(vm: ScannerViewModel) {
    var passphrase by remember { mutableStateOf("") }
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) vm.importGlanceIndex(uri, passphrase)
    }
    Text("Glance index", style = MaterialTheme.typography.titleMedium)
    Text(
        "\"Glance\" identifies people continuously against this index - every enrolled " +
            "identity, matched on this phone in under a second, fully offline.",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(10.dp))
    InfoRow("Index", vm.glanceSummary())
    Spacer(Modifier.height(8.dp))
    if (vm.isHybrid) {
        Button(onClick = { vm.refreshGlanceIndex() }, enabled = !vm.glanceBusy) {
            Icon(Icons.Filled.CloudDownload, null); Spacer(Modifier.size(8.dp)); Text("Update from server")
        }
        Spacer(Modifier.height(8.dp))
    }
    OutlinedTextField(
        value = passphrase, onValueChange = { passphrase = it },
        label = { Text("Export file passphrase (for file import)") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        modifier = Modifier.fillMaxWidth(),
    )
    Spacer(Modifier.height(8.dp))
    OutlinedButton(
        onClick = { pickFile.launch("*/*") },
        enabled = !vm.glanceBusy && passphrase.isNotBlank(),
    ) { Text("Import index file…") }
    if (vm.glanceBusy) { Spacer(Modifier.height(6.dp)); LinearProgressIndicator(Modifier.fillMaxWidth()) }
    if (vm.glanceMsg.isNotEmpty()) {
        Spacer(Modifier.height(8.dp))
        Text(vm.glanceMsg, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Trust list for the offline credential verifier ("Check card" mode): which
 *  issuers' signed QR cards this device accepts + their revocation lists.
 *  Hybrid refreshes from the server; any build can import a saved file. */
@Composable
private fun TrustStoreSection(vm: ScannerViewModel) {
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) vm.importTrust(uri)
    }
    Text("Credential trust list", style = MaterialTheme.typography.titleMedium)
    Text(
        "\"Check card\" verifies signed QR credentials against this list of issuers " +
            "and their revocations - fully offline. Refresh it when you can so " +
            "revoked cards are rejected promptly.",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(10.dp))
    InfoRow("Trust list", vm.trustSummary())
    Spacer(Modifier.height(8.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (vm.isHybrid) {
            Button(onClick = { vm.refreshTrust() }, enabled = !vm.trustBusy) {
                Icon(Icons.Filled.CloudDownload, null); Spacer(Modifier.size(8.dp)); Text("Refresh")
            }
        }
        OutlinedButton(onClick = { pickFile.launch("*/*") }, enabled = !vm.trustBusy) {
            Text("Import file…")
        }
    }
    if (vm.trustBusy) { Spacer(Modifier.height(6.dp)); LinearProgressIndicator(Modifier.fillMaxWidth()) }
    if (vm.trustMsg.isNotEmpty()) {
        Spacer(Modifier.height(8.dp))
        Text(vm.trustMsg, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Offline provisioning: import a passphrase-encrypted template bundle the admin
 *  exported on the server and moved here out-of-band (USB / MDM). No network is
 *  used - the airgap is preserved. PIN-gated like the sync settings. */
@Composable
private fun BundleImportSection(vm: ScannerViewModel, ctx: android.content.Context) {
    val adminGate = remember { AdminGate(ctx) }
    var unlocked by remember { mutableStateOf(false) }
    var showPin by remember { mutableStateOf(false) }
    var passphrase by remember { mutableStateOf("") }
    var pickedName by remember { mutableStateOf<String?>(null) }
    var pickedUri by remember { mutableStateOf<android.net.Uri?>(null) }

    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        pickedUri = uri
        pickedName = uri?.lastPathSegment
    }

    Text("Bulk import (offline)", style = MaterialTheme.typography.titleMedium)
    Text(
        "Load a roster the administrator exported on the server. The file is decrypted " +
            "here with its passphrase - no network is used, the device stays offline.",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(12.dp))

    if (!unlocked) {
        Button(onClick = { showPin = true }) {
            Icon(Icons.Filled.Lock, null); Spacer(Modifier.size(8.dp)); Text("Unlock import")
        }
    } else {
        OutlinedButton(onClick = { pickFile.launch("*/*") }) {
            Text(pickedName?.let { "File: $it" } ?: "Choose bundle file…")
        }
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = passphrase, onValueChange = { passphrase = it },
            label = { Text("Bundle passphrase") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = { pickedUri?.let { vm.importBundle(it, passphrase) } },
            enabled = !vm.bundleBusy && pickedUri != null && passphrase.isNotBlank(),
        ) {
            Icon(Icons.Filled.CloudDownload, null); Spacer(Modifier.size(8.dp)); Text("Import")
        }
        if (vm.bundleBusy) { Spacer(Modifier.height(6.dp)); LinearProgressIndicator(Modifier.fillMaxWidth()) }
        if (vm.bundleMsg.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(vm.bundleMsg, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }

    if (showPin) PinDialog(
        creating = !adminGate.isSet(),
        onDismiss = { showPin = false },
        onConfirm = { pin ->
            if (!adminGate.isSet()) { adminGate.setPin(pin); unlocked = true; showPin = false; true }
            else if (adminGate.check(pin)) { unlocked = true; showPin = false; true }
            else false
        },
    )
}

@Composable
private fun SyncSection(vm: ScannerViewModel, ctx: android.content.Context) {
    val adminGate = remember { AdminGate(ctx) }
    var unlocked by remember { mutableStateOf(false) }
    var showPin by remember { mutableStateOf(false) }
    var url by remember { mutableStateOf(vm.syncServerUrl()) }
    var key by remember { mutableStateOf("") }
    var conflict by remember { mutableStateOf("skip") }

    Text("Server sync", style = MaterialTheme.typography.titleMedium)
    Text(
        "Mirror your company's dataset to match offline, and push on-device enrolments up. " +
            "Which dataset is determined by your API key.",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(12.dp))

    if (!unlocked) {
        Button(onClick = { showPin = true }) {
            Icon(Icons.Filled.Lock, null); Spacer(Modifier.size(8.dp)); Text("Unlock sync settings")
        }
    } else {
        OutlinedTextField(
            value = url, onValueChange = { url = it },
            label = { Text("Server URL (https://…)") }, singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = key, onValueChange = { key = it },
            label = { Text(if (vm.syncApiKeySet()) "API key (set - blank keeps it)" else "API key") },
            singleLine = true, visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.saveSyncConfig(url, key); key = "" }) { Text("Save") }
            OutlinedButton(onClick = { vm.testSync() }, enabled = !vm.syncBusy) { Text("Test") }
        }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.pullNow() }, enabled = !vm.syncBusy) {
                Icon(Icons.Filled.CloudDownload, null); Spacer(Modifier.size(8.dp)); Text("Pull")
            }
            Button(onClick = { vm.pushAll(conflict) }, enabled = !vm.syncBusy) {
                Icon(Icons.Filled.CloudUpload, null); Spacer(Modifier.size(8.dp)); Text("Push all")
            }
        }
        Spacer(Modifier.height(12.dp))
        Text("If a face already exists under a different name:",
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("skip", "merge", "force").forEach { m ->
                FilterChip(conflict == m, { conflict = m }, { Text(m) })
            }
        }
        Spacer(Modifier.height(12.dp))
        InfoRow("Last sync", vm.lastSyncLabel())
        if (vm.syncBusy) { Spacer(Modifier.height(6.dp)); LinearProgressIndicator(Modifier.fillMaxWidth()) }
        if (vm.syncMsg.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(vm.syncMsg, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        vm.syncConflicts.forEach { c ->
            Text("• $c", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        Spacer(Modifier.height(20.dp))
        Text("This device", style = MaterialTheme.typography.titleMedium)
        Text(
            "Pair this kiosk with a single-use code from the console: it gets its own " +
                "identity and key, shows up with a live last-seen, and can be cut off " +
                "remotely without touching any other device.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        InfoRow("Paired as", vm.deviceLabel())
        Spacer(Modifier.height(8.dp))
        var pairCode by remember { mutableStateOf("") }
        OutlinedTextField(
            value = pairCode, onValueChange = { pairCode = it },
            label = { Text("Pairing code (pc_…)") }, singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Button(onClick = { vm.pairDevice(pairCode); pairCode = "" }, enabled = !vm.pairBusy) {
            Text("Pair this device")
        }
        if (vm.pairMsg.isNotEmpty()) {
            Spacer(Modifier.height(6.dp))
            Text(vm.pairMsg, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }

    if (showPin) PinDialog(
        creating = !adminGate.isSet(),
        onDismiss = { showPin = false },
        onConfirm = { pin ->
            if (!adminGate.isSet()) { adminGate.setPin(pin); unlocked = true; showPin = false; true }
            else if (adminGate.check(pin)) { unlocked = true; showPin = false; true }
            else false
        },
    )
}

/** The online build's only setting that matters: which deployment to verify against.
 *
 *  The admin password is optional and separate. Verifying never needs it. Enrolling
 *  needs it only once the deployment closes open enrolment, and the People tab always
 *  does - so an operator can leave it unset on a walk-up verifier and nothing breaks. */
@Composable
private fun OnlineSection(vm: ScannerViewModel) {
    var url by remember { mutableStateOf(vm.onlineServerUrl()) }
    var user by remember { mutableStateOf(vm.onlineAdminUser()) }
    var password by remember { mutableStateOf("") }

    Text("Server", style = MaterialTheme.typography.titleMedium)
    Text(
        "This build recognises people on the server, so nothing is stored on this phone " +
            "and the captured frames are sent for the decision. It needs a connection to work.",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(12.dp))
    OutlinedTextField(
        value = url, onValueChange = { url = it },
        label = { Text("Server address (https://…)") }, singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Spacer(Modifier.height(8.dp))
    OutlinedTextField(
        value = user, onValueChange = { user = it },
        label = { Text("Operator name (leave blank for \"admin\")") }, singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Spacer(Modifier.height(8.dp))
    OutlinedTextField(
        value = password, onValueChange = { password = it },
        label = {
            Text(
                if (vm.onlineAdminPasswordSet()) "Admin password (stored - blank keeps it)"
                else "Admin password (optional)"
            )
        },
        singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        modifier = Modifier.fillMaxWidth(),
    )
    // A stored secret is never shown back, so say plainly that there is one - the empty
    // field on the way back in is otherwise indistinguishable from having lost it.
    if (vm.onlineAdminPasswordSet()) {
        Spacer(Modifier.height(6.dp))
        Text(
            "A password is stored on this device. Typing a new one replaces it.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    Spacer(Modifier.height(4.dp))
    Text(
        "Enrolling needs this only when the deployment closes open enrolment. " +
            "The People tab always needs it - the server keeps the roster admin-only.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(10.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(
            onClick = { vm.saveOnlineConfig(url, user, password); password = "" },
            enabled = !vm.onlineBusy,
        ) { Text("Save") }
        OutlinedButton(onClick = { vm.testOnline() }, enabled = !vm.onlineBusy) { Text("Test") }
        if (vm.onlineAdminPasswordSet()) {
            TextButton(onClick = { vm.clearOnlineAdminPassword() }) { Text("Forget password") }
        }
    }
    if (vm.onlineBusy) { Spacer(Modifier.height(6.dp)); LinearProgressIndicator(Modifier.fillMaxWidth()) }
    if (vm.onlineMsg.isNotEmpty()) {
        Spacer(Modifier.height(8.dp))
        Text(vm.onlineMsg, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Text(label, Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value)
    }
}

@Composable
private fun CenterMessage(title: String, body: String) {
    Column(
        Modifier.fillMaxSize().padding(24.dp), Arrangement.Center, Alignment.CenterHorizontally
    ) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(body, textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
