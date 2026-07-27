// ---------------------------------------------------------------------------
// Self-enrolment — opened from an invite link (/enroll?token=...).
//   * The person's identity is FIXED by the token (pre-assigned by an admin) and
//     shown read-only; there is no name field to type.
//   * The link is scoped to one or both modalities (face / palm). Only the allowed
//     ones are shown, and a combined shot can only ever bind an allowed modality.
//   * If the link ADDS a modality to someone who already exists, a step-up is
//     required first: the enrollee proves an EXISTING modality (they really are the
//     pre-assigned person) before the new one can be bound. This closes the
//     "bind a stranger's palm to a real face account" hijack.
//   * Progress is saved per capture (resumable); tap Finish to consume the link.
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const video = $('video'), canvas = $('canvas'), ctx = canvas.getContext('2d');
const captureBtn = $('capture-btn'), finishBtn = $('finish-btn');
const OUT_W = 720;

let facing = 'user';                 // 'user' = front (face), 'environment' = rear (palm)
let busy = false;
let enrolled = [];                   // modalities completed so far (face / palm)
let allowed = ['face', 'palm'];      // modalities this link may bind
let stepUp = { required: false, satisfied: true, modality: null };
let liveness = false;                // server: self_enroll_liveness && active_liveness

const DEAD_CODES = ['used', 'expired', 'revoked', 'invalid'];
// Guided head-turn — see static/app.js for the reasoning. The old values here were
// worse still: 12 frames 130 ms apart is 1.5 SECONDS for a three-part instruction,
// with each prompt shown only after its frame was already taken.
const TURN_PHASES = [
    { hint: '⟵  Slowly turn your head LEFT', frames: 4 },
    { hint: 'Now turn your head RIGHT  ⟶', frames: 4 },
    { hint: 'Look straight at the camera', frames: 3 },
];
const PHASE_LEAD_MS = 350, BURST_GAP_MS = 250;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

function setHint(text, kind = '') {
    const h = $('hint');
    h.textContent = text;
    h.className = 'hint' + (kind ? ' ' + kind : '');
}

function setModeNote(text) {
    const n = $('mode-note');
    if (!text) { n.classList.add('hidden'); return; }
    n.textContent = text;
    n.classList.remove('hidden');
}

function fatal(message) {
    const v = video.srcObject;
    if (v) v.getTracks().forEach((t) => t.stop());
    $('capture-area').classList.add('hidden');
    $('done').classList.add('hidden');
    $('gate').classList.remove('hidden');
    $('gate-text').textContent = message || 'This enrolment link is not valid.';
}

// Only show chips for modalities this link may bind.
function applyScope() {
    $('chip-face').classList.toggle('hidden', !allowed.includes('face'));
    $('chip-palm').classList.toggle('hidden', !allowed.includes('palm'));
}

function renderChips() {
    $('chip-face').classList.toggle('on', enrolled.includes('face'));
    $('chip-palm').classList.toggle('on', enrolled.includes('palm'));
    finishBtn.disabled = enrolled.length === 0;
}

function inStepUp() {
    return stepUp.required && !stepUp.satisfied;
}

// Reflect the current phase (step-up vs enrol) in the banner + button label + hint.
function renderPhase() {
    if (inStepUp()) {
        setModeNote('Security check: this link adds a new biometric to an existing ' +
            "record. First confirm your identity with a biometric you've already " +
            'enrolled — your face (front camera) OR your hand (rear camera).');
        captureBtn.textContent = 'Confirm identity';
        setHint('Show your enrolled face or hand, then tap Confirm identity');
    } else {
        setModeNote('');
        captureBtn.textContent = 'Capture';
        const label = allowed.length === 1
            ? (allowed[0] === 'palm' ? 'your open hand' : 'your face')
            : 'your face — or your open hand';
        setHint(`Show ${label}, then tap Capture`);
    }
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
        try { await video.play(); } catch (e) {}                // iOS sometimes needs an explicit play
        keepVideoAlive(video);                                  // auto-resume if iOS pauses the preview
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
    setHint(facing === 'user' ? 'Front camera — best for face' : 'Back camera — best for hand');
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
        allowed = (data.modalities && data.modalities.length) ? data.modalities : ['face', 'palm'];
        stepUp = {
            required: !!data.requires_step_up,
            satisfied: !!data.step_up_satisfied,
            modality: data.step_up_modality || (allowed.includes('face') ? null : 'palm'),
        };
        liveness = !!data.self_enroll_liveness && !!data.active_liveness;
        // Informed consent: show the statement this enrolment records agreement to.
        if (data.consent_text) {
            $('consent-text').textContent = data.consent_text;
            $('consent-box').classList.remove('hidden');
        }
        applyScope();
        renderChips();
        renderPhase();
        await startCamera();
    } catch (err) {
        fatal('Could not reach the server. Check your connection and reload.');
    }
}

// We're capturing a face right now when the link allows face and the front camera
// is selected. Palm (rear camera) and palm-only links never do the head-turn.
function faceCapture() {
    return allowed.includes('face') && facing === 'user';
}

async function getChallengeToken() {
    try {
        const ch = await (await fetch('/api/challenge')).json();
        return (ch && ch.active) ? ch.token : null;
    } catch (e) { return null; }
}

// Record a short burst while guiding the user through a live head-turn.
async function captureBurst() {
    setHint('Keep your face in view…');
    await sleep(300);
    const frames = [];
    const totalFrames = TURN_PHASES.reduce((n, p) => n + p.frames, 0);
    let taken = 0;
    for (const phase of TURN_PHASES) {
        setHint(phase.hint, 'info');
        await sleep(PHASE_LEAD_MS);            // instruction first, THEN record
        for (let i = 0; i < phase.frames; i++) {
            const f = grabFrame();
            if (f) frames.push(f);
            taken++;
            await sleep(BURST_GAP_MS);
        }
    }
    return frames;
}

// A short burst of fresh stills (no head-turn) — the server keeps the sharpest, so
// one soft/ghosted frame never decides a capture. Used for palm and non-liveness face.
async function captureStillBurst(n = 5, gap = 110) {
    const frames = [];
    for (let i = 0; i < n; i++) {
        await ensureLiveVideo(video);             // never a stale/frozen frame
        const f = grabFrame();
        if (f) frames.push(f);
        if (i < n - 1) await sleep(gap);
    }
    return frames;
}

// Build the POST body for a capture: a liveness head-turn burst for a live face (when
// the server requires it), else a short still burst (sharpest frame wins server-side).
async function captureBody() {
    await ensureLiveVideo(video);                 // never capture a stale/frozen frame
    if (liveness && faceCapture()) {
        const token_challenge = await getChallengeToken();
        if (token_challenge) return { frames: await captureBurst(), token_challenge };
    }
    const frames = await captureStillBurst();
    return frames.length ? { frames } : null;
}

async function doStepUp() {
    const body = await captureBody();
    if (!body) { setHint('Camera not ready — try again.'); return; }
    setHint('Checking…');
    const res = await fetch('/api/invite/stepup', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: TOKEN, ...body }),
    });
    const data = await res.json();
    if (res.status === 410 || DEAD_CODES.includes(data.code)) { fatal(data.message); return; }
    if (data.success) {
        stepUp.satisfied = true;
        renderPhase();
        setHint('Identity confirmed ✓ — now add your new biometric', 'ok');
    } else {
        setHint(data.message || 'That did not match — try again.', 'warn');
    }
}

async function postEnroll(body, hand) {
    const payload = { token: TOKEN, ...body };
    if (hand) payload.hand = hand;
    const res = await fetch('/api/invite/enroll', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    return { res, data: await res.json() };
}

async function doEnroll() {
    const body = await captureBody();
    if (!body) { setHint('Camera not ready — try again.'); return; }
    setHint('Checking…');
    let { res, data } = await postEnroll(body);
    // A palm that matches neither hand you've enrolled: offer to add it as your other
    // hand (so either palm verifies you later).
    if (data.code === 'different_hand') {
        if (confirm(data.message || 'Add as your other hand?')) {
            setHint('Adding your other hand…');
            ({ res, data } = await postEnroll(body, 'other'));
        } else {
            setHint('Okay — present the SAME hand you enrolled first.', 'warn');
            return;
        }
    }
    if (res.status === 410 || DEAD_CODES.includes(data.code)) { fatal(data.message); return; }
    if (data.code === 'hands_full') {
        setHint(data.message || 'Both your hands are already enrolled.', 'warn');
        return;
    }
    if (data.code === 'same_hand_side') {          // that side is already enrolled
        setHint(data.message || 'That side is already enrolled — use your other hand.', 'warn');
        return;
    }
    if (data.code === 'step_up_required') {          // server insists on step-up first
        stepUp.required = true; stepUp.satisfied = false;
        stepUp.modality = data.step_up_modality || stepUp.modality;
        renderPhase();
        setHint('Confirm your existing biometric first.', 'warn');
        return;
    }
    if (data.success) {
        enrolled = data.enrolled || enrolled;
        renderChips();
        const what = data.modality === 'palm'
            ? (data.hand === 2 ? 'Other hand' : 'Print') : 'Face';
        const more = allowed.filter((m) => !enrolled.includes(m));
        const nextLabel = more.length ? (more[0] === 'palm' ? 'hand' : more[0]) : '';
        const palmHint = data.modality === 'palm' && data.hand === 1
            ? ' — you can also add your OTHER hand' : '';
        setHint(more.length
            ? `${what} captured ✓ — now your ${nextLabel}, or tap Finish`
            : `${what} captured ✓${palmHint} — tap Finish`, 'ok');
    } else {
        setHint(data.message || 'No usable biometric detected — try again.', 'warn');
    }
}

async function capture() {
    if (busy) return;
    busy = true; captureBtn.disabled = true; setHint('Checking…');
    try {
        if (inStepUp()) await doStepUp();
        else await doEnroll();
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
            if (data.credential && data.credential.card_url) {
                const a = $('done-card');
                a.href = data.credential.card_url;
                a.classList.remove('hidden');
            }
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
