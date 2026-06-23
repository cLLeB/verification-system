'use strict';
const $ = (id) => document.getElementById(id);
const ENROLL_TARGET = 3;
let facing = 'user', captured = 0, stream = null;

// --- auth ------------------------------------------------------------------
async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
    if (r.status === 401) { showLogin(); throw new Error('auth'); }
    return r.json();
}
function showLogin() { $('login').classList.remove('hidden'); $('console').classList.add('hidden'); }
function showConsole() {
    $('login').classList.add('hidden'); $('console').classList.remove('hidden');
    startCamera(); loadOverview(); loadPeople(); loadKeys(); loadTenants(); loadUsage(); loadOps();
    populateAuditTenants(); loadAudit();
}
async function checkSession() {
    const d = await (await fetch('/admin/session')).json();
    d.admin ? showConsole() : showLogin();
}
$('login-btn').onclick = async () => {
    const r = await fetch('/admin/login', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: $('user').value.trim(), password: $('pw').value }) });
    if (r.ok) { $('login-err').textContent = ''; $('pw').value = ''; showConsole(); }
    else $('login-err').textContent = 'Incorrect username or password.';
};
$('logout-btn').onclick = async () => {
    await fetch('/admin/logout', { method: 'POST' });
    if (stream) stream.getTracks().forEach(t => t.stop());
    showLogin();
};

// --- tabs ------------------------------------------------------------------
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('is-active'));
    t.classList.add('is-active');
    document.querySelectorAll('.panel').forEach(p => p.classList.add('hidden'));
    $('tab-' + t.dataset.tab).classList.remove('hidden');
});

// --- overview --------------------------------------------------------------
async function loadOverview() {
    const d = await api('/admin/api/overview');
    const cards = [
        ['people', 'People enrolled'], ['checks_this_month', 'Checks this month'],
        ['api_keys', 'API keys'], ['tenants', 'Customer tenants'], ['operators', 'Operators'],
    ];
    $('stats').innerHTML = cards.map(([k, label]) =>
        `<div class="stat"><div class="n">${d[k] ?? '—'}</div><div class="l">${label}</div></div>`).join('') +
        `<div class="stat"><div class="n">${d.encrypted ? '🔒' : '⚠'}</div><div class="l">${d.encrypted ? 'Encrypted' : 'Not encrypted'}</div></div>`;
}

// --- enrol -----------------------------------------------------------------
async function startCamera() {
    try {
        if (stream) stream.getTracks().forEach(t => t.stop());
        stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: facing } }, audio: false });
        $('video').srcObject = stream;
        $('video').style.transform = facing === 'user' ? 'scaleX(-1)' : 'none';
    } catch (e) { $('enroll-msg').textContent = 'Camera unavailable — allow access.'; }
}
$('swap').onclick = () => { facing = facing === 'user' ? 'environment' : 'user'; startCamera(); };
function renderDots() {
    $('dots').innerHTML = '';
    for (let i = 0; i < ENROLL_TARGET; i++) {
        const d = document.createElement('span');
        d.className = 'dot' + (i < captured ? ' on' : ''); $('dots').appendChild(d);
    }
}
function grab() {
    const v = $('video'), c = $('canvas');
    if (!v.videoWidth) return null;
    const w = Math.min(640, v.videoWidth), h = Math.round(w * v.videoHeight / v.videoWidth);
    c.width = w; c.height = h; c.getContext('2d').drawImage(v, 0, 0, w, h);
    return c.toDataURL('image/jpeg', 0.9);
}
$('capture').onclick = async () => {
    const id = $('enroll-id').value.trim();
    if (!id) { $('enroll-msg').textContent = 'Enter a name or ID first.'; return; }
    const img = grab();
    if (!img) { $('enroll-msg').textContent = 'Camera not ready.'; return; }
    $('enroll-msg').textContent = 'Checking…';
    const d = await api('/api/enroll', { method: 'POST', body: JSON.stringify({ user_id: id, image: img }) });
    $('enroll-msg').textContent = d.message || '';
    if (d.success) {
        captured = d.samples || captured + 1; renderDots();
        if (captured >= ENROLL_TARGET) {
            $('enroll-msg').textContent = `✓ ${id} enrolled.`;
            captured = 0; $('enroll-id').value = ''; renderDots(); loadPeople();
        }
    }
};

function fileToDataUrl(file) {
    return new Promise((res, rej) => {
        const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej;
        r.readAsDataURL(file);
    });
}
$('upload-enroll').onclick = async () => {
    const id = $('enroll-id').value.trim();
    if (!id) { $('enroll-msg').textContent = 'Enter a name or ID first.'; return; }
    const files = Array.from($('enroll-files').files || []);
    if (!files.length) { $('enroll-msg').textContent = 'Choose one or more photos.'; return; }
    let ok = 0;
    for (let i = 0; i < files.length; i++) {
        $('enroll-msg').textContent = `Enrolling photo ${i + 1}/${files.length}…`;
        const img = await fileToDataUrl(files[i]);
        const d = await api('/api/enroll', { method: 'POST', body: JSON.stringify({ user_id: id, image: img }) });
        if (d.success) ok++;
    }
    $('enroll-msg').textContent = `Enrolled ${ok}/${files.length} photo(s) for ${id}.`;
    $('enroll-files').value = ''; loadPeople(); loadOverview();
};

// --- people ----------------------------------------------------------------
let people = [];
async function loadPeople() {
    const d = await api('/api/users'); people = d.users || [];
    $('people-count').textContent = people.length; renderPeople();
}
function renderPeople() {
    const q = $('people-search').value.toLowerCase();
    const list = $('people-list'); list.innerHTML = '';
    people.filter(u => u.toLowerCase().includes(q)).forEach(u => {
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<span class="grow">${u}</span>`;
        const b = document.createElement('button'); b.className = 'del'; b.textContent = 'Delete';
        b.onclick = async () => {
            if (!confirm(`Delete ${u}?`)) return;
            await api('/api/users/delete', { method: 'POST', body: JSON.stringify({ user_id: u }) });
            loadPeople();
        };
        row.appendChild(b); list.appendChild(row);
    });
}
$('people-search').oninput = renderPeople;
$('people-csv').onclick = () => {
    const csv = 'user_id\n' + people.map(u => `"${u.replace(/"/g, '""')}"`).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = 'people.csv'; a.click(); URL.revokeObjectURL(a.href);
};

// --- keys ------------------------------------------------------------------
async function loadKeys() {
    const d = await api('/admin/api/keys');
    const list = $('keys-list'); list.innerHTML = '';
    const byTenant = {};
    (d.keys || []).forEach(k => { (byTenant[k.tenant] = byTenant[k.tenant] || []).push(k); });
    Object.keys(byTenant).sort().forEach(tenant => {
        const grp = byTenant[tenant];
        const adminN = grp.filter(k => k.role === 'admin').length;
        const head = document.createElement('div'); head.className = 'group-head';
        head.innerHTML = `Tenant: <b>${tenant}</b> <span class="pill">${grp.length} key(s) · ${adminN} admin</span>`;
        list.appendChild(head);
        grp.forEach(k => {
            const row = document.createElement('div'); row.className = 'item';
            const used = k.last_used ? new Date(k.last_used * 1000).toLocaleDateString() : 'never';
            const exp = k.expires ? ` · expires ${new Date(k.expires * 1000).toLocaleDateString()}` : '';
            row.innerHTML = `<div class="grow"><div>${k.name} <span class="pill">${k.role}</span></div>
                <div class="sub">${k.key_id} · used: ${used}${exp}</div></div>`;
            const b = document.createElement('button'); b.className = 'del'; b.textContent = 'Revoke';
            b.onclick = async () => {
                if (!confirm(`Revoke key ${k.key_id} (${k.name})?`)) return;
                await api('/admin/api/keys/revoke', { method: 'POST', body: JSON.stringify({ key_id: k.key_id }) });
                loadKeys();
            };
            row.appendChild(b); list.appendChild(row);
        });
    });
}

function downloadFile(filename, text) {
    const blob = new Blob([text], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
}
function keysToCsv(keys) {
    const cols = ['tenant', 'key_id', 'name', 'role', 'api_key', 'signing_secret', 'expires'];
    const rows = [cols.join(',')];
    keys.forEach(k => rows.push(cols.map(c => `"${k[c] != null ? String(k[c]).replace(/"/g, '""') : ''}"`).join(',')));
    return rows.join('\n');
}
function renderNewKeys(box, d) {
    box.innerHTML = '';
    const h = document.createElement('div');
    h.innerHTML = `<b>Created ${d.count} key(s) for tenant <code>${d.tenant}</code> — shown only once. Download now.</b>`;
    box.appendChild(h);
    d.keys.forEach(k => {
        const div = document.createElement('div'); div.className = 'newkey-row';
        div.innerHTML = `<div><span class="pill">${k.role}</span> ${k.name}</div>
            <div class="mono">api_key: ${k.api_key}</div><div class="mono">signing_secret: ${k.signing_secret}</div>`;
        const dl = document.createElement('button'); dl.className = 'btn ghost'; dl.textContent = 'Download this key';
        dl.onclick = () => downloadFile(`${k.tenant}_${k.key_id}.json`, JSON.stringify(k, null, 2));
        div.appendChild(dl); box.appendChild(div);
    });
    const row = document.createElement('div'); row.className = 'row';
    const j = document.createElement('button'); j.className = 'btn primary'; j.textContent = 'Download all (JSON)';
    j.onclick = () => downloadFile(`${d.tenant}_keys.json`, JSON.stringify(d.keys, null, 2));
    const c = document.createElement('button'); c.className = 'btn ghost'; c.textContent = 'Download all (CSV)';
    c.onclick = () => downloadFile(`${d.tenant}_keys.csv`, keysToCsv(d.keys));
    row.appendChild(j); row.appendChild(c); box.appendChild(row);
}
$('key-create').onclick = async () => {
    const name = $('key-name').value.trim();
    const box = $('key-new'); box.classList.remove('hidden');
    if (!name) { box.textContent = 'A company / app name is required.'; return; }
    const admin = parseInt($('key-admin-n').value || '0', 10);
    const verify = parseInt($('key-verify-n').value || '0', 10);
    if (admin + verify < 1) { box.textContent = 'Choose at least one key to create.'; return; }
    const expires = parseInt($('key-expires').value || '0', 10);
    box.textContent = 'Creating…';
    const d = await api('/admin/api/keys/bulk', { method: 'POST', body: JSON.stringify({
        name, tenant: $('key-tenant').value.trim(), admin, verify,
        expires_in_days: expires > 0 ? expires : undefined }) });
    if (!d.success) { box.textContent = d.message || 'Failed to create keys.'; return; }
    renderNewKeys(box, d);
    $('key-name').value = ''; $('key-tenant').value = ''; loadKeys();
};

// --- tenant access (entitlements / paywall) --------------------------------
$('ent-save').onclick = async () => {
    const tenant = $('ent-tenant').value.trim();
    if (!tenant) { $('ent-msg').textContent = 'Tenant id required.'; return; }
    const roles = $('ent-roles').value.trim();
    const d = await api('/admin/api/tenants/entitlement', { method: 'POST', body: JSON.stringify({
        tenant, enabled: $('ent-enabled').checked, plan: $('ent-plan').value.trim() || undefined,
        max_keys: parseInt($('ent-maxkeys').value || '0', 10), allowed_roles: roles || undefined }) });
    $('ent-msg').textContent = d.success
        ? `Saved: ${tenant} · ${d.enabled ? 'enabled' : 'DISABLED'} · plan ${d.plan} · max ${d.max_keys} · roles ${d.allowed_roles.join('/')}`
        : (d.message || 'Failed');
};
$('ent-portal-set').onclick = async () => {
    const tenant = $('ent-tenant').value.trim();
    const pw = $('ent-portal-pw').value;
    if (!tenant) { $('ent-msg').textContent = 'Tenant id required.'; return; }
    if (!pw || pw.length < 6) { $('ent-msg').textContent = 'Portal password must be ≥6 chars.'; return; }
    const d = await api('/admin/api/tenants/portal-password', { method: 'POST',
        body: JSON.stringify({ tenant, password: pw }) });
    $('ent-portal-pw').value = '';
    $('ent-msg').textContent = d.success
        ? `Portal login set for ${tenant}. They sign in at ${location.origin}/portal with tenant id "${tenant}".`
        : (d.message || 'Failed');
};
$('ent-offboard').onclick = async () => {
    const tenant = $('ent-tenant').value.trim();
    if (!tenant) { $('ent-msg').textContent = 'Tenant id required.'; return; }
    if (!confirm(`Offboard '${tenant}'? This REVOKES its keys and PERMANENTLY ERASES its enrolled data.`)) return;
    if (!confirm(`Final check — erase ALL data for '${tenant}'? This cannot be undone.`)) return;
    const d = await api('/admin/api/tenants/offboard', { method: 'POST', body: JSON.stringify({ tenant }) });
    $('ent-msg').textContent = d.success
        ? `Offboarded ${tenant}: revoked ${d.keys_revoked} key(s), data erased = ${d.store_erased}.`
        : (d.message || 'Failed');
    loadKeys();
};

// --- tenant settings (CORS + webhooks) -------------------------------------
async function loadTenants() {
    const d = await api('/admin/api/tenants');
    const list = $('ts-list'); list.innerHTML = '';
    (d.tenants || []).forEach(t => {
        const wh = t.webhook_url ? `webhook: ${t.webhook_url}` : 'no webhook';
        const sec = t.webhook_secret ? ` · secret: ${t.webhook_secret}` : '';
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<div class="grow"><div><b>${t.tenant}</b></div>
            <div class="sub">origins: ${(t.cors_origins || []).join(', ') || '—'}</div>
            <div class="sub">${wh}${sec}</div></div>`;
        list.appendChild(row);
    });
}
$('ts-save').onclick = async () => {
    const tenant = $('ts-tenant').value.trim();
    if (!tenant) return;
    await api('/admin/api/tenants', { method: 'POST', body: JSON.stringify({
        tenant, cors_origins: $('ts-origins').value, webhook_url: $('ts-webhook').value.trim() }) });
    $('ts-tenant').value = ''; $('ts-origins').value = ''; $('ts-webhook').value = ''; loadTenants();
};

// --- usage -----------------------------------------------------------------
async function loadUsage() {
    const d = await api('/admin/api/usage');
    const list = $('usage-list'); list.innerHTML = '';
    (d.usage || []).forEach(u => {
        const parts = Object.entries(u.counts).map(([k, v]) => `${k}:${v}`).join('  ') || '—';
        const cap = u.quota ? `${u.total}/${u.quota}` : `${u.total} (no cap)`;
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<div class="grow"><div><b>${u.tenant}</b> <span class="pill">${cap}</span></div>
            <div class="sub">${parts}</div></div>`;
        list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML = '<div class="muted">No usage yet.</div>';
}
$('quota-save').onclick = async () => {
    const tenant = $('quota-tenant').value.trim();
    if (!tenant) return;
    const v = $('quota-value').value.trim();
    await api('/admin/api/quota', { method: 'POST',
        body: JSON.stringify({ tenant, quota: v ? Number(v) : null }) });
    $('quota-tenant').value = ''; $('quota-value').value = ''; loadUsage();
};

// --- operators -------------------------------------------------------------
let currentAdmin = '';
async function loadOps() {
    const d = await api('/admin/api/admins');
    currentAdmin = d.current || '';
    const list = $('ops-list'); list.innerHTML = '';
    (d.admins || []).forEach(name => {
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<span class="grow">${name}${name === currentAdmin ? ' <span class="pill">you</span>' : ''}</span>`;
        if (name !== currentAdmin) {
            const b = document.createElement('button'); b.className = 'del'; b.textContent = 'Remove';
            b.onclick = async () => {
                if (!confirm(`Remove operator ${name}?`)) return;
                await api('/admin/api/admins/remove', { method: 'POST', body: JSON.stringify({ username: name }) });
                loadOps();
            };
            row.appendChild(b);
        }
        list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML = '<div class="muted">No named operators yet (bootstrap login active).</div>';
}
$('op-add').onclick = async () => {
    const u = $('op-user').value.trim(), p = $('op-pw').value;
    if (!u || !p) return;
    await api('/admin/api/admins', { method: 'POST', body: JSON.stringify({ username: u, password: p }) });
    $('op-user').value = ''; $('op-pw').value = ''; loadOps();
};

// --- audit -----------------------------------------------------------------
async function populateAuditTenants() {
    const d = await api('/admin/api/keys');
    const tenants = ['first_party', ...new Set((d.keys || []).map(k => k.tenant))];
    $('audit-tenant').innerHTML = tenants.map(t => `<option value="${t}">${t}</option>`).join('');
    $('audit-tenant').onchange = loadAudit;
}
async function loadAudit() {
    const tenant = $('audit-tenant').value || 'first_party';
    const d = await api('/admin/api/audit?tenant=' + encodeURIComponent(tenant));
    const list = $('audit-list'); list.innerHTML = '';
    (d.events || []).forEach(e => {
        const row = document.createElement('div'); row.className = 'item';
        const tag = e.success ? '<span class="tag-ok">ok</span>' : '<span class="tag-bad">no</span>';
        row.innerHTML = `<span class="grow">${e.iso} · <b>${e.action}</b> · ${e.user_id || '—'}
            · ${e.actor || ''} ${tag}</span>`;
        list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML = '<div class="muted">No events yet.</div>';
}

renderDots();
checkSession();
