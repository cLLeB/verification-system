// ---------------------------------------------------------------------------
// Face Verify - front-camera client.
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
const hint = $('hint'), statusText = $('status-text');
// The loading bar under the shutter was removed; these stand in for it so the
// capture flow keeps its single set of progress calls instead of sprouting a
// null-check at every step.
const bar = { style: {} };
const progressWrap = { classList: { add() {}, remove() {} } };
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
// ICON_CAM retired with the text capture button (the shutter draws itself).
// const ICON_CAM = '<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>';
const ICON_RETRY = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';

const ENROLL_TARGET = 3;
const statusPill = $('status-pill');
let sampleCount = 0;                       // enrolled samples this session (drives labels)
const shots = [];                          // captured sample images -> progress thumbnails

// The capture control is a shutter, not a text button, so the wording lives in
// aria-label/title (screen readers and long-press still get it) and never as
// innerHTML - writing innerHTML here would blow away the shutter's own elements.
// The "n of 3" count already has two better homes: the dots row and hint-sub.
function setCaptureLabel(t) {
    captureBtn.setAttribute('aria-label', t);
    captureBtn.title = t;
}
function refreshCaptureLabel() {
    setCaptureLabel(mode === 'enroll'
        ? `Capture sample ${Math.min(sampleCount, ENROLL_TARGET - 1) + 1} of ${ENROLL_TARGET}`
        : 'Capture and verify');
}
// white flash over the oval at the moment of capture
function flashOval() { const f = $('flash'); f.classList.remove('go'); void f.offsetWidth; f.classList.add('go'); }
const OUT_W = 720;

// Guided head-turn. The old loop grabbed 7 frames 280 ms apart - under 2 SECONDS
// for "turn left, turn right, look at camera" - and set the instruction AFTER
// grabbing each frame, so the first frames were recorded before the user had been
// told anything. Nobody can perform that, which is why live verifies failed with
// "Turn your head a bit more, side to side". Each instruction now appears BEFORE
// its frames and stays up long enough to act on.
const TURN_PHASES = [
    { hint: '⟵  Slowly turn your head LEFT', frames: 4 },
    { hint: 'Now turn your head RIGHT  ⟶', frames: 4 },
    { hint: 'Look straight at the camera', frames: 3 },
];
const PHASE_LEAD_MS = 350;                     // read it and start moving first
const BURST_GAP_MS = 250;                      // ~3.8s total, vs 2s before
let mode = 'verify', busy = false;
const wait = (ms) => new Promise(r => setTimeout(r, ms));

// --- Live-preview watchdog - production camera-freeze fix --------------------
// iOS Safari pauses an inline, transformed <video> after a canvas capture plus
// CSS animations, and never auto-resumes. A paused <video> keeps re-drawing its
// LAST decoded frame, so drawImage()/toDataURL() return byte-identical images -
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

// --- Live capture coaching -------------------------------------------------
// Tell the person WHAT is wrong while they are still framing, instead of failing
// the shot and leaving them to guess. Three signals, each measured rather than
// invented:
//   Lighting - mean luma of the oval region, computed here from the video. Free
//              and instant, so it needs no network at all.
//   Distance - how much of the frame the detected face/palm fills.
//   Angle    - palm facing the camera with fingers spread, or head yaw/pitch
//              inside the accept range.
// Distance and Angle can only come from the detector, so a small frame goes to
// /api/detect?coach at a slow cadence. The thresholds stay on the SERVER, next to
// the ones enrolment actually enforces, so the chips can never drift out of sync
// with the gate they are predicting.
// Everything here is best-effort: it never blocks a capture, never throws into
// the capture path, and stops while busy or backgrounded so it cannot compete
// with a real request for the 2-vCPU container.
const COACH_MS = 900;                       // one probe per ~0.9s while idle
const COACH_W = 320;                        // downscaled probe frame
const LUMA_LOW = 55, LUMA_HIGH = 240;       // usable exposure band
let coachTimer = null, coachBusy = false, coachOn = false;
const coachCanvas = document.createElement('canvas');
const coachCtx = coachCanvas.getContext('2d', { willReadFrequently: true });

function setChip(id, state) {               // state: 'ok' | 'bad' | null (unknown)
    const el = $(id);
    if (!el) return;
    el.classList.toggle('ok', state === 'ok');
    el.classList.toggle('bad', state === 'bad');
}

// Mean luma over the centre of the frame, where the subject actually is.
function frameLuma() {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return null;
    const w = 48, h = 60;
    coachCanvas.width = w; coachCanvas.height = h;
    const cw = vw * 0.6, ch = vh * 0.6;      // centre 60%, roughly the oval
    coachCtx.drawImage(video, (vw - cw) / 2, (vh - ch) / 2, cw, ch, 0, 0, w, h);
    const d = coachCtx.getImageData(0, 0, w, h).data;
    let sum = 0;
    for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
    return sum / (d.length / 4);
}

function coachFrame() {                      // small JPEG for the detector
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return null;
    const w = Math.min(COACH_W, vw), h = Math.round(w * vh / vw);
    coachCanvas.width = w; coachCanvas.height = h;
    coachCtx.drawImage(video, 0, 0, w, h);
    return coachCanvas.toDataURL('image/jpeg', 0.6);
}

async function coachTick() {
    if (!coachOn || coachBusy || busy || document.hidden) return;
    if (!video.srcObject || video.readyState < 2) return;
    coachBusy = true;
    try {
        const luma = frameLuma();
        const lightOk = luma !== null && luma >= LUMA_LOW && luma <= LUMA_HIGH;
        setChip('q-light', luma === null ? null : (lightOk ? 'ok' : 'bad'));

        const img = coachFrame();
        let sizeOk = null, angleOk = null;
        if (img) {
            const r = await fetch('/api/detect', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image: img, coach: 1 }),
            }).then((x) => x.json());
            const q = r && r.quality;
            if (q) { sizeOk = !!q.size_ok; angleOk = !!q.aligned; }
            else if (r && r.modality === 'none') { sizeOk = false; angleOk = null; }
        }
        setChip('q-dist', sizeOk === null ? null : (sizeOk ? 'ok' : 'bad'));
        setChip('q-angle', angleOk === null ? null : (angleOk ? 'ok' : 'bad'));

        // All three in range -> the shutter goes green. The oval's progress ring
        // was removed; the chips already say which signal is out.
        const good = [lightOk, sizeOk === true, angleOk === true].filter(Boolean).length;
        captureBtn.classList.toggle('ready', good === 3);
    } catch (e) {
        /* coaching is advisory: a failed probe leaves the chips as they were */
    } finally {
        coachBusy = false;
    }
}

function startCoach() {
    if (coachTimer) return;
    coachOn = true;
    coachTimer = setInterval(coachTick, COACH_MS);
}
function stopCoach() {
    coachOn = false;
    if (coachTimer) { clearInterval(coachTimer); coachTimer = null; }
    ['q-light', 'q-dist', 'q-angle'].forEach((id) => setChip(id, null));
    captureBtn.classList.remove('ready');
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
        startCoach();                                           // live framing chips
    } catch (err) {
        statusText.textContent = 'Blocked';
        statusPill.classList.add('bad');
        $('cam-denied').classList.remove('hidden');
        setHint('Camera access denied. Enable it in your browser to continue.', 'warn');
        captureBtn.disabled = true;
        stopCoach();
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
// One gentle note at the start of a session - shown once, auto-dismissed, never spammed.
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
    captureBtn.classList.add('busy'); captureBtn.classList.remove('ready');
    statusPill.classList.remove('ok');
    // Clear the PREVIOUS outcome before a new attempt. The result card used to be
    // hidden only by the "Start over" button, so after enrolling one hand its green
    // "Enrolled - ready to verify" card stayed up while the person captured their
    // OTHER hand (or their face) under the same name - the screen then showed
    // "Enrolled" and "Captured 1/3, tap Capture again" at the same time.
    result.classList.add('hidden');
    setCaptureLabel('Capturing…');
    statusText.textContent = status; progressWrap.classList.remove('hidden');
}

// --- Auto-retry after a coachable failure -----------------------------------
// A run that fails on POSITIONING ("turn your head a bit more", "move closer",
// "nothing detected") used to stop dead. The person reads the advice, repositions,
// and waits - without realising the attempt ended and the shutter needs pressing
// again. They had already tapped, so continuing is not a new consent decision; it
// finishes the attempt they started.
// Only codes a person can FIX BY MOVING retry. A real decision - not recognised,
// access denied, consent withdrawn, duplicate - never does, because repeating
// those is pointless and hides the answer. The run is capped so it can never loop,
// and any manual tap takes over and resets the budget.
const RETRY_CODES = ['liveness', 'low_quality', 'multiple_faces', 'no_biometric_detected',
                     'no_hand', 'palm_too_small', 'palm_blurry', 'fingers_not_spread',
                     'palm_not_facing', 'multiple_hands', 'palm_enroll_blurry',
                     'palm_enroll_too_far', 'palm_enroll_too_dark', 'palm_enroll_too_bright'];
const RETRY_MAX = 3, RETRY_DELAY_MS = 2600;   // long enough to read the advice and move
let retryLeft = RETRY_MAX, retryTimer = null, retryTick = null;

function cancelRetry() {
    if (retryTimer) clearTimeout(retryTimer);
    if (retryTick) clearInterval(retryTick);
    retryTimer = retryTick = null;
}
function scheduleRetry(msg) {
    cancelRetry();
    const advice = msg || 'Adjust your position';
    if (retryLeft <= 0) {                     // budget spent: hand control back, clearly
        setHint(`${advice} Tap the shutter when you are ready.`, 'warn');
        return;
    }
    retryLeft--;
    let left = Math.round(RETRY_DELAY_MS / 1000);
    const paint = () => setHint(`${advice} Retrying in ${left}...`, 'warn');
    paint();
    retryTick = setInterval(() => { left = Math.max(0, left - 1); paint(); }, 1000);
    retryTimer = setTimeout(() => {
        cancelRetry();
        if (document.hidden) {              // backgrounded: don't fire into nothing
            setHint(`${advice} Tap the shutter when you are ready.`, 'warn');
            return;
        }
        onCapture();
    }, RETRY_DELAY_MS);
}

async function onCapture() {
    if (busy) return;
    if (mode === 'enroll') return enrollCapture();
    return verify();
}

async function ensureAdmin() {
    // Enrolment is restricted. If not already signed in, prompt for the admin password.
    // While the deployment runs open enrolment (pilot), there's no prompt at all.
    const s = await (await fetch('/admin/session')).json().catch(() => ({ admin: false }));
    if (s.open_enroll || s.admin) return true;
    const user = prompt('Enrolment is restricted. Admin username:', 'admin');
    if (user === null) return false;
    const pw = prompt('Admin password:');
    if (!pw) return false;
    const r = await fetch('/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user.trim(), password: pw }) });
    if (!r.ok) { setHint('Incorrect username or password.', 'warn'); return false; }
    return true;
}

let lastEnrollPayload = null;                 // last capture body ({frames}|{image}) for the "add other hand" confirm
let lastEnrollThumb = null;                   // a single frame for the dots row

// One place that POSTs an enrolment. `payload` is {frames:[...]} (a burst - preferred)
// or {image:...}. `hand` ("other"/"any") is sent to bind a person's second palm.
function postEnroll(payload, hand) {
    const body = { user_id: userId.value.trim(), ...payload };
    if (hand) body.hand = hand;
    return fetch('/api/enroll', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) }).then((r) => r.json());
}

// A short burst of fresh stills - the server keeps the sharpest, so one soft/ghosted
// frame never decides an enrolment (no single-still captures anywhere).
async function captureStillBurst(n = 5, gap = 110) {
    const frames = [];
    for (let i = 0; i < n; i++) {
        await ensureLiveVideo(video);
        const f = grabFrame();
        if (f) frames.push(f);
        if (i < n - 1) await wait(gap);
    }
    return frames;
}

async function enrollCapture() {
    if (!userId.value.trim()) { setHint('Enter a name or ID to enrol first'); userId.focus(); return; }
    if (!(await ensureAdmin())) return;
    flashOval();
    startBusy('Checking');
    let p = 25; bar.style.width = '25%'; setHint('Checking…');
    const anim = setInterval(() => { p = Math.min(95, p + 6); bar.style.width = p + '%'; }, 140);
    try {
        const frames = await captureStillBurst();
        if (!frames.length) { clearInterval(anim); reset('Camera not ready - try again.', 'warn'); return; }
        lastEnrollPayload = { frames };
        lastEnrollThumb = frames[frames.length - 1];
        const data = await postEnroll(lastEnrollPayload);
        if (data.success) shots.push(lastEnrollThumb);   // sample thumbnail for the dots row
        clearInterval(anim); bar.style.width = '100%';
        setTimeout(() => handle(data), 150);
    } catch (e) { clearInterval(anim); reset('Network error - is the server running?', 'warn'); }
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
            lastEnrollPayload = { image: img };
            lastEnrollThumb = img;
            // Uploading several photos is an authorized batch -> auto-bind up to two
            // hands (both palms under one name) with no per-photo confirmation.
            last = await postEnroll(lastEnrollPayload, files.length > 1 ? 'any' : undefined);
            if (last.success) { ok++; shots.push(img); }   // thumbnail for the dots row
        } catch (e) { /* keep going through the rest */ }
    }
    $('enroll-files').value = '';
    if (last) handle(last);                       // show the final per-photo result + dots
    else reset('Could not read those photos - try different files.', 'warn');
    if (ok > 1) setHint(`Enrolled ${ok}/${files.length} photo(s).`);
}

async function verify() {
    await ensureLiveVideo(video);
    const img0 = grabFrame();
    if (!img0) { setHint('Camera not ready - try again.'); return; }
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
    if (modality === 'none') { reset('Show your face - or your open hand - clearly', 'warn'); return; }

    let ch;
    try { ch = await (await fetch('/api/challenge')).json(); }
    catch (e) { reset('Network error - is the server running?', 'warn'); return; }

    if (!ch || !ch.active) {                       // active liveness off -> single shot
        return singleVerify(img0);
    }
    // Record a burst while guiding the user through the head turn in real time.
    statusText.textContent = 'Liveness';
    setHint('Keep your face in the oval…');
    await wait(400);
    const frames = [];
    const totalFrames = TURN_PHASES.reduce((n, p) => n + p.frames, 0);
    let taken = 0;
    for (const phase of TURN_PHASES) {
        setHint(phase.hint, 'info');
        await wait(PHASE_LEAD_MS);             // instruction first, THEN record
        for (let i = 0; i < phase.frames; i++) {
            const f = grabFrame(); if (f) frames.push(f);
            taken++;
            bar.style.width = Math.round(taken / totalFrames * 100) + '%';
            await wait(BURST_GAP_MS);
        }
    }
    statusText.textContent = 'Checking'; setHint('Checking…');
    try {
        const res = await fetch('/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frames, token: ch.token }) });
        const data = await res.json();
        setTimeout(() => handle(data), 120);
    } catch (e) { reset('Network error - is the server running?', 'warn'); }
}

// Palm verify: a short steady burst (no head-turn - that's a face challenge). The
// server picks the SHARPEST frame, so one motion-ghosted frame in dim light doesn't
// sink the attempt.
async function palmVerify() {
    await ensureLiveVideo(video);
    setHint('Hold your open hand steady…', 'info');
    statusText.textContent = 'Checking';
    await wait(450);
    const frames = [];
    for (let i = 0; i < 3; i++) {
        const f = grabFrame(); if (f) frames.push(f);
        bar.style.width = (20 + i * 10) + '%';
        if (i < 2) await wait(220);
    }
    if (!frames.length) { reset('Camera not ready - try again.', 'warn'); return; }
    let p = 50; bar.style.width = '50%';
    const anim = setInterval(() => { p = Math.min(95, p + 8); bar.style.width = p + '%'; }, 120);
    try {
        const res = await fetch('/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frames }) });
        const data = await res.json();
        clearInterval(anim); bar.style.width = '100%';
        setTimeout(() => handle(data), 120);
    } catch (e) { clearInterval(anim); reset('Network error - is the server running?', 'warn'); }
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
    } catch (e) { clearInterval(anim); reset('Network error - is the server running?', 'warn'); }
}

async function handle(data) {
    statusText.textContent = 'Ready'; scanner.classList.remove('busy');
    progressWrap.classList.add('hidden'); bar.style.width = '0%';
    // The server routed this to palm (a hand was seen) - if the camera isn't ideal
    // for palm, nudge toward the rear/face now. This is the accurate palm-intent signal.
    if (data.modality === 'palm' || data.matched_modality === 'palm' ||
        (typeof data.code === 'string' && data.code.startsWith('palm_'))) palmCameraNudge();
    if (RETRY_CODES.includes(data.code)) { reset(); scheduleRetry(data.message); return; }

    if (mode === 'enroll') {
        const n = data.samples || 0;
        renderDots(n);
        const idNote = data.source === 'id_document'
            ? ' (from ID document - add a live capture for best accuracy)' : '';
        const handLabel = data.hand === 2 ? "this person's other hand" : 'this hand';
        if (data.success && n < ENROLL_TARGET) { reset(`Captured ${n}/${ENROLL_TARGET} for ${handLabel}${idNote} - tap Capture again`); return; }
        if (data.success) {
            const more = data.hand === 1
                ? ' - capture their OTHER hand now for either-hand verify, or Start over'
                : '';
            show('ok', ICON_OK, 'Enrolled', `${userId.value.trim()} is ready to verify${idNote}${more}`);
            if (data.hand !== 1) { userId.value = ''; }   // keep the name to add the 2nd hand
            renderDots(0); return;                        // renderDots(0) also clears thumbnails
        }
        // A palm that matches neither enrolled hand: offer to bind it as the 2nd hand.
        if (data.code === 'different_hand') {
            if (lastEnrollPayload && confirm(data.message || "Add as this person's other hand?")) {
                startBusy('Adding other hand');
                const d2 = await postEnroll(lastEnrollPayload, 'other');
                if (d2.success && lastEnrollThumb) shots.push(lastEnrollThumb);
                return handle(d2);
            }
            reset('Okay - present the SAME hand you enrolled first.', 'warn'); return;
        }
        if (data.code === 'same_hand_side') { show('warn', ICON_BAD, 'Same hand again', data.message || ''); return; }
        if (data.code === 'hands_full') { show('warn', ICON_BAD, 'Both hands already enrolled', data.message || ''); return; }
        if (data.code === 'inconsistent' || data.code === 'duplicate') { reset(data.message, 'warn'); return; }
        show('bad', ICON_BAD, 'Enrolment failed', data.message || ''); return;
    }
    if (data.success) {
        const via = data.matched_modality || data.modality;
        const viaLabel = via === 'palm' ? 'print' : via;   // display-only relabel
        const tag = (via === 'face' || via === 'palm') ? ` (via ${viaLabel})` : '';
        // Guardianship: a verified guardian sees who they may collect for.
        const wards = (data.wards || []).map(w => w.beneficiary || w).join(', ');
        const wardNote = wards ? ` - may collect for: ${wards}` : '';
        show('ok', ICON_OK, 'Access granted', data.user_id ? `Welcome, ${data.user_id}${tag}${wardNote}` : '');
    } else if (data.code === 'no_biometric_detected') {
        reset(); scheduleRetry('Nothing detected - show your face, or your open hand, clearly.');
    } else if (data.code === 'step_up_required') {
        show('warn', ICON_BAD, 'One more step', data.message || 'Also present your other biometric');
    } else if (data.code === 'access_denied') {
        // Recognised, but an access policy denies right now - say so honestly.
        show('warn', ICON_BAD, 'Not allowed right now',
            data.message || 'You were recognised, but access is not permitted at this time.');
    } else if (data.code === 'identity_expired') {
        show('warn', ICON_BAD, 'Guest pass expired',
            data.message || 'You were recognised, but this guest pass has expired - see the front desk.');
    } else if (data.code === 'consent_withdrawn' || data.code === 'consent_missing') {
        show('warn', ICON_BAD, 'Consent required',
            data.message || 'Verification is paused for this record - see the operator.');
    } else {
        show('bad', ICON_BAD, 'Access denied', 'Face or print not recognised');
    }
}

function show(kind, icon, title, sub) {
    busy = false; captureBtn.disabled = false;
    captureBtn.classList.remove('busy');
    cancelRetry(); retryLeft = RETRY_MAX;
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
    captureBtn.classList.remove('busy');
    statusPill.classList.remove('ok');
    refreshCaptureLabel();
    progressWrap.classList.add('hidden'); bar.style.width = '0%';
    result.classList.add('hidden');     // a hint replaces the old result, never sits under it
    setHint(msg || defaultHint(), kind);
}
function defaultHint() {
    return '';        // no idle instruction line; the shutter speaks for itself
}

againBtn.addEventListener('click', () => { cancelRetry(); retryLeft = RETRY_MAX; result.classList.add('hidden'); reset(); });
captureBtn.addEventListener('click', () => { cancelRetry(); retryLeft = RETRY_MAX; onCapture(); });
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
    // Expose the mode to CSS. Only ENROL carries the extra name field, upload card
    // and explainer, so only enrol needs the layout compacted to stay on one
    // screen - verify keeps the full-size oval it always had.
    document.documentElement.dataset.mode = m;
    cancelRetry(); retryLeft = RETRY_MAX;
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
        deviceTipDismissed = true;          // user dismissed - don't nudge again this session
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
