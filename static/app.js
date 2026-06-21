// ---------------------------------------------------------------------------
// Face Verify — front-camera client (tap to capture).
// Live selfie preview; you center your face and tap the button. The frame is
// sent, the server detects the face, (optionally) checks liveness, and matches.
// No motion guessing — capture happens exactly when you tap.
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const video = $('video'), canvas = $('canvas'), ctx = canvas.getContext('2d');
const scanner = document.querySelector('.scanner');
const modeVerify = $('mode-verify'), modeEnroll = $('mode-enroll'), segThumb = $('seg-thumb');
const enrollRow = $('enroll-row'), userId = $('user-id'), dots = $('dots');
const hint = $('hint'), bar = $('bar'), progressWrap = $('progress-wrap'), statusText = $('status-text');
const actions = $('actions'), captureBtn = $('capture-btn');
const result = $('result'), resultSvg = $('result-svg');
const resultTitle = $('result-title'), resultSub = $('result-sub'), againBtn = $('again');

const ICON_OK = '<path d="M20 6 9 17l-5-5"/>';
const ICON_BAD = '<path d="M18 6 6 18M6 6l12 12"/>';

const ENROLL_TARGET = 3;
const OUT_W = 720;
let mode = 'verify', busy = false;

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
    ctx.drawImage(video, 0, 0, w, h);            // true (un-mirrored) frame for matching
    return canvas.toDataURL('image/jpeg', 0.92);
}

async function capture() {
    if (busy) return;
    if (mode === 'enroll' && !userId.value.trim()) { setHint('Enter a name or ID to enrol first'); userId.focus(); return; }
    const img = grabFrame();
    if (!img) { setHint('Camera not ready yet — try again in a second.'); return; }

    busy = true; captureBtn.disabled = true; scanner.classList.add('busy');
    statusText.textContent = 'Checking';
    progressWrap.classList.remove('hidden');
    let p = 25; bar.style.width = '25%'; setHint('Checking…');
    const anim = setInterval(() => { p = Math.min(95, p + 6); bar.style.width = p + '%'; }, 140);

    const payload = { image: img };
    if (mode === 'enroll') payload.user_id = userId.value.trim();
    try {
        const res = await fetch(mode === 'enroll' ? '/api/enroll' : '/api/verify', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
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
    return mode === 'enroll' ? 'Center your face, then tap Capture (3 times)' : 'Center your face in the circle, then tap Verify';
}

againBtn.addEventListener('click', () => { result.classList.add('hidden'); reset(); });
captureBtn.addEventListener('click', capture);

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
