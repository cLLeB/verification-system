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
const captureBtn = $('capture-btn');
const result = $('result'), resultSvg = $('result-svg');
const resultTitle = $('result-title'), resultSub = $('result-sub'), againBtn = $('again');

const ICON_OK = '<path d="M20 6 9 17l-5-5"/>';
const ICON_BAD = '<path d="M18 6 6 18M6 6l12 12"/>';

const ENROLL_TARGET = 3;
const OUT_W = 720;
const BURST_FRAMES = 7, BURST_GAP_MS = 280;    // ~2s head-turn recording
let mode = 'verify', busy = false;
const wait = (ms) => new Promise(r => setTimeout(r, ms));

async function initCamera() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'user' }, width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false,
        });
        video.srcObject = stream;
        statusText.textContent = 'Ready';
    } catch (err) {
        statusText.textContent = 'No camera';
        setHint('Camera unavailable — allow camera access and reload.');
        captureBtn.disabled = true;
    }
}

function setHint(t) { hint.textContent = t; }
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

async function enrollCapture() {
    if (!userId.value.trim()) { setHint('Enter a name or ID to enrol first'); userId.focus(); return; }
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
    } catch (e) { clearInterval(anim); reset('Network error — is the server running?'); }
}

async function verify() {
    const img0 = grabFrame();
    if (!img0) { setHint('Camera not ready — try again.'); return; }
    startBusy('Liveness');
    let ch;
    try { ch = await (await fetch('/api/challenge')).json(); }
    catch (e) { reset('Network error — is the server running?'); return; }

    if (!ch || !ch.active) {                       // active liveness off -> single shot
        return singleVerify(img0);
    }
    // Record a burst while the user performs the head turn.
    setHint(ch.instruction || 'Slowly turn your head left and right');
    const frames = [];
    for (let i = 0; i < BURST_FRAMES; i++) {
        const f = grabFrame(); if (f) frames.push(f);
        bar.style.width = Math.round(((i + 1) / BURST_FRAMES) * 100) + '%';
        await wait(BURST_GAP_MS);
    }
    statusText.textContent = 'Checking'; setHint('Checking…');
    try {
        const res = await fetch('/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frames, token: ch.token }) });
        const data = await res.json();
        setTimeout(() => handle(data), 120);
    } catch (e) { reset('Network error — is the server running?'); }
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
    } catch (e) { clearInterval(anim); reset('Network error — is the server running?'); }
}

function handle(data) {
    statusText.textContent = 'Ready'; scanner.classList.remove('busy');
    progressWrap.classList.add('hidden'); bar.style.width = '0%';
    if (['liveness', 'low_quality', 'multiple_faces'].includes(data.code)) { reset(data.message); return; }

    if (mode === 'enroll') {
        const n = data.samples || 0;
        renderDots(n);
        if (data.success && n < ENROLL_TARGET) { reset(`Captured ${n}/${ENROLL_TARGET} — tap Capture again`); return; }
        if (data.success) { show('ok', ICON_OK, 'Enrolled', `${userId.value.trim()} is ready to verify`); userId.value = ''; renderDots(0); return; }
        if (data.code === 'inconsistent' || data.code === 'duplicate') { reset(data.message); return; }
        show('bad', ICON_BAD, 'Enrolment failed', data.message || ''); return;
    }
    if (data.success) show('ok', ICON_OK, 'Access granted', data.user_id ? `Welcome, ${data.user_id}` : '');
    else show('bad', ICON_BAD, 'Access denied', 'Face not recognised');
}

function show(kind, icon, title, sub) {
    busy = false; captureBtn.disabled = false;
    result.className = 'result ' + kind;
    resultSvg.innerHTML = icon; resultTitle.textContent = title; resultSub.textContent = sub || '';
    result.classList.remove('hidden');
}
function reset(msg) {
    busy = false; captureBtn.disabled = false; scanner.classList.remove('busy');
    progressWrap.classList.add('hidden'); bar.style.width = '0%';
    setHint(msg || defaultHint());
}
function defaultHint() {
    return mode === 'enroll' ? 'Center your face, then tap Capture (3 times)'
                             : 'Center your face, tap Verify, then turn your head';
}

againBtn.addEventListener('click', () => { result.classList.add('hidden'); reset(); });
captureBtn.addEventListener('click', onCapture);

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

setMode('verify');
initCamera();
