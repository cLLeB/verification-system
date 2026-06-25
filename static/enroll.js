// ---------------------------------------------------------------------------
// Self-enrolment — opened from an invite link (/enroll?token=...).
//   * The person's identity is FIXED by the token (pre-assigned by an admin) and
//     shown read-only; there is no name field to type.
//   * Capture face, palm, or both (auto-detected server-side). Progress is saved
//     per capture, so a refresh or dropped network resumes where it left off.
//   * Tap Finish to consume the one-time link.
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const video = $('video'), canvas = $('canvas'), ctx = canvas.getContext('2d');
const captureBtn = $('capture-btn'), finishBtn = $('finish-btn');
const OUT_W = 720;

let facing = 'user';                 // 'user' = front (face), 'environment' = rear (palm)
let busy = false;
let enrolled = [];                   // modalities completed so far (face / palm)

const DEAD_CODES = ['used', 'expired', 'revoked', 'invalid'];

function setHint(text, kind = '') {
    const h = $('hint');
    h.textContent = text;
    h.className = 'hint' + (kind ? ' ' + kind : '');
}

function fatal(message) {
    const v = video.srcObject;
    if (v) v.getTracks().forEach((t) => t.stop());
    $('capture-area').classList.add('hidden');
    $('done').classList.add('hidden');
    $('gate').classList.remove('hidden');
    $('gate-text').textContent = message || 'This enrolment link is not valid.';
}

function renderChips() {
    $('chip-face').classList.toggle('on', enrolled.includes('face'));
    $('chip-palm').classList.toggle('on', enrolled.includes('palm'));
    finishBtn.disabled = enrolled.length === 0;
}

async function startCamera() {
    try {
        const old = video.srcObject;
        if (old) old.getTracks().forEach((t) => t.stop());
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 960 } },
            audio: false,
        });
        video.srcObject = stream;
        video.classList.toggle('mirror', facing === 'user');   // mirror the selfie view only
        captureBtn.disabled = false;
    } catch (err) {
        setHint('Camera unavailable — allow camera access and reload.', 'warn');
        captureBtn.disabled = true;
    }
}

function swapCamera() {
    if (busy) return;
    facing = facing === 'user' ? 'environment' : 'user';
    startCamera();
    setHint(facing === 'user' ? 'Front camera — best for face' : 'Back camera — best for palm');
}

function grabFrame() {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return null;
    const w = Math.min(OUT_W, vw), h = Math.round(w * vh / vw);
    canvas.width = w; canvas.height = h;
    ctx.drawImage(video, 0, 0, w, h);                          // true (un-mirrored) frame
    return canvas.toDataURL('image/jpeg', 0.9);
}

async function loadInvite() {
    if (!TOKEN) { fatal('This enrolment link is missing its token.'); return; }
    try {
        const res = await fetch('/api/invite?token=' + encodeURIComponent(TOKEN));
        const data = await res.json();
        if (!data.success) { fatal(data.message); return; }
        $('person-name').textContent = data.user_id;
        enrolled = data.enrolled || [];
        renderChips();
        await startCamera();
    } catch (err) {
        fatal('Could not reach the server. Check your connection and reload.');
    }
}

async function capture() {
    if (busy) return;
    const img = grabFrame();
    if (!img) { setHint('Camera not ready — try again.'); return; }
    busy = true; captureBtn.disabled = true; setHint('Checking…');
    try {
        const res = await fetch('/api/invite/enroll', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: TOKEN, image: img }),
        });
        const data = await res.json();
        if (res.status === 410 || DEAD_CODES.includes(data.code)) { fatal(data.message); return; }
        if (data.success) {
            enrolled = data.enrolled || enrolled;
            renderChips();
            const what = data.modality === 'palm' ? 'Palm' : 'Face';
            setHint(`${what} captured ✓ — capture the other, or tap Finish`, 'ok');
        } else {
            setHint(data.message || 'No face or palm detected — try again.', 'warn');
        }
    } catch (err) {
        setHint('Network error — try again.', 'warn');
    } finally {
        busy = false; captureBtn.disabled = false;
    }
}

async function finish() {
    if (busy || enrolled.length === 0) return;
    busy = true; finishBtn.disabled = true;
    try {
        const res = await fetch('/api/invite/finish', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: TOKEN }),
        });
        const data = await res.json();
        if (data.success) {
            const v = video.srcObject;
            if (v) v.getTracks().forEach((t) => t.stop());
            $('done-name').textContent = $('person-name').textContent;
            $('capture-area').classList.add('hidden');
            $('done').classList.remove('hidden');
        } else if (DEAD_CODES.includes(data.code)) {
            fatal(data.message);
        } else {
            setHint(data.message || 'Could not finish — try again.', 'warn');
            busy = false; finishBtn.disabled = false;
        }
    } catch (err) {
        setHint('Network error — try again.', 'warn');
        busy = false; finishBtn.disabled = false;
    }
}

captureBtn.addEventListener('click', capture);
finishBtn.addEventListener('click', finish);
$('swap-btn').addEventListener('click', swapCamera);
loadInvite();
