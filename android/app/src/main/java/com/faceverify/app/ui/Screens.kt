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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cameraswitch
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.CheckCircle
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.compose.ui.platform.LocalContext
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

    Scaffold(
        bottomBar = {
            NavigationBar {
                NavigationBarItem(tab == 0, { tab = 0 },
                    icon = { Icon(Icons.Filled.Face, null) }, label = { Text("Scan") })
                NavigationBarItem(tab == 1, { tab = 1; vm.refreshPeople() },
                    icon = { Icon(Icons.Filled.People, null) }, label = { Text("People") })
                NavigationBarItem(tab == 2, { tab = 2 },
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

@Composable
private fun ScanScreen(vm: ScannerViewModel, adminGate: AdminGate) {
    var adminUnlocked by remember { mutableStateOf(false) }
    var showPin by remember { mutableStateOf(false) }
    var lensFacing by remember { mutableIntStateOf(androidx.camera.core.CameraSelector.LENS_FACING_FRONT) }
    val pickPhoto = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) vm.enrollFromPhoto(uri)
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Face Verify", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(12.dp))

        // Mode toggle
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(vm.mode == Mode.VERIFY, { vm.selectMode(Mode.VERIFY) }, { Text("Verify") })
            FilterChip(vm.mode == Mode.ENROLL, { vm.selectMode(Mode.ENROLL) }, { Text("Enrol") })
        }
        Spacer(Modifier.height(12.dp))

        if (vm.mode == Mode.ENROLL) {
            OutlinedTextField(
                value = vm.enrollName, onValueChange = { vm.enrollName = it },
                label = { Text("Name or ID") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            Dots(captured = vm.captured, total = vm.enrollTarget)
            Spacer(Modifier.height(8.dp))
        }

        // Camera oval + result overlay
        Box(
            Modifier.fillMaxWidth(0.8f).aspectRatio(0.8f)
                .clip(CircleShape)
                .border(3.dp, MaterialTheme.colorScheme.primary, CircleShape)
                .background(Color.Black),
            contentAlignment = Alignment.Center,
        ) {
            CameraPreview(
                modifier = Modifier.fillMaxSize(),
                lensFacing = lensFacing,
                shouldProcess = { vm.tryBeginFrame() },
                onBitmap = { vm.processFrame(it) },
            )
            if (vm.result == null) {
                IconButton(
                    onClick = {
                        lensFacing = if (lensFacing == androidx.camera.core.CameraSelector.LENS_FACING_FRONT)
                            androidx.camera.core.CameraSelector.LENS_FACING_BACK
                        else androidx.camera.core.CameraSelector.LENS_FACING_FRONT
                    },
                    modifier = Modifier.align(Alignment.TopEnd).padding(6.dp),
                ) {
                    Icon(Icons.Filled.Cameraswitch, "Flip camera", tint = Color.White)
                }
            }
            vm.result?.let { ResultOverlay(it) { vm.scanAgain() } }
        }

        Spacer(Modifier.height(14.dp))
        if (vm.result == null) {
            Text(
                vm.status, textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (vm.mode == Mode.VERIFY) {
                Spacer(Modifier.height(10.dp))
                LinearProgressIndicator(
                    progress = { vm.livenessProgress },
                    modifier = Modifier.fillMaxWidth(0.6f),
                )
            } else {
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = {
                        if (adminUnlocked) vm.requestEnrollCapture() else showPin = true
                    },
                    enabled = vm.enrollName.isNotBlank(),
                    modifier = Modifier.fillMaxWidth(0.6f),
                ) {
                    Icon(Icons.Filled.Lock, null); Spacer(Modifier.size(8.dp)); Text("Capture")
                }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(
                    onClick = {
                        if (adminUnlocked)
                            pickPhoto.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                        else showPin = true
                    },
                    enabled = vm.enrollName.isNotBlank(),
                    modifier = Modifier.fillMaxWidth(0.6f),
                ) {
                    Icon(Icons.Filled.Image, null); Spacer(Modifier.size(8.dp)); Text("Enrol from photo")
                }
            }
        }
    }

    if (showPin) PinDialog(
        creating = !adminGate.isSet(),
        onDismiss = { showPin = false },
        onConfirm = { pin ->
            if (!adminGate.isSet()) { adminGate.setPin(pin); adminUnlocked = true; showPin = false; true }
            else if (adminGate.check(pin)) { adminUnlocked = true; showPin = false; true }
            else false
        },
    )
}

@Composable
private fun ResultOverlay(result: ScanResult, onAgain: () -> Unit) {
    Box(
        Modifier.fillMaxSize().background(Color(0xE60B1020)),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(20.dp)) {
            Icon(
                if (result.ok) Icons.Filled.CheckCircle else Icons.Filled.Cancel,
                contentDescription = null,
                tint = if (result.ok) Ok else Bad,
                modifier = Modifier.size(64.dp),
            )
            Spacer(Modifier.height(8.dp))
            Text(result.title, style = MaterialTheme.typography.titleLarge, color = Color.White)
            Text(result.sub, color = Color(0xFF9AA8BD), textAlign = TextAlign.Center)
            Spacer(Modifier.height(14.dp))
            Button(onClick = onAgain) { Text("Scan again") }
        }
    }
}

@Composable
private fun Dots(captured: Int, total: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        repeat(total) { i ->
            Box(
                Modifier.size(10.dp).clip(CircleShape).background(
                    if (i < captured) Ok else MaterialTheme.colorScheme.surfaceVariant
                )
            )
        }
    }
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
            CenterMessage("No one enrolled yet", "Use the Scan tab → Enrol to add people.")
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
    Column(Modifier.fillMaxSize().padding(20.dp)) {
        Text("Settings", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(16.dp))
        InfoRow("People enrolled", vm.people.size.toString())
        InfoRow("Match threshold", com.faceverify.app.Config.MATCH_THRESHOLD.toString())
        InfoRow("Storage", "Encrypted, on-device only")
        InfoRow("Network", "None — fully offline")
        Spacer(Modifier.height(20.dp))
        Text(
            "Face Verify runs entirely on this device. Faces are turned into an encrypted " +
                "mathematical template — no photos and no data ever leave the phone.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
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
