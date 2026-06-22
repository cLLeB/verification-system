/**
 * <face-verify> — drop-in face verification web component.
 *
 *   <script src="https://YOUR-HOST/widget.js"></script>
 *   <face-verify base="https://YOUR-HOST" api-key="fk_verify_key"></face-verify>
 *
 *   const el = document.querySelector('face-verify');
 *   el.addEventListener('result', (e) => {
 *     if (e.detail.success) grantAccess(e.detail.user_id);  // e.detail is the signed API result
 *   });
 *
 * Attributes:
 *   base        API base URL (required)
 *   api-key     a VERIFY-role key (required; safe to expose — it can only recognise)
 *   user-id     optional: verify a specific person (1:1). Omit for identify (1:N).
 *   accent      optional: override the accent colour (default iris violet #8B7CF6)
 *   button-text optional: CTA label (default "Verify")
 *
 * Self-contained (Shadow DOM, no dependencies). The integrator must register their
 * site's origin in the tenant's CORS settings (admin console) so the browser calls
 * are allowed. Matches the platform's "Verified" design language.
 */
(function () {
  if (customElements.get('face-verify')) return;
  const BURST = 7, GAP = 360, OUT_W = 640;

  class FaceVerify extends HTMLElement {
    connectedCallback() {
      this.base = (this.getAttribute('base') || '').replace(/\/+$/, '');
      this.key = this.getAttribute('api-key') || '';
      this.userId = this.getAttribute('user-id') || '';
      this.accent = this.getAttribute('accent') || '#8B7CF6';
      this.buttonText = this.getAttribute('button-text') || 'Verify';
      this.busy = false;
      this._render();
      this._start();
    }
    disconnectedCallback() { this._stop(); }

    _render() {
      const a = this.accent;
      this.root = this.attachShadow({ mode: 'open' });
      this.root.innerHTML = `
        <style>
          :host { all: initial; display: inline-block;
            font-family: "Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
          .wrap { width: 280px; background:#141B2D; border:1px solid rgba(148,163,184,.16);
            border-radius:18px; padding:16px; color:#EAF0F8; text-align:center; }
          .oval { position:relative; width:200px; aspect-ratio:4/5; margin:0 auto 12px;
            border-radius:50%; overflow:hidden; background:#05080d; border:3px solid ${a};
            box-shadow:0 0 0 6px ${a}1a, 0 0 22px ${a}3d inset; }
          video { width:100%; height:100%; object-fit:cover; transform:scaleX(-1); display:block; border-radius:50%; }
          .oval.busy { animation:fv-pulse 1.4s ease infinite; }
          @keyframes fv-pulse { 50% { box-shadow:0 0 0 10px ${a}26, 0 0 40px ${a}80 inset; } }
          .hint { min-height:1.2em; font-size:13px; color:#9AA8BD; margin:2px 0 10px; font-weight:500; }
          .hint.info { color:${a}; font-weight:600; }
          button { width:100%; border:0; border-radius:12px; padding:13px; font-size:15px; font-weight:600;
            font-family:inherit; color:#fff; background:#6D4DE6; cursor:pointer; }
          button:disabled { opacity:.5; cursor:default; }
          .res { display:flex; align-items:center; justify-content:center; gap:8px;
            font-weight:700; min-height:1.4em; margin-top:6px; }
          .ok { color:#34D399; } .bad { color:#FB7185; }
          .powered { margin-top:10px; font-size:10px; color:#67768D; letter-spacing:.04em; }
        </style>
        <div class="wrap">
          <div class="oval" id="oval"><video id="v" autoplay playsinline muted></video></div>
          <div class="hint" id="hint">Center your face, then ${this.buttonText.toLowerCase()}</div>
          <button id="go">${this.buttonText}</button>
          <div class="res" id="res"></div>
          <div class="powered">SECURED BY FACE VERIFY</div>
        </div>`;
      this.$ = (id) => this.root.getElementById(id);
      this.$('go').onclick = () => this._verify();
    }

    async _start() {
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
        this.$('v').srcObject = this.stream;
      } catch (e) { this._hint('Camera unavailable — allow access.'); }
    }
    _stop() { if (this.stream) this.stream.getTracks().forEach(t => t.stop()); }
    _hint(t, cls = '') { const h = this.$('hint'); h.textContent = t; h.className = 'hint ' + cls; }
    _wait(ms) { return new Promise(r => setTimeout(r, ms)); }

    _frame() {
      const v = this.$('v'); if (!v.videoWidth) return null;
      const w = Math.min(OUT_W, v.videoWidth), h = Math.round(w * v.videoHeight / v.videoWidth);
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').drawImage(v, 0, 0, w, h);
      return c.toDataURL('image/jpeg', 0.9);
    }
    _api(path, body) {
      return fetch(this.base + path, {
        method: body ? 'POST' : 'GET',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': this.key },
        body: body ? JSON.stringify(body) : undefined,
      }).then(r => r.json());
    }

    async _verify() {
      if (this.busy) return;
      this.busy = true; this.$('go').disabled = true; this.$('res').textContent = '';
      this.$('oval').classList.add('busy');
      try {
        let ch = {};
        try { ch = await this._api('/v1/challenge'); } catch (e) {}
        let result;
        if (ch && ch.active) {
          this._hint('Keep your face in the oval…');
          await this._wait(400);
          const frames = [];
          for (let i = 0; i < BURST; i++) {
            const f = this._frame(); if (f) frames.push(f);
            const frac = (i + 1) / BURST;
            this._hint(frac < 0.45 ? '←  Slowly turn your head LEFT'
                     : frac < 0.85 ? 'Now turn your head RIGHT  →' : 'Look at the camera', 'info');
            await this._wait(GAP);
          }
          this._hint('Checking…');
          const body = { frames }; if (ch.token) body.token = ch.token;
          if (this.userId) body.user_id = this.userId;
          result = await this._api('/v1/verify', body);
        } else {
          this._hint('Checking…');
          const img = this._frame();
          const body = { image: img }; if (this.userId) body.user_id = this.userId;
          result = await this._api('/v1/verify', body);
        }
        this._show(result);
        this.dispatchEvent(new CustomEvent('result', { detail: result, bubbles: true, composed: true }));
      } catch (e) {
        this._hint('Network error — try again.');
        this.dispatchEvent(new CustomEvent('error', { detail: String(e), bubbles: true, composed: true }));
      } finally {
        this.busy = false; this.$('go').disabled = false; this.$('oval').classList.remove('busy');
      }
    }

    _show(r) {
      this._hint(r && r.hint ? r.hint : '');
      const res = this.$('res');
      if (r && r.success) { res.className = 'res ok'; res.textContent = '✓ ' + (r.user_id ? 'Welcome, ' + r.user_id : 'Verified'); }
      else { res.className = 'res bad'; res.textContent = '✕ ' + ((r && r.message) || 'Not recognised'); }
    }
  }
  customElements.define('face-verify', FaceVerify);
})();
