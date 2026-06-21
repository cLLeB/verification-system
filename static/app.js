// ---------------------------------------------------------------------------
// Contactless Fingerprint — native-camera capture client.
//
// The reliable way to get ridges off a phone is the NATIVE camera (tap to
// focus, pinch to zoom, you SEE the ridges before shooting). So we open it via
// a file input, let the user confirm the framing in the circle, crop to that
// circle, and send the crop. No live-video focus fighting, no blind auto-crop.
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const modeVerify = $('mode-verify'), modeEnroll = $('mode-enroll'), segThumb = $('seg-thumb');
const enrollRow = $('enroll-row'), userId = $('user-id'), dots = $('dots');
const hint = $('hint'), bar = $('bar'), progressWrap = $('progress-wrap'), statusText = $('status-text');
const scannerEmpty = $('scanner-empty'), preview = $('preview');
const actions = $('actions'), confirmActions = $('confirm-actions');
const captureBtn = $('capture-btn'), retakeBtn = $('retake-btn'), useBtn = $('use-btn');
const fileInput = $('file-input'), canvas = $('canvas'), ctx = canvas.getContext('2d');
const result = $('result'), resultSvg = $('result-svg');
const resultTitle = $('result-title'), resultSub = $('result-sub'), againBtn = $('again');

const ICON_OK = '<path d="M20 6 9 17l-5-5"/>';
const ICON_BAD = '<path d="M18 6 6 18M6 6l12 12"/>';

const ENROLL_TARGET = 3;
const CROP_ASPECT = 3 / 4;     // must match the .scanner box aspect-ratio in app.css
const OUT_H = 600;             // exported crop height (px); width follows CROP_ASPECT

let mode = 'verify';
let objUrl = null;             // current preview object URL (revoke when replaced)

// --- capture: open the native camera ---------------------------------------
function openCamera() {
    if (mode === 'enroll' && !userId.value.trim()) {
        setHint('Enter a name or ID to enrol first'); userId.focus(); return;
    }
    fileInput.value = '';      // allow re-picking after a cancel
    fileInput.click();
}

fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    if (objUrl) URL.revokeObjectURL(objUrl);
    objUrl = URL.createObjectURL(file);
    preview.onload = () => {
        scannerEmpty.classList.add('hidden');
        preview.classList.add('show');
        actions.classList.add('hidden');
        confirmActions.classList.remove('hidden');
        result.classList.add('hidden');
        setHint('Sharp ridges, filling the circle? If not, retake.');
    };
    preview.src = objUrl;
});

// --- crop the previewed photo to exactly what the circle shows --------------
// The <img> uses object-fit: cover in a CROP_ASPECT box, so the visible region
// is the centre of the image at CROP_ASPECT. We reproduce that crop precisely.
function croppedDataURL() {
    const iw = preview.naturalWidth, ih = preview.naturalHeight;
    if (!iw || !ih) return null;
    let cw, ch;
    if (iw / ih > CROP_ASPECT) { ch = ih; cw = ih * CROP_ASPECT; }
    else { cw = iw; ch = iw / CROP_ASPECT; }
    const sx = (iw - cw) / 2, sy = (ih - ch) / 2;
    canvas.height = OUT_H; canvas.width = Math.round(OUT_H * CROP_ASPECT);
    ctx.drawImage(preview, sx, sy, cw, ch, 0, 0, canvas.width, canvas.height);
    // Lossless PNG — JPEG compression smears ridge detail and destabilises matching.
    return canvas.toDataURL('image/png');
}

async function submit() {
    const img = croppedDataURL();
    if (!img) { setHint('Could not read that photo — retake.'); return; }
    confirmActions.classList.add('hidden');
    statusText.textContent = 'Processing';
    progressWrap.classList.remove('hidden');
    let p = 20; setBar(20, 'Processing…');
    const anim = setInterval(() => { p = p < 80 ? p + 5 : Math.min(95, p + 2); bar.style.width = p + '%'; }, 160);

    const payload = { image: img };
    if (mode === 'enroll') payload.user_id = userId.value.trim();
    try {
        const res = await fetch(mode === 'enroll' ? '/api/enroll' : '/api/verify', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const data = await res.json();
        clearInterval(anim); bar.style.width = '100%';
        setTimeout(() => handle(data), 150);
    } catch (e) {
        clearInterval(anim); resetToIdle('Network error — is the server running?');
    }
}

// --- result handling --------------------------------------------------------
function handle(data) {
    statusText.textContent = 'Ready';
    progressWrap.classList.add('hidden');
    if (data.code === 'low_quality' || data.code === 'liveness') {
        resetToIdle(data.message || 'Capture not usable — retake with more light, filling the circle.');
        return;
    }
    if (mode === 'enroll') {
        const n = data.samples || 0;
        renderDots(n);
        if (data.success && n < ENROLL_TARGET) { resetToIdle(`Captured ${n}/${ENROLL_TARGET} — scan the SAME finger again`); return; }
        if (data.success) { show('ok', ICON_OK, 'Enrolled', `${userId.value.trim()} is ready to verify`); userId.value = ''; renderDots(0); return; }
        if (data.code === 'inconsistent') { resetToIdle(data.message); return; }
        show('bad', ICON_BAD, 'Enrolment failed', data.message || '');
        return;
    }
    if (data.success) show('ok', ICON_OK, 'Access granted', data.user_id ? `Welcome, ${data.user_id}` : '');
    else show('bad', ICON_BAD, 'Access denied', 'Fingerprint not recognised');
}

function show(kind, icon, title, sub) {
    result.className = 'result ' + kind;
    resultSvg.innerHTML = icon; resultTitle.textContent = title; resultSub.textContent = sub || '';
    result.classList.remove('hidden');
}

// --- ui helpers -------------------------------------------------------------
function setBar(pct, text) { bar.style.width = Math.max(0, Math.min(100, pct)) + '%'; if (text !== undefined) setHint(text); }
function setHint(text) { hint.textContent = text; }

function resetToIdle(msg) {
    if (objUrl) { URL.revokeObjectURL(objUrl); objUrl = null; }
    preview.removeAttribute('src'); preview.classList.remove('show');
    scannerEmpty.classList.remove('hidden');
    result.classList.add('hidden');
    confirmActions.classList.add('hidden');
    actions.classList.remove('hidden');
    progressWrap.classList.add('hidden');
    setBar(0);
    setHint(msg || (mode === 'enroll'
        ? 'Fill the circle, then capture the SAME finger 3×'
        : 'Fill the circle with your fingertip pad, then capture'));
}

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
    renderDots(0);
    resetToIdle();
}

// --- wiring -----------------------------------------------------------------
captureBtn.addEventListener('click', openCamera);
retakeBtn.addEventListener('click', openCamera);
useBtn.addEventListener('click', submit);
againBtn.addEventListener('click', () => resetToIdle());
modeEnroll.addEventListener('click', () => setMode('enroll'));
modeVerify.addEventListener('click', () => setMode('verify'));

setMode('verify');
