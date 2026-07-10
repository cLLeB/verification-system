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
const ICON_CAM = '<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>';
const ICON_RETRY = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';

const ENROLL_TARGET = 3;
const statusPill = $('status-pill');
let sampleCount = 0;                       // enrolled samples this session (drives labels)
const shots = [];                          // captured sample images -> progress thumbnails

function setCaptureLabel(t) { captureBtn.innerHTML = ICON_CAM + '<span>' + t + '</span>'; }
function refreshCaptureLabel() {
    setCaptureLabel(mode === 'enroll'
        ? `Capture sample ${Math.min(sampleCount, ENROLL_TARGET - 1) + 1}`
        : 'Capture & verify');
}
// white flash over the oval at the moment of capture
function flashOval() { const f = $('flash'); f.classList.remove('go'); void f.offsetWidth; f.classList.add('go'); }
const OUT_W = 720;
const BURST_FRAMES = 7, BURST_GAP_MS = 280;    // ~2s head-turn recording
let mode = 'verify', busy = false;
const wait = (ms) => new Promise(r => setTimeout(r, ms));

// --- Live-preview watchdog — production camera-freeze fix --------------------
// iOS Safari pauses an inline, transformed <video> after a canvas capture plus
// CSS animations, and never auto-resumes. A paused <video> keeps re-drawing its
// LAST decoded frame, so drawImage()/toDataURL() return byte-identical images —
// that is why enrolment recorded the same frozen frame as samples 2 and 3. A
// MUTED video may always be replayed programmatically (autoplay policy), so we
// resume it on every pause / tab return, and wait for a genuinely fresh frame
// before every capture.
function keepVideoAlive(v) {
    if (!v || v._liveWatch) return;
    v._liveWatch = true;
    const resume = () => { if (v.srcObject && v.paused) { const p = v.play(); if (p && p.catch) p.catch(() => {}); } };
    v.addEventListener('pause', resume);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) resume(); });
    window.addEventListener('focus', resume);
    window.addEventListener('pageshow', resume);
}
function nextVideoFrame(v) {
    return new Promise((resolve) => {
        let done = false;
        const finish = () => { if (!done) { done = true; resolve(); } };
        if (typeof v.requestVideoFrameCallback === 'function') {
            try { v.requestVideoFrameCallback(() => finish()); } catch (e) { finish(); }
        } else {
            const t0 = v.currentTime, started = performance.now();
            const tick = () => {
                if (v.currentTime > t0 || performance.now() - started > 250) finish();
                else requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        }
        setTimeout(finish, 350);   // hard cap: a capture must never hang
    });
}
async function ensureLiveVideo(v) {
    if (!v || !v.srcObject) return;
    if (v.paused || v.ended) { const p = v.play(); if (p) { try { await p; } catch (e) {} } }
    await nextVideoFrame(v);
}

let facing = 'user';                         // 'user' = front (selfie), 'environment' = rear
async function startCamera() {
    try {
        const old = video.srcObject;
        if (old) old.getTracks().forEach(t => t.stop());
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false,
        });
        video.srcObject = stream;
        try { await video.play(); } catch (e) {}                // iOS sometimes needs an explicit play
        keepVideoAlive(video);                                  // auto-resume if iOS pauses the preview
        video.classList.toggle('mirror', facing === 'user');   // mirror front only
        statusText.textContent = 'Ready';
        statusPill.classList.remove('bad');
        $('cam-denied').classList.add('hidden');
        captureBtn.disabled = false;
        showDeviceTip();                                        // palm camera guidance
    } catch (err) {
        statusText.textContent = 'Blocked';
        statusPill.classList.add('bad');
        $('cam-denied').classList.remove('hidden');
        setHint('Camera access denied. Enable it in your browser to continue.', 'warn');
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
    statusPill.classList.remove('ok');
    setCaptureLabel('Capturing…');
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

let lastEnrollImg = null;                     // last capture, for the "add other hand" confirm

// One place that POSTs an enrolment. `hand` ("other") is sent only when the admin
// confirms a person's second palm after a different_hand prompt.
function postEnroll(img, hand) {
    const body = { image: img, user_id: userId.value.trim() };
    if (hand) body.hand = hand;
    return fetch('/api/enroll', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) }).then((r) => r.json());
}

async function enrollCapture() {
    if (!userId.value.trim()) { setHint('Enter a name or ID to enrol first'); userId.focus(); return; }
    if (!(await ensureAdmin())) return;
    await ensureLiveVideo(video);                 // never capture a stale/frozen frame
    const img = grabFrame();
    if (!img) { setHint('Camera not ready — try again.'); return; }
    lastEnrollImg = img;
    flashOval();
    startBusy('Checking');
    let p = 25; bar.style.width = '25%'; setHint('Checking…');
    const anim = setInterval(() => { p = Math.min(95, p + 6); bar.style.width = p + '%'; }, 140);
    try {
        const data = await postEnroll(img);
        if (data.success) shots.push(img);            // sample thumbnail for the dots row
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
            lastEnrollImg = img;
            // Uploading several photos is an authorized batch -> auto-bind up to two
            // hands (both palms under one name) with no per-photo confirmation.
            last = await postEnroll(img, files.length > 1 ? 'any' : undefined);
            if (last.success) { ok++; shots.push(img); }   // thumbnail for the dots row
        } catch (e) { /* keep going through the rest */ }
    }
    $('enroll-files').value = '';
    if (last) handle(last);                       // show the final per-photo result + dots
    else reset('Could not read those photos — try different files.', 'warn');
    if (ok > 1) setHint(`Enrolled ${ok}/${files.length} photo(s).`);
}

async function verify() {
    await ensureLiveVideo(video);
    const img0 = grabFrame();
    if (!img0) { setHint('Camera not ready — try again.'); return; }
    flashOval();
    startBusy('Detecting');
    // Quick modality check so a palm skips the face head-turn challenge.
    let modality = 'face';
    try {
        const d = await (await fetch('/api/detect', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: img0 }) })).json();
        modality = d.modality || 'face';
    } catch (e) { /* default to face */ }
    if (modality === 'palm') return palmVerify();
    if (modality === 'none') { reset('Show your face — or your open palm — clearly', 'warn'); return; }

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

// Palm verify: a short steady burst (no head-turn — that's a face challenge). The
// server picks the SHARPEST frame, so one motion-ghosted frame in dim light doesn't
// sink the attempt.
async function palmVerify() {
    await ensureLiveVideo(video);
    setHint('Hold your open palm steady…', 'info');
    statusText.textContent = 'Checking';
    await wait(450);
    const frames = [];
    for (let i = 0; i < 3; i++) {
        const f = grabFrame(); if (f) frames.push(f);
        bar.style.width = (20 + i * 10) + '%';
        if (i < 2) await wait(220);
    }
    if (!frames.length) { reset('Camera not ready — try again.', 'warn'); return; }
    let p = 50; bar.style.width = '50%';
    const anim = setInterval(() => { p = Math.min(95, p + 8); bar.style.width = p + '%'; }, 120);
    try {
        const res = await fetch('/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frames }) });
        const data = await res.json();
        clearInterval(anim); bar.style.width = '100%';
        setTimeout(() => handle(data), 120);
    } catch (e) { clearInterval(anim); reset('Network error — is the server running?', 'warn'); }
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

async function handle(data) {
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
        const handLabel = data.hand === 2 ? "this person's other hand" : 'this palm';
        if (data.success && n < ENROLL_TARGET) { reset(`Captured ${n}/${ENROLL_TARGET} for ${handLabel}${idNote} — tap Capture again`); return; }
        if (data.success) {
            const more = data.hand === 1
                ? ' — capture their OTHER hand now for either-hand verify, or Start over'
                : '';
            show('ok', ICON_OK, 'Enrolled', `${userId.value.trim()} is ready to verify${idNote}${more}`);
            if (data.hand !== 1) { userId.value = ''; }   // keep the name to add the 2nd hand
            renderDots(0); return;                        // renderDots(0) also clears thumbnails
        }
        // A palm that matches neither enrolled hand: offer to bind it as the 2nd hand.
        if (data.code === 'different_hand') {
            if (lastEnrollImg && confirm(data.message || "Add as this person's other hand?")) {
                startBusy('Adding other hand');
                const d2 = await postEnroll(lastEnrollImg, 'other');
                if (d2.success) shots.push(lastEnrollImg);
                return handle(d2);
            }
            reset('Okay — present the SAME hand you enrolled first.', 'warn'); return;
        }
        if (data.code === 'hands_full') { show('warn', ICON_BAD, 'Both hands already enrolled', data.message || ''); return; }
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
    refreshCaptureLabel();
    if (kind === 'ok') { statusText.textContent = 'Complete'; statusPill.classList.add('ok'); }
    // done-state action: "Verify again" / "Start over" with a restart icon (per design)
    againBtn.innerHTML = ICON_RETRY + '<span>' + (mode === 'verify' ? 'Verify again' : 'Start over') + '</span>';
    setHint('');                                  // clear "Checking…" under the oval
    result.className = 'result ' + kind;
    resultSvg.innerHTML = icon; resultTitle.textContent = title; resultSub.textContent = sub || '';
    result.classList.remove('hidden');
}
function reset(msg, kind = '') {
    busy = false; captureBtn.disabled = false; scanner.classList.remove('busy');
    statusPill.classList.remove('ok');
    refreshCaptureLabel();
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
// selecting a reference photo submits it directly (no separate button, per design)
$('enroll-files').addEventListener('change', () => { if ($('enroll-files').files.length) enrollFromFiles(); });

// "n of 3 samples captured" bold line under the instruction (enroll only, per design)
function updateHintSub(n) {
    const el = $('hint-sub');
    if (mode !== 'enroll') { el.classList.add('hidden'); return; }
    el.textContent = `${Math.min(n, ENROLL_TARGET)} of ${ENROLL_TARGET} samples captured`;
    el.classList.remove('hidden');
}
function renderDots(n) {
    sampleCount = n;
    if (n === 0) shots.length = 0;             // fresh enrolment -> drop old thumbnails
    updateHintSub(n);
    refreshCaptureLabel();
    dots.innerHTML = '';
    if (mode !== 'enroll') return;
    for (let i = 0; i < ENROLL_TARGET; i++) {
        if (i < n && shots[i]) {               // captured -> glowing photo thumbnail
            const im = document.createElement('img');
            im.src = shots[i]; im.className = 'thumb'; im.alt = `Sample ${i + 1}`;
            dots.appendChild(im); continue;
        }
        const d = document.createElement('i');
        if (i < n) d.className = 'on';         // captured but no local image (e.g. resumed)
        else if (i === n) d.className = 'next'; // the slot being captured pulses
        dots.appendChild(d);
    }
}
function setMode(m) {
    mode = m;
    const enr = m === 'enroll';
    modeEnroll.classList.toggle('is-active', enr); modeVerify.classList.toggle('is-active', !enr);
    modeEnroll.setAttribute('aria-selected', enr); modeVerify.setAttribute('aria-selected', !enr);
    segThumb.classList.toggle('right', enr);
    enrollRow.classList.toggle('hidden', !enr);
    result.classList.add('hidden'); renderDots(0);
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
