/**
 * Face Verification Backbone — JavaScript SDK (browser & Node 18+, zero deps).
 *
 *   import { FaceVerifyClient } from "./faceverify.js";
 *   const fv = new FaceVerifyClient("https://your-host:5000", "fk_yourkey");
 *   await fv.enroll("alice", [dataUrl1, dataUrl2, dataUrl3]);
 *   const r = await fv.verify("alice", dataUrl);
 *   if (r.success) grantAccess();
 *
 * Images are passed as base64 strings or data-URLs (the server strips the prefix).
 * NOTE: an admin/enroll key in browser code is exposed to users — keep enrol keys
 * server-side and only ship a `verify`-role key to the browser if at all.
 */
export class FaceVerifyClient {
  constructor(baseUrl, apiKey, { timeoutMs = 30000 } = {}) {
    this.base = baseUrl.replace(/\/+$/, "");
    this.apiKey = apiKey;
    this.timeoutMs = timeoutMs;
  }

  async _call(method, path, body) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), this.timeoutMs);
    try {
      const res = await fetch(this.base + path, {
        method,
        headers: { "Content-Type": "application/json", "X-API-Key": this.apiKey },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      });
      return await res.json();
    } finally {
      clearTimeout(t);
    }
  }

  // stateless
  embed(image) { return this._call("POST", "/v1/embed", { image }); }
  compare(probe, references, threshold) {
    const ref = (x) => (typeof x === "object" ? x : { image: x });
    const body = { probe: ref(probe), references: references.map(ref) };
    if (threshold != null) body.threshold = threshold;
    return this._call("POST", "/v1/compare", body);
  }

  // managed
  enroll(userId, images) {
    const imgs = Array.isArray(images) ? images : [images];
    return this._call("POST", "/v1/enroll", { user_id: userId, images: imgs });
  }
  enrollBulk(people) { return this._call("POST", "/v1/enroll/bulk", { people }); }
  verify(userId, image) { return this._call("POST", "/v1/verify", { user_id: userId, image }); }
  identify(image) { return this._call("POST", "/v1/identify", { image }); }
  verifyLive(frames, token, userId = "") {
    const body = { frames, token };
    if (userId) body.user_id = userId;
    return this._call("POST", "/v1/verify", body);
  }
  challenge() { return this._call("GET", "/v1/challenge"); }
  users() { return this._call("GET", "/v1/users"); }
  deleteUser(userId) {
    return Array.isArray(userId)
      ? this._call("POST", "/v1/users/delete", { user_ids: userId })
      : this._call("POST", "/v1/users/delete", { user_id: userId });
  }
  exportUser(userId) { return this._call("POST", "/v1/users/export", { user_id: userId }); }
  usage() { return this._call("GET", "/v1/usage"); }
  health() { return this._call("GET", "/v1/health"); }
}
