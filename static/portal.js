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
        loadIssuerKeys();
        loadProtection();
        loadCredentials();
        loadTrust();
        loadPortalPolicies();
        loadPortalGuests();
        loadPortalDevices();
        loadPortalGuardians();
        loadPortalConsent();
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

const policySave = $('policy-save');
if (policySave) policySave.onclick = async () => {
    const d = await api('/portal/api/match-policy', { method: 'POST',
        body: JSON.stringify({ match_policy: $('match-policy').value }) });
    if (d && d.success) policySave.textContent = `Saved (${d.match_policy})`;
};

function renderEntitlement(e) {
    const remaining = e.max_keys ? `${e.remaining} of ${e.max_keys} left` : 'unlimited';
    $('ent-stats').innerHTML = `
        <div class="stat"><div class="n">${e.enabled ? 'Active' : 'Disabled'}</div><div class="l">status</div></div>
        <div class="stat"><div class="n">${e.plan}</div><div class="l">plan</div></div>
        <div class="stat"><div class="n">${e.used}</div><div class="l">keys in use</div></div>
        <div class="stat"><div class="n">${e.max_keys || '∞'}</div><div class="l">max keys</div></div>
        <div class="stat"><div class="n">${e.palm_enabled ? 'On' : 'Off'}</div><div class="l">print</div></div>`;
    const policy = $('match-policy');
    if (policy && e.match_policy) policy.value = e.match_policy;
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

// --- issuer signing key (your organisation's signing identity) ---------------
async function loadIssuerKeys() {
    const d = await api('/portal/api/issuer-keys');
    const list = $('psec-keys');
    if (!list) return;
    list.innerHTML = '';
    (d.keys || []).forEach(k => {
        const row = document.createElement('div'); row.className = 'item';
        const retired = k.retired_at ? ` · retired ${new Date(k.retired_at * 1000).toLocaleDateString()}` : '';
        row.innerHTML = `<div class="grow"><div><code>${k.kid}</code> <span class="pill">${k.status}</span></div>
            <div class="sub">created ${new Date(k.created * 1000).toLocaleDateString()}${retired}</div></div>`;
        list.appendChild(row);
    });
}

const psecRotate = $('psec-rotate');
if (psecRotate) psecRotate.onclick = async () => {
    if (!confirm('Rotate your signing key?\n\nEverything already issued stays valid; new items are signed with the new key.')) return;
    const d = await api('/portal/api/issuer-keys/rotate', { method: 'POST', body: '{}' });
    if (d && !d.success) alert(d.message || 'Rotation failed.');
    loadIssuerKeys();
};

// --- template protection (revocable biometrics) ------------------------------
async function loadProtection() {
    const list = $('pprot-status');
    if (!list) return;
    const d = await api('/portal/api/protection');
    list.innerHTML = '';
    Object.entries(d.modalities || {}).forEach(([mod, s]) => {
        const row = document.createElement('div'); row.className = 'item';
        const last = s.last_reissue ? new Date(s.last_reissue * 1000).toLocaleString() : 'never';
        row.innerHTML = `<div class="grow"><div><b>${mod}</b>
            <span class="pill">${s.enabled ? 'protected' : 'not protected'}</span></div>
            <div class="sub">${s.users} enrolled · last reissue: ${last}</div></div>`;
        list.appendChild(row);
    });
}

const pprotReissue = $('pprot-reissue');
if (pprotReissue) pprotReissue.onclick = async () => {
    const user = ($('pprot-user').value || '').trim();
    if (user) {
        if (!confirm(`Reissue "${user}"?\n\nAny previously exported or stolen copy of their template stops matching immediately. They keep verifying — no re-enrolment.`)) return;
    } else {
        // organisation-wide is the big red button: require typing REISSUE
        const typed = prompt('Reissue ALL your templates?\n\nAny previously exported or stolen copy stops matching immediately. Your people keep verifying — nobody re-enrols.\n\nType REISSUE to confirm:');
        if (typed !== 'REISSUE') return;
    }
    const d = await api('/portal/api/protection/reissue', { method: 'POST',
        body: JSON.stringify(user ? { user_id: user } : {}) });
    $('pprot-msg').textContent = d.success
        ? `Reissued: ${Object.entries(d.reissued).map(([m, n]) => `${m} ${n}`).join(', ')}.`
        : (d.message || 'Reissue failed.');
    if (d.success) loadProtection();
};

// --- offline QR credentials ---------------------------------------------------
async function loadCredentials() {
    const list = $('pcred-list');
    if (!list) return;
    const d = await api('/portal/api/credentials');
    list.innerHTML = '';
    (d.credentials || []).forEach(c => {
        const row = document.createElement('div'); row.className = 'item';
        const exp = new Date(c.exp * 1000).toLocaleDateString();
        row.innerHTML = `<div class="grow"><div>${c.user_id}
            <span class="pill">${c.revoked ? 'REVOKED' : 'active'}</span></div>
            <div class="sub"><code>${c.cid.slice(0, 12)}…</code> · expires ${exp}${c.name ? ' · ' + c.name : ''}</div></div>`;
        if (!c.revoked) {
            const b = document.createElement('button'); b.className = 'del'; b.textContent = 'Revoke';
            b.onclick = async () => {
                if (!confirm(`Revoke this credential for ${c.user_id}?`)) return;
                await api('/portal/api/credentials/revoke', { method: 'POST',
                    body: JSON.stringify({ credential_id: c.cid }) });
                loadCredentials();
            };
            row.appendChild(b);
        }
        list.appendChild(row);
    });
}

const pcredIssue = $('pcred-issue');
if (pcredIssue) pcredIssue.onclick = async () => {
    const user = ($('pcred-user').value || '').trim();
    if (!user) { $('pcred-msg').textContent = 'Enter the user id to issue for.'; return; }
    const d = await api('/portal/api/credentials', { method: 'POST',
        body: JSON.stringify({ user_id: user,
            name: ($('pcred-name').value || '').trim() || undefined }) });
    const box = $('pcred-new');
    if (!d.success) {
        box.classList.add('hidden');
        $('pcred-msg').textContent = d.message || 'Issue failed.';
        return;
    }
    const card = `${location.origin}/card?d=${encodeURIComponent(d.payload_b45)}`;
    box.classList.remove('hidden');
    box.innerHTML = `<img src="data:image/png;base64,${d.qr_png_b64}" alt="Credential QR"
            style="max-width:240px;width:100%;image-rendering:pixelated;background:#fff;border-radius:8px">
        <div class="row" style="justify-content:center;margin-top:10px">
            <a class="btn ghost" download="credential-${user}.png"
               href="data:image/png;base64,${d.qr_png_b64}">Download PNG</a>
            <a class="btn ghost" href="${card}" target="_blank">Open card</a>
            <button class="btn ghost" id="pcred-copy">Copy card link</button>
        </div>`;
    $('pcred-copy').onclick = () => navigator.clipboard.writeText(card)
        .then(() => { $('pcred-msg').textContent = 'Card link copied — send it to the holder.'; });
    $('pcred-msg').textContent = `Issued ${d.credential_id.slice(0, 12)}…`;
    loadCredentials();
};
const pcredLoad = $('pcred-load');
if (pcredLoad) pcredLoad.onclick = loadCredentials;

// --- trusted organisations ------------------------------------------------------
async function loadTrust() {
    const list = $('ptrust-list');
    if (!list) return;
    const d = await api('/portal/api/trust');
    list.innerHTML = '';
    (d.trusted_issuers || []).forEach(issuer => {
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<div class="grow"><div>${issuer}</div>
            <div class="sub">their credentials verify here</div></div>`;
        const b = document.createElement('button'); b.className = 'del'; b.textContent = 'Untrust';
        b.onclick = async () => {
            await api('/portal/api/trust', { method: 'POST',
                body: JSON.stringify({ issuer, trusted: false }) });
            loadTrust();
        };
        row.appendChild(b);
        list.appendChild(row);
    });
    if (!(d.trusted_issuers || []).length) {
        list.innerHTML = '<div class="item"><div class="grow sub">Only your own credentials are accepted.</div></div>';
    }
}
const ptrustAdd = $('ptrust-add');
if (ptrustAdd) ptrustAdd.onclick = async () => {
    const issuer = ($('ptrust-issuer').value || '').trim();
    if (!issuer) return;
    const d = await api('/portal/api/trust', { method: 'POST',
        body: JSON.stringify({ issuer, trusted: true }) });
    if (d && !d.success) alert(d.message || 'Failed.');
    $('ptrust-issuer').value = '';
    loadTrust();
};

// --- access policies · guests · devices · guardians · consent ----------------
const pFmt = (ts) => ts ? new Date(ts * 1000).toLocaleString() : '—';

async function loadPortalPolicies() {
    const d = await api('/portal/api/policies');
    if (!d.success) return;
    $('ppol-mode').value = d.mode; $('ppol-default').value = d.default;
    $('ppol-tz').value = d.tz_offset_minutes || '';
    const rules = $('ppol-rules'); rules.innerHTML = '';
    (d.rules || []).forEach(r => {
        const row = document.createElement('div'); row.className = 'item';
        const when = (r.days || []).length ? r.days.join(',') : 'every day';
        const win = r.start ? ` ${r.start}–${r.end}` : '';
        row.innerHTML = `<div class="grow"><div>${r.name}
            <span class="pill">${r.effect}</span></div>
            <div class="sub">${(r.subjects || []).join(', ')} · ${when}${win}</div></div>`;
        const b = document.createElement('button');
        b.className = 'del'; b.textContent = 'Delete';
        b.onclick = async () => {
            await api('/portal/api/policies/rule', { method: 'POST',
                body: JSON.stringify({ delete: true, rule_id: r.rule_id }) });
            loadPortalPolicies();
        };
        row.appendChild(b);
        rules.appendChild(row);
    });
    if (!rules.children.length) rules.innerHTML = '<div class="muted">No rules yet.</div>';
    const groups = $('ppol-groups'); groups.innerHTML = '';
    Object.entries(d.groups || {}).forEach(([name, members]) => {
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<div class="grow"><div>group:${name}</div>
            <div class="sub">${members.join(', ') || '(empty)'}</div></div>`;
        const b = document.createElement('button');
        b.className = 'del'; b.textContent = 'Delete';
        b.onclick = async () => {
            await api('/portal/api/policies/group', { method: 'POST',
                body: JSON.stringify({ delete: true, name }) });
            loadPortalPolicies();
        };
        row.appendChild(b);
        groups.appendChild(row);
    });
}
$('ppol-save').onclick = async () => {
    const d = await api('/portal/api/policies', { method: 'POST',
        body: JSON.stringify({ mode: $('ppol-mode').value, default: $('ppol-default').value,
            tz_offset_minutes: parseInt($('ppol-tz').value, 10) || 0 }) });
    $('ppol-msg').textContent = d.success
        ? `Saved — mode ${d.mode}, default ${d.default}.` : (d.message || 'Failed.');
    loadPortalPolicies();
};
$('prule-add').onclick = async () => {
    const d = await api('/portal/api/policies/rule', { method: 'POST',
        body: JSON.stringify({ name: $('prule-name').value, effect: $('prule-effect').value,
            subjects: $('prule-subjects').value || '*', days: $('prule-days').value,
            start: $('prule-start').value || null, end: $('prule-end').value || null }) });
    $('ppol-msg').textContent = d.success ? 'Rule added.' : (d.message || 'Failed.');
    if (d.success) { $('prule-name').value = ''; loadPortalPolicies(); }
};
$('pgrp-save').onclick = async () => {
    const d = await api('/portal/api/policies/group', { method: 'POST',
        body: JSON.stringify({ name: $('pgrp-name').value, members: $('pgrp-members').value }) });
    $('ppol-msg').textContent = d.success ? 'Group saved.' : (d.message || 'Failed.');
    if (d.success) loadPortalPolicies();
};

async function loadPortalGuests() {
    const d = await api('/portal/api/guests');
    if (!d.success) return;
    const list = $('pgst-list'); list.innerHTML = '';
    (d.guests || []).forEach(gm => {
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<div class="grow"><div>${gm.user_id}
            <span class="pill">${gm.expired ? 'EXPIRED' : 'active'}</span></div>
            <div class="sub">expires ${pFmt(gm.expires)}</div></div>`;
        const b = document.createElement('button');
        b.className = 'del'; b.textContent = 'Make permanent';
        b.onclick = async () => {
            await api('/portal/api/guests', { method: 'POST',
                body: JSON.stringify({ user_id: gm.user_id, clear: true }) });
            loadPortalGuests();
        };
        row.appendChild(b);
        list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML = '<div class="muted">No guest passes.</div>';
}
$('pgst-set').onclick = async () => {
    const uid = ($('pgst-user').value || '').trim();
    if (!uid) { alert('Enter the user id first.'); return; }
    const d = await api('/portal/api/guests', { method: 'POST',
        body: JSON.stringify({ user_id: uid,
            expires_in_days: parseFloat($('pgst-days').value) || 0,
            expires_in_hours: parseFloat($('pgst-hours').value) || 0 }) });
    $('pgst-msg').textContent = d.success
        ? `Pass set — ${uid} expires ${pFmt(d.expires)}.` : (d.message || 'Failed.');
    if (d.success) { $('pgst-user').value = ''; loadPortalGuests(); }
};

async function loadPortalDevices() {
    const d = await api('/portal/api/devices');
    if (!d.success) return;
    const list = $('pdev-list'); list.innerHTML = '';
    (d.devices || []).forEach(dev => {
        const row = document.createElement('div'); row.className = 'item';
        const seen = dev.last_seen ? 'seen ' + pFmt(dev.last_seen) : 'never seen';
        row.innerHTML = `<div class="grow"><div>${dev.name}
            <span class="pill">${dev.disabled ? 'DISABLED' : 'active'}</span></div>
            <div class="sub">${dev.device_id} · ${seen}</div></div>`;
        if (!dev.disabled) {
            const b = document.createElement('button');
            b.className = 'del'; b.textContent = 'Disable';
            b.onclick = async () => {
                if (!confirm(`Disable '${dev.name}'? Its key is revoked immediately.`)) return;
                await api('/portal/api/devices/disable', { method: 'POST',
                    body: JSON.stringify({ device_id: dev.device_id }) });
                loadPortalDevices();
            };
            row.appendChild(b);
        }
        list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML = '<div class="muted">No devices paired.</div>';
}
$('pdev-pair').onclick = async () => {
    const name = ($('pdev-name').value || '').trim();
    if (!name) { alert('Name the device first.'); return; }
    const d = await api('/portal/api/devices/pairing', { method: 'POST',
        body: JSON.stringify({ name }) });
    if (!d.success) { $('pdev-msg').textContent = d.message || 'Failed.'; return; }
    const box = $('pdev-new');
    box.classList.remove('hidden');
    box.innerHTML = `<b>Pairing code for “${d.name}”</b> — enter it on the device within
        15 minutes (single use, shown once):<br>
        <code style="font-size:1.05rem">${d.pairing_code}</code>`;
    $('pdev-name').value = '';
    loadPortalDevices();
};

async function loadPortalGuardians() {
    const d = await api('/portal/api/guardians');
    if (!d.success) return;
    const list = $('pgdn-list'); list.innerHTML = '';
    (d.links || []).forEach(l => {
        const row = document.createElement('div'); row.className = 'item';
        row.innerHTML = `<div class="grow"><div>${l.beneficiary}
            <span class="pill">← ${l.guardian}</span></div>
            <div class="sub">${l.relationship || 'guardian'} · linked ${pFmt(l.created)}</div></div>`;
        const b = document.createElement('button');
        b.className = 'del'; b.textContent = 'Unlink';
        b.onclick = async () => {
            await api('/portal/api/guardians', { method: 'POST',
                body: JSON.stringify({ unlink: true, beneficiary: l.beneficiary,
                    guardian: l.guardian }) });
            loadPortalGuardians();
        };
        row.appendChild(b);
        list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML = '<div class="muted">No guardianship links.</div>';
}
$('pgdn-link').onclick = async () => {
    const d = await api('/portal/api/guardians', { method: 'POST',
        body: JSON.stringify({ beneficiary: $('pgdn-beneficiary').value,
            guardian: $('pgdn-guardian').value, relationship: $('pgdn-rel').value }) });
    $('pgdn-msg').textContent = d.success
        ? `Linked — ${d.guardian} may now verify for ${d.beneficiary}.`
        : (d.message || 'Failed.');
    if (d.success) { $('pgdn-beneficiary').value = ''; $('pgdn-guardian').value = ''; loadPortalGuardians(); }
};

async function loadPortalConsent() {
    const d = await api('/portal/api/consent');
    if (!d.success) return;
    $('pcns-text').value = d.policy.text;
    $('pcns-enforce').checked = !!d.policy.enforce_withdrawal;
    $('pcns-require').checked = !!d.policy.require_consent;
    const list = $('pcns-list'); list.innerHTML = '';
    (d.records || []).forEach(r => {
        const row = document.createElement('div'); row.className = 'item';
        const withdrawn = !!r.withdrawn_at;
        row.innerHTML = `<div class="grow"><div>${r.user_id}
            <span class="pill">${withdrawn ? 'WITHDRAWN' : 'granted'}</span>
            <span class="pill">v${r.version}</span></div>
            <div class="sub">${r.method} · ${pFmt(r.granted_at)}
            ${withdrawn ? ' · withdrawn ' + pFmt(r.withdrawn_at) : ''}</div></div>`;
        list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML = '<div class="muted">Consent records appear as people enrol.</div>';
    $('pcns-msg').textContent = `${d.granted} granted · ${d.withdrawn} withdrawn (statement v${d.policy.version}).`;
}
$('pcns-save').onclick = async () => {
    const d = await api('/portal/api/consent/policy', { method: 'POST',
        body: JSON.stringify({ text: $('pcns-text').value,
            enforce_withdrawal: $('pcns-enforce').checked,
            require_consent: $('pcns-require').checked }) });
    $('pcns-msg').textContent = d.success ? `Saved (statement v${d.version}).` : (d.message || 'Failed.');
    if (d.success) loadPortalConsent();
};
$('pcns-withdraw').onclick = async () => {
    const uid = ($('pcns-user').value || '').trim();
    if (!uid) { alert('Enter the user id first.'); return; }
    if (!confirm(`Withdraw consent for '${uid}'? Their verification is blocked immediately `
               + 'and their QR cards are revoked.')) return;
    const d = await api('/portal/api/consent/withdraw', { method: 'POST',
        body: JSON.stringify({ user_id: uid }) });
    $('pcns-msg').textContent = d.success
        ? `Consent withdrawn for ${uid} (${d.credentials_revoked} card(s) revoked).`
        : 'No consent record found.';
    loadPortalConsent();
};

refreshSession();
