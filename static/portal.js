// Tenant self-service portal: sign in, see your plan/limits, mint/revoke your OWN keys.
const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
    return r.json();
}

function show(view) {
    $('login').classList.toggle('hidden', view !== 'login');
    $('console').classList.toggle('hidden', view !== 'console');
}

async function refreshSession() {
    const d = await api('/portal/session');
    if (d.authenticated) {
        $('who').textContent = `tenant: ${d.tenant}`;
        show('console');
        renderEntitlement(d.entitlement);
        loadKeys();
    } else {
        show('login');
    }
}

$('login-btn').onclick = async () => {
    const d = await api('/portal/login', { method: 'POST', body: JSON.stringify({
        tenant: $('tenant').value.trim(), password: $('pw').value }) });
    if (d.success) { $('pw').value = ''; refreshSession(); }
    else $('login-err').textContent = d.message || 'Sign in failed.';
};
$('logout-btn').onclick = async () => { await api('/portal/logout', { method: 'POST' }); show('login'); };

function renderEntitlement(e) {
    const remaining = e.max_keys ? `${e.remaining} of ${e.max_keys} left` : 'unlimited';
    $('ent-stats').innerHTML = `
        <div class="stat"><div class="n">${e.enabled ? 'Active' : 'Disabled'}</div><div class="l">status</div></div>
        <div class="stat"><div class="n">${e.plan}</div><div class="l">plan</div></div>
        <div class="stat"><div class="n">${e.used}</div><div class="l">keys in use</div></div>
        <div class="stat"><div class="n">${e.max_keys || '∞'}</div><div class="l">max keys</div></div>`;
    $('disabled-note').classList.toggle('hidden', e.enabled);
    // limit the create form to what the plan allows
    $('key-admin-n').disabled = !e.allowed_roles.includes('admin') || !e.enabled;
    $('key-verify-n').disabled = !e.allowed_roles.includes('verify') || !e.enabled;
    $('key-create').disabled = !e.enabled;
    $('key-name').dataset.remaining = e.max_keys ? e.remaining : '';
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
    h.innerHTML = `<b>Created ${d.count} key(s) — shown only once. Download now.</b>`;
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
    const box = $('key-new'); box.classList.remove('hidden');
    const admin = parseInt($('key-admin-n').value || '0', 10);
    const verify = parseInt($('key-verify-n').value || '0', 10);
    if (admin + verify < 1) { box.textContent = 'Choose at least one key to create.'; return; }
    box.textContent = 'Creating…';
    const d = await api('/portal/api/keys/bulk', { method: 'POST', body: JSON.stringify({
        name: $('key-name').value.trim(), admin, verify }) });
    if (!d.success) { box.textContent = d.message || 'Failed to create keys.'; return; }
    renderNewKeys(box, d);
    $('key-name').value = ''; refreshSession();
};

async function loadKeys() {
    const d = await api('/portal/api/keys');
    const list = $('keys-list'); list.innerHTML = '';
    if (!(d.keys || []).length) { list.innerHTML = '<p class="muted">No keys yet.</p>'; return; }
    d.keys.forEach(k => {
        const row = document.createElement('div'); row.className = 'item';
        const used = k.last_used ? new Date(k.last_used * 1000).toLocaleDateString() : 'never';
        const exp = k.expires ? ` · expires ${new Date(k.expires * 1000).toLocaleDateString()}` : '';
        row.innerHTML = `<div class="grow"><div>${k.name} <span class="pill">${k.role}</span></div>
            <div class="sub">${k.key_id} · used: ${used}${exp}</div></div>`;
        const b = document.createElement('button'); b.className = 'del'; b.textContent = 'Revoke';
        b.onclick = async () => {
            if (!confirm(`Revoke key ${k.key_id} (${k.name})? Apps using it stop working immediately.`)) return;
            await api('/portal/api/keys/revoke', { method: 'POST', body: JSON.stringify({ key_id: k.key_id }) });
            refreshSession();
        };
        row.appendChild(b); list.appendChild(row);
    });
}

refreshSession();
