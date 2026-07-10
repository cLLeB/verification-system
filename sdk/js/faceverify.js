/**
 * Biometric Verification Backbone — JavaScript SDK (browser & Node 18+, zero deps).
 *
 * Face AND palm in one API: the server AUTO-DETECTS whether each image is a face or
 * a palm and routes it — you never declare the modality. A user can enrol either or
 * both under one id; presenting either verifies them (`r.modality` says which).
 *
 *   import { FaceVerifyClient } from "./faceverify.js";
 *   const fv = new FaceVerifyClient("https://your-host:5000", "fk_yourkey");
 *   await fv.enroll("alice", [dataUrl1, dataUrl2, dataUrl3]);  // faces or palms
 *   const r = await fv.verify("alice", dataUrl);               // either one
 *   if (r.success) grantAccess();
 *
 * Images are passed as base64 strings or data-URLs (the server strips the prefix).
 * Pass an optional `modality` ("face" | "palm") only to pin routing; omit it to
 * auto-detect. NOTE: an admin/enroll key in browser code is exposed to users — keep
 * enrol keys server-side and only ship a `verify`-role key to the browser if at all.
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
  embed(image, modality) {
    const body = { image };
    if (modality) body.modality = modality;
    return this._call("POST", "/v1/embed", body);
  }
  compare(probe, references, threshold) {
    const ref = (x) => (typeof x === "object" ? x : { image: x });
    const body = { probe: ref(probe), references: references.map(ref) };
    if (threshold != null) body.threshold = threshold;
    return this._call("POST", "/v1/compare", body);
  }

  // managed — image may be a face OR a palm; auto-detected unless `modality` is set.
  // Palm holds BOTH hands under one id (present either to verify). Several palm
  // images at once auto-bind up to two hands (bulk). `hand`: "any" (auto-bind 2nd,
  // default for multi-image) | "other" (confirm after a different_hand prompt) |
  // "auto" (single-image UI: a new hand returns code:"different_hand" to confirm).
  // A third distinct hand is refused (code:"hands_full").
  enroll(userId, images, modality, hand) {
    const imgs = Array.isArray(images) ? images : [images];
    const body = { user_id: userId, images: imgs };
    if (modality) body.modality = modality;
    if (hand) body.hand = hand;
    return this._call("POST", "/v1/enroll", body);
  }
  enrollBulk(people) { return this._call("POST", "/v1/enroll/bulk", { people }); }
  verify(userId, image, modality) {
    const body = { user_id: userId, image };
    if (modality) body.modality = modality;
    return this._call("POST", "/v1/verify", body);
  }
  identify(image, modality) {
    const body = { image };
    if (modality) body.modality = modality;
    return this._call("POST", "/v1/identify", body);
  }
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
  /** Template-protection status per modality (or one user via userId). Admin key required. */
  templateStatus(userId) {
    const q = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
    return this._call("GET", `/v1/templates/status${q}`);
  }
  /** Re-protect stored templates in a new domain (cancelable biometrics): old
   *  exported/stolen copies stop matching; nobody re-enrols. Omit userId for the
   *  whole tenant. Admin key required. */
  reissueTemplates(userId) {
    const body = { confirm: true };
    if (userId) body.user_id = userId;
    return this._call("POST", "/v1/templates/reissue", body);
  }
  /** Issue a signed QR credential for an enrolled user -> {credential_id,
   *  payload_b45, qr_png_b64, expires}. Admin key required. */
  issueCredential(userId, { modalities, expiryDays = 365, name, attrs } = {}) {
    const body = { user_id: userId, expiry_days: expiryDays };
    if (modalities) body.modalities = modalities;
    if (name) body.name = name;
    if (attrs) body.attrs = attrs;
    return this._call("POST", "/v1/credentials", body);
  }
  listCredentials(userId) {
    const q = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
    return this._call("GET", `/v1/credentials${q}`);
  }
  /** Revoke an issued credential (offline verifiers see it on their next
   *  trust-store refresh). Admin key required. */
  revokeCredential(credentialId) { return this._call("DELETE", `/v1/credentials/${credentialId}`); }
  /** Hosted check of a scanned FV1: credential against a live capture. */
  verifyCredential(credential, { image, embedding } = {}) {
    const body = { credential };
    if (image) body.image = image;
    if (embedding) body.embedding = embedding;
    return this._call("POST", "/v1/credentials/verify", body);
  }
  /** On-device 1:N glance index (int8 protection-domain vectors + calibrated
   *  threshold). Admin key + allow_export entitlement required. */
  glanceIndex(modality = "face") { return this._call("GET", `/v1/sync/index?modality=${modality}`); }
  /** The glance index as a passphrase-encrypted file payload (air-gapped devices). */
  exportGlanceIndex(passphrase, modality = "face") {
    return this._call("POST", "/v1/export/glance-index", { passphrase, modality });
  }
  /** Public signed bundle of issuer keys + revocation lists. */
  trustStore() { return this._call("GET", "/v1/trust-store"); }
  trustedIssuers() { return this._call("GET", "/v1/trust"); }
  /** Accept credentials issued by another tenant (cross-org verification). */
  trustIssuer(tenant) { return this._call("POST", `/v1/trust/${tenant}`); }
  untrustIssuer(tenant) { return this._call("DELETE", `/v1/trust/${tenant}`); }
  /** List this tenant's issuer signing keys (active first). Admin key required. */
  tenantKeys() { return this._call("GET", "/v1/tenant/keys"); }
  /** Rotate the issuer signing key. Previously signed items stay verifiable. */
  rotateTenantKeys() { return this._call("POST", "/v1/tenant/keys/rotate", { confirm: true }); }
  usage() { return this._call("GET", "/v1/usage"); }
  health() { return this._call("GET", "/v1/health"); }
}
