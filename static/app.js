// ---------------------------------------------------------------------------
// Face Verify — front-camera client.
//   Enroll : center your face, tap Capture (x3).
//   Verify : tap Verify -> server issues a head-turn challenge -> we record a
//            short burst while you turn your head -> server checks liveness +
//            matches. A flat photo can't perform a real 3D head turn.
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const video = $('video'), canvas = $('canvas'), ctx = canvas.getContext('2d');
const scanner = document.querySelector('.scanner');
const modeVerify = $('mode-verify'), modeEnroll = $('mode-enroll'), segThumb = $('seg-thumb');
const enrollRow = $('enroll-row'), userId = $('user-id'), dots = $('dots');
const hint = $('hint'), bar = $('bar'), progressWrap = $('progress-wrap'), statusText = $('status-text');
const captureBtn = $('capture-btn'), swapBtn = $('swap-btn');
const result = $('result'), resultSvg = $('result-svg');
const resultTitle = $('result-title'), resultSub = $('result-sub'), againBtn = $('again');
const themeBtn = $('theme-btn'), themeIcon = $('theme-icon');

// --- light/dark theme -------------------------------------------------------
const SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>';
const MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>';
function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    themeIcon.innerHTML = t === 'light' ? MOON : SUN;   // show the icon you'd switch TO
    try { localStorage.setItem('theme', t); } catch (e) {}
}
themeBtn.addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'));
applyTheme((() => { try { return localStorage.getItem('theme'); } catch (e) { return null; } })() || 'dark');

const ICON_OK = '<path d="M20 6 9 17l-5-5"/>';
const ICON_BAD = '<path d="M18 6 6 18M6 6l12 12"/>';

const ENROLL_TARGET = 3;
const OUT_W = 720;
const BURST_FRAMES = 7, BURST_GAP_MS = 280;    // ~2s head-turn recording
let mode = 'verify', busy = false;
const wait = (ms) => new Promise(r => setTimeout(r, ms));

let facing = 'user';                         // 'user' = front (selfie), 'environment' = rear
async function startCamera() {
    try {
        const old = video.srcObject;
        if (old) old.getTracks().forEach(t => t.stop());
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false,
        });
        video.srcObject = stream;
        video.classList.toggle('mirror', facing === 'user');   // mirror front only
        statusText.textContent = 'Ready';
        captureBtn.disabled = false;
        showDeviceTip();                                        // palm camera guidance
    } catch (err) {
        statusText.textContent = 'No camera';
        setHint('Camera unavailable — allow camera access and reload.', 'warn');
        captureBtn.disabled = true;
    }
}
async function swapCamera() {
    if (busy) return;
    facing = facing === 'user' ? 'environment' : 'user';
    await startCamera();
    setHint(facing === 'user' ? 'Front camera' : 'Back camera');
}

function setHint(t, kind = '') { hint.textContent = t; hint.className = 'hint' + (kind ? ' ' + kind : ''); }

// Smart palm guidance: nudge toward the rear camera (sharpest for palm) on phones,
// or toward face on laptops / low-res webcams. Quick, model-free.
let deviceTipDismissed = false;
let tipTimer = null;
function hideDeviceTip() {
    if (tipTimer) { clearTimeout(tipTimer); tipTimer = null; }
    $('device-tip').classList.add('hidden');
}
function renderTip(adv, autoHideMs) {
    const tip = $('device-tip'); if (!tip) return;
    $('device-tip-text').textContent = adv.text;
    const btn = $('device-tip-action');
    if (adv.action === 'switch-rear') {
        btn.textContent = 'Use back camera'; btn.hidden = false;
        btn.onclick = () => { hideDeviceTip(); if (facing !== 'environment') swapCamera(); };
    } else {
        btn.hidden = true;                       // 'use-face' is informational only
    }
    tip.classList.remove('hidden');
    if (tipTimer) clearTimeout(tipTimer);
    if (autoHideMs) tipTimer = setTimeout(() => tip.classList.add('hidden'), autoHideMs);
}
// One gentle note at the start of a session — shown once, auto-dismissed, never spammed.
async function showDeviceTip() {
    if (deviceTipDismissed || !window.DeviceGuide) return;
    if (sessionStorage.getItem('palmTipShown')) return;
    let adv = null;
    try { adv = await window.DeviceGuide.palmAdvice(video.srcObject); } catch (_) {}
    if (!adv) return;
    sessionStorage.setItem('palmTipShown', '1');
    renderTip(adv, 7000);
}
// Reactive + accurate: the server just routed a capture to PALM, so we KNOW the user
// is using palm. If the camera isn't ideal for it, nudge now (auto-dismissed).
async function palmCameraNudge() {
    if (deviceTipDismissed || !window.DeviceGuide) return;
    let adv = null;
    try { adv = await window.DeviceGuide.palmAdvice(video.srcObject); } catch (_) {}
    if (adv && adv.action) renderTip(adv, 8000);
}
function grabFrame() {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return null;
    const w = Math.min(OUT_W, vw), h = Math.round(w * vh / vw);
    canvas.width = w; canvas.height = h;
    ctx.drawImage(video, 0, 0, w, h);            // true (un-mirrored) frame
    return canvas.toDataURL('image/jpeg', 0.9);
}

function startBusy(status) {
    busy = true; captureBtn.disabled = true; scanner.classList.add('busy');
    statusText.textContent = status; progressWrap.classList.remove('hidden');
}

async function onCapture() {
    if (busy) return;
    if (mode === 'enroll') return enrollCapture();
    return verify();
}

async function ensureAdmin() {
    // Enrolment is restricted. If not already signed in, prompt for the admin password.
    const s = await (await fetch('/admin/session')).json().catch(() => ({ admin: false }));
    if (s.admin) return true;
    const user = prompt('Enrolment is restricted. Admin username:', 'admin');
    if (user === null) return false;
    const pw = prompt('Admin password:');
    if (!pw) return false;
    const r = await fetch('/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user.trim(), password: pw }) });
    if (!r.ok) { setHint('Incorrect username or password.', 'warn'); return false; }
    return true;
}

async function enrollCapture() {
    if (!userId.value.trim()) { setHint('Enter a name or ID to enrol first'); userId.focus(); return; }
    if (!(await ensureAdmin())) return;
    const img = grabFrame();
    if (!img) { setHint('Camera not ready — try again.'); return; }
    startBusy('Checking');
    let p = 25; bar.style.width = '25%'; setHint('Checking…');
    const anim = setInterval(() => { p = Math.min(95, p + 6); bar.style.width = p + '%'; }, 140);
    try {
        const res = await fetch('/api/enroll', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: img, user_id: userId.value.trim() }) });
        const data = await res.json();
        clearInterval(anim); bar.style.width = '100%';
        setTimeout(() => handle(data), 150);
    } catch (e) { clearInterval(anim); reset('Network error — is the server running?', 'warn'); }
}

// Read a File into a data URL (base64) for /api/enroll.
function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(fr.result);
        fr.onerror = reject;
        fr.readAsDataURL(file);
    });
}

// Enroll from one or more chosen photos (same admin-gated /api/enroll as the camera;
// ID cards auto-branch server-side via source:"auto").
async function enrollFromFiles() {
    if (!userId.value.trim()) { setHint('Enter a name or ID to enroll first'); userId.focus(); return; }
    const files = Array.from($('enroll-files').files || []);
    if (!files.length) { setHint('Choose one or more photos first'); return; }
    if (!(await ensureAdmin())) return;
    startBusy('Uploading');
    let ok = 0, last = null;
    for (let i = 0; i < files.length; i++) {
        setHint(`Enrolling photo ${i + 1}/${files.length}…`);
        bar.style.width = Math.round(((i + 1) / files.length) * 100) + '%';
        try {
            const img = await fileToDataUrl(files[i]);
            const res = await fetch('/api/enroll', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId.value.trim(), image: img }) });
            last = await res.json();
            if (last.success) ok++;
        } catch (e) { /* keep going through the rest */ }
    }
    $('enroll-files').value = '';
    if (last) handle(last);                       // show the final per-photo result + dots
    else reset('Could not read those photos — try different files.', 'warn');
    if (ok > 1) setHint(`Enrolled ${ok}/${files.length} photo(s).`);
}

async function verify() {
    const img0 = grabFrame();
    if (!img0) { setHint('Camera not ready — try again.'); return; }
    startBusy('Liveness');
    let ch;
    try { ch = await (await fetch('/api/challenge')).json(); }
    catch (e) { reset('Network error — is the server running?', 'warn'); return; }

    if (!ch || !ch.active) {                       // active liveness off -> single shot
        return singleVerify(img0);
    }
    // Record a burst while guiding the user through the head turn in real time.
    statusText.textContent = 'Liveness';
    setHint('Keep your face in the oval…');
    await wait(400);
    const frames = [];
    for (let i = 0; i < BURST_FRAMES; i++) {
        const f = grabFrame(); if (f) frames.push(f);
        const frac = (i + 1) / BURST_FRAMES;
        setHint(frac < 0.45 ? '⟵  Slowly turn your head LEFT'
              : frac < 0.85 ? 'Now turn your head RIGHT  ⟶'
              :               'Look at the camera', 'info');
        bar.style.width = Math.round(frac * 100) + '%';
        await wait(BURST_GAP_MS);
    }
    statusText.textContent = 'Checking'; setHint('Checking…');
    try {
        const res = await fetch('/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frames, token: ch.token }) });
        const data = await res.json();
        setTimeout(() => handle(data), 120);
    } catch (e) { reset('Network error — is the server running?', 'warn'); }
}

async function singleVerify(img) {
    let p = 25; bar.style.width = '25%'; setHint('Checking…');
    const anim = setInterval(() => { p = Math.min(95, p + 6); bar.style.width = p + '%'; }, 140);
    try {
        const res = await fetch('/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: img }) });
        const data = await res.json();
        clearInterval(anim); bar.style.width = '100%';
        setTimeout(() => handle(data), 150);
    } catch (e) { clearInterval(anim); reset('Network error — is the server running?', 'warn'); }
}

function handle(data) {
    statusText.textContent = 'Ready'; scanner.classList.remove('busy');
    progressWrap.classList.add('hidden'); bar.style.width = '0%';
    // The server routed this to palm (a hand was seen) — if the camera isn't ideal
    // for palm, nudge toward the rear/face now. This is the accurate palm-intent signal.
    if (data.modality === 'palm' || data.matched_modality === 'palm' ||
        (typeof data.code === 'string' && data.code.startsWith('palm_'))) palmCameraNudge();
    if (['liveness', 'low_quality', 'multiple_faces'].includes(data.code)) { reset(data.message, 'warn'); return; }

    if (mode === 'enroll') {
        const n = data.samples || 0;
        renderDots(n);
        const idNote = data.source === 'id_document'
            ? ' (from ID document — add a live capture for best accuracy)' : '';
        if (data.success && n < ENROLL_TARGET) { reset(`Captured ${n}/${ENROLL_TARGET}${idNote} — tap Capture again`); return; }
        if (data.success) { show('ok', ICON_OK, 'Enrolled', `${userId.value.trim()} is ready to verify${idNote}`); userId.value = ''; renderDots(0); return; }
        if (data.code === 'inconsistent' || data.code === 'duplicate') { reset(data.message, 'warn'); return; }
        show('bad', ICON_BAD, 'Enrolment failed', data.message || ''); return;
    }
    if (data.success) {
        const via = data.matched_modality || data.modality;
        const tag = (via === 'face' || via === 'palm') ? ` (via ${via})` : '';
        show('ok', ICON_OK, 'Access granted', data.user_id ? `Welcome, ${data.user_id}${tag}` : '');
    } else if (data.code === 'no_biometric_detected') {
        show('bad', ICON_BAD, 'Nothing detected', 'Show your face — or your open palm — clearly');
    } else if (data.code === 'step_up_required') {
        show('warn', ICON_BAD, 'One more step', data.message || 'Also present your other biometric');
    } else {
        show('bad', ICON_BAD, 'Access denied', 'Face or palm not recognised');
    }
}

function show(kind, icon, title, sub) {
    busy = false; captureBtn.disabled = false;
    setHint('');                                  // clear "Checking…" under the oval
    result.className = 'result ' + kind;
    resultSvg.innerHTML = icon; resultTitle.textContent = title; resultSub.textContent = sub || '';
    result.classList.remove('hidden');
}
function reset(msg, kind = '') {
    busy = false; captureBtn.disabled = false; scanner.classList.remove('busy');
    progressWrap.classList.add('hidden'); bar.style.width = '0%';
    setHint(msg || defaultHint(), kind);
}
function defaultHint() {
    return mode === 'enroll' ? 'Show your face or open palm, then tap Capture (3 times)'
                             : 'Show your face — or your open palm — then tap Verify';
}

againBtn.addEventListener('click', () => { result.classList.add('hidden'); reset(); });
captureBtn.addEventListener('click', onCapture);
$('upload-enroll').addEventListener('click', enrollFromFiles);

function renderDots(n) {
    dots.innerHTML = '';
    if (mode !== 'enroll') return;
    for (let i = 0; i < ENROLL_TARGET; i++) { const d = document.createElement('i'); if (i < n) d.className = 'on'; dots.appendChild(d); }
}
function setMode(m) {
    mode = m;
    const enr = m === 'enroll';
    modeEnroll.classList.toggle('is-active', enr); modeVerify.classList.toggle('is-active', !enr);
    modeEnroll.setAttribute('aria-selected', enr); modeVerify.setAttribute('aria-selected', !enr);
    segThumb.classList.toggle('right', enr);
    enrollRow.classList.toggle('hidden', !enr);
    result.classList.add('hidden'); renderDots(0);
    captureBtn.textContent = enr ? 'Capture' : 'Verify';
    reset();
}
modeEnroll.addEventListener('click', () => setMode('enroll'));
modeVerify.addEventListener('click', () => setMode('verify'));

swapBtn.addEventListener('click', swapCamera);
{
    const dismiss = $('device-tip-dismiss');
    if (dismiss) dismiss.addEventListener('click', () => {
        deviceTipDismissed = true;          // user dismissed — don't nudge again this session
        hideDeviceTip();
    });
}

setMode('verify');
startCamera();

// Register the service worker so the app is installable / loads instantly.
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

// --- Install as an app (PWA) -----------------------------------------------
// Desktop Chrome/Edge + Android Chrome fire `beforeinstallprompt`; we capture it
// and reveal an explicit Install button that triggers the native prompt on click.
// iOS Safari has no prompt API → show Add-to-Home-Screen instructions instead.
(function installSetup() {
    const btn = $('install-btn');
    if (!btn) return;
    let deferred = null;
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches
        || window.navigator.standalone === true;        // iOS
    const ua = navigator.userAgent || '';
    const isIOS = /iphone|ipad|ipod/i.test(ua) && !window.MSStream;
    const inIframe = window.self !== window.top;

    if (isStandalone) return;                            // already installed → keep hidden

    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        deferred = e;
        btn.hidden = false;                              // now installable → show button
    });
    window.addEventListener('appinstalled', () => { btn.hidden = true; deferred = null; });

    // iOS never fires beforeinstallprompt; offer the button with manual instructions.
    if (isIOS && !inIframe) btn.hidden = false;

    btn.addEventListener('click', async () => {
        if (deferred) {
            deferred.prompt();
            await deferred.userChoice.catch(() => {});
            deferred = null; btn.hidden = true;
            return;
        }
        if (isIOS) {
            setHint('To install: tap the Share icon, then "Add to Home Screen".', 'info');
            return;
        }
        // Fallback (e.g. opened inside the HF Space iframe, where install is blocked)
        setHint(inIframe
            ? 'Open this page in its own tab (not embedded) to install it as an app.'
            : 'Use your browser menu → "Install app" / "Add to Home screen".', 'info');
    });
})();
