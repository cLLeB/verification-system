// ---------------------------------------------------------------------------
// Face Verify — front-camera client.
// Live selfie preview; when the scene is held steady it auto-captures one frame
// and sends it. The server detects the face, (optionally) checks liveness, and
// matches. A short cooldown after each attempt prevents rapid re-fire.
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const video = $('video'), canvas = $('canvas'), ctx = canvas.getContext('2d');
const scanner = document.querySelector('.scanner');
const modeVerify = $('mode-verify'), modeEnroll = $('mode-enroll'), segThumb = $('seg-thumb');
const enrollRow = $('enroll-row'), userId = $('user-id'), dots = $('dots');
const hint = $('hint'), bar = $('bar'), statusText = $('status-text');
const result = $('result'), resultSvg = $('result-svg');
const resultTitle = $('result-title'), resultSub = $('result-sub'), againBtn = $('again');

const ICON_OK = '<path d="M20 6 9 17l-5-5"/>';
const ICON_BAD = '<path d="M18 6 6 18M6 6l12 12"/>';

const ENROLL_TARGET = 3;
const OUT_W = 720;                 // sent frame width
const STABLE_NEEDED = 7;           // ~0.85s of stillness before capture
const MOVE_SETTLE = 3.0;           // smoothed motion below this = "still"
const COOLDOWN_MS = 1400;          // pause after each attempt (prevents thrash)

let mode = 'verify', busy = false;
const A = 48;
const aCanvas = document.createElement('canvas'); aCanvas.width = A; aCanvas.height = A;
const actx = aCanvas.getContext('2d', { willReadFrequently: true });
let prev = null, ema = 0, stable = 0, cooldownUntil = 0;

async function initCamera() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'user' }, width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false,
        });
        video.srcObject = stream;
        statusText.textContent = 'Ready';
        setInterval(analyze, 120);
    } catch (err) {
        statusText.textContent = 'No camera';
        setHint('Camera unavailable — allow camera access and reload.');
    }
}

function motion() {
    actx.drawImage(video, 0, 0, A, A);
    const d = actx.getImageData(0, 0, A, A).data;
    const g = new Float32Array(A * A);
    for (let i = 0, p = 0; i < d.length; i += 4, p++) g[p] = 0.299*d[i] + 0.587*d[i+1] + 0.114*d[i+2];
    let diff = 0;
    if (prev) { for (let i = 0; i < g.length; i++) diff += Math.abs(g[i] - prev[i]); diff /= g.length; }
    prev = g;
    return diff;
}

function analyze() {
    if (busy || !video.videoWidth) return;
    if (mode === 'enroll' && !userId.value.trim()) { stable = 0; setBar(0, 'Enter a name or ID to enrol'); return; }
    if (performance.now() < cooldownUntil) return;            // brief settle pause

    ema = ema * 0.6 + motion() * 0.4;                          // smoothed motion
    if (ema < MOVE_SETTLE) stable++; else stable = Math.max(0, stable - 1);

    const pct = Math.min(100, Math.round((stable / STABLE_NEEDED) * 100));
    if (stable > 0) setBar(pct, 'Hold still…');
    else setBar(0, mode === 'enroll' ? 'Position your face in the circle' : 'Center your face in the circle');

    if (stable >= STABLE_NEEDED) capture();
}

function setBar(pct, text) { bar.style.width = Math.max(0, Math.min(100, pct)) + '%'; if (text !== undefined) setHint(text); }
function setHint(t) { hint.textContent = t; }

function grabFrame() {
    const vw = video.videoWidth, vh = video.videoHeight;
    const w = Math.min(OUT_W, vw), h = Math.round(w * vh / vw);
    canvas.width = w; canvas.height = h;
    ctx.drawImage(video, 0, 0, w, h);            // true (un-mirrored) frame
    return canvas.toDataURL('image/jpeg', 0.92);
}

async function capture() {
    busy = true; stable = 0; scanner.classList.add('busy');
    statusText.textContent = 'Checking';
    let p = 30; setBar(30, 'Checking…');
    const anim = setInterval(() => { p = Math.min(95, p + 5); bar.style.width = p + '%'; }, 140);

    const payload = { image: grabFrame() };
    if (mode === 'enroll') payload.user_id = userId.value.trim();
    try {
        const res = await fetch(mode === 'enroll' ? '/api/enroll' : '/api/verify', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await res.json();
        clearInterval(anim); bar.style.width = '100%';
        setTimeout(() => handle(data), 150);
    } catch (e) { clearInterval(anim); resume('Network error — is the server running?'); }
}

function handle(data) {
    statusText.textContent = 'Ready'; scanner.classList.remove('busy');
    if (['liveness', 'low_quality', 'multiple_faces'].includes(data.code)) { resume(data.message); return; }

    if (mode === 'enroll') {
        const n = data.samples || 0;
        renderDots(n);
        if (data.success && n < ENROLL_TARGET) { resume(`Captured ${n}/${ENROLL_TARGET} — hold still for the next`); return; }
        if (data.success) { show('ok', ICON_OK, 'Enrolled', `${userId.value.trim()} is ready to verify`); userId.value = ''; renderDots(0); return; }
        if (data.code === 'inconsistent' || data.code === 'duplicate') { resume(data.message); return; }
        show('bad', ICON_BAD, 'Enrolment failed', data.message || ''); return;
    }
    if (data.success) show('ok', ICON_OK, 'Access granted', data.user_id ? `Welcome, ${data.user_id}` : '');
    else show('bad', ICON_BAD, 'Access denied', 'Face not recognised');
}

function show(kind, icon, title, sub) {
    result.className = 'result ' + kind;
    resultSvg.innerHTML = icon; resultTitle.textContent = title; resultSub.textContent = sub || '';
    result.classList.remove('hidden');
}
// Pause detection briefly so a failed attempt can't instantly re-fire.
function resume(msg) { busy = false; stable = 0; ema = 0; prev = null; cooldownUntil = performance.now() + COOLDOWN_MS; scanner.classList.remove('busy'); setBar(0, msg); }

againBtn.addEventListener('click', () => { result.classList.add('hidden'); resume(mode === 'enroll' ? 'Position your face in the circle' : 'Center your face in the circle'); });

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
    resume(enr ? 'Position your face in the circle' : 'Center your face in the circle');
}
modeEnroll.addEventListener('click', () => setMode('enroll'));
modeVerify.addEventListener('click', () => setMode('verify'));

setMode('verify');
initCamera();
