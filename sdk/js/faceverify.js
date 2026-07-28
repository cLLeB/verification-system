/**
 * Biometric Verification Backbone - JavaScript SDK (browser & Node 18+, zero deps).
 *
 * Face AND palm in one API: the server AUTO-DETECTS whether each image is a face or
 * a palm and routes it - you never declare the modality. A user can enrol either or
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
 * auto-detect. NOTE: an admin/enroll key in browser code is exposed to users - keep
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

  // managed - image may be a face OR a palm; auto-detected unless `modality` is set.
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
  // --- access policies (authorization on top of verification) ---------------
  policies() { return this._call("GET", "/v1/policies"); }
  /** mode: "off"|"advise"|"enforce"; defaultOutcome: "allow"|"deny". */
  configurePolicies({ mode, defaultOutcome, tzOffsetMinutes } = {}) {
    const body = {};
    if (mode) body.mode = mode;
    if (defaultOutcome) body.default = defaultOutcome;
    if (tzOffsetMinutes != null) body.tz_offset_minutes = tzOffsetMinutes;
    return this._call("POST", "/v1/policies", body);
  }
  /** subjects: "*", "user:<id>", "group:<name>"; start/end "HH:MM" (overnight wraps). */
  addPolicyRule(name, effect, subjects, { days, start, end } = {}) {
    const body = { name, effect, subjects };
    if (days) body.days = days;
    if (start && end) { body.start = start; body.end = end; }
    return this._call("POST", "/v1/policies/rules", body);
  }
  setPolicyGroup(name, members) {
    return this._call("POST", "/v1/policies/groups", { name, members });
  }

  // --- guest passes (identities that expire) ---------------------------------
  /** After expiry, verifies return code:"identity_expired". */
  setGuest(userId, { days = 0, hours = 0 } = {}) {
    return this._call("POST", "/v1/guests",
      { user_id: userId, expires_in_days: days, expires_in_hours: hours });
  }
  guests() { return this._call("GET", "/v1/guests"); }
  clearGuest(userId) { return this._call("DELETE", `/v1/guests/${encodeURIComponent(userId)}`); }
  purgeExpiredGuests(graceHours = 0) {
    return this._call("POST", "/v1/guests/purge", { grace_hours: graceHours });
  }

  // --- device registry --------------------------------------------------------
  /** Mint a single-use pairing code for a kiosk (returned ONCE). Admin key. */
  createDevicePairing(name) { return this._call("POST", "/v1/devices/pairings", { name }); }
  /** Device-side: redeem the code -> {device_id, api_key}. No key needed. */
  pairDevice(pairingCode) { return this._call("POST", "/v1/devices/pair", { pairing_code: pairingCode }); }
  /** Call with the DEVICE's own key. */
  deviceHeartbeat(info = {}) { return this._call("POST", "/v1/devices/heartbeat", { info }); }
  devices() { return this._call("GET", "/v1/devices"); }
  /** Cut one device off immediately (its key is revoked). */
  disableDevice(deviceId) {
    return this._call("POST", `/v1/devices/${encodeURIComponent(deviceId)}/disable`);
  }

  // --- guardianship (proxy verification) ---------------------------------------
  linkGuardian(beneficiary, guardian, relationship = "") {
    return this._call("POST", "/v1/guardians", { beneficiary, guardian, relationship });
  }
  unlinkGuardian(beneficiary, guardian) {
    return this._call("POST", "/v1/guardians/unlink", { beneficiary, guardian });
  }
  guardians({ beneficiary, guardian } = {}) {
    const q = beneficiary ? `?beneficiary=${encodeURIComponent(beneficiary)}`
      : guardian ? `?guardian=${encodeURIComponent(guardian)}` : "";
    return this._call("GET", `/v1/guardians${q}`);
  }
  /** The guardian presents THEIR OWN biometric on behalf of the linked
   *  beneficiary. success + code:"proxy_match" approves; r.proxy carries both
   *  identities for your ledger. */
  verifyProxy(beneficiary, image, guardian) {
    const body = { on_behalf_of: beneficiary, image };
    if (guardian) body.user_id = guardian;
    return this._call("POST", "/v1/verify", body);
  }

  // --- consent & data-subject rights --------------------------------------------
  consentSummary() { return this._call("GET", "/v1/consent"); }
  setConsentPolicy({ text, enforceWithdrawal, requireConsent } = {}) {
    const body = {};
    if (text != null) body.text = text;
    if (enforceWithdrawal != null) body.enforce_withdrawal = enforceWithdrawal;
    if (requireConsent != null) body.require_consent = requireConsent;
    return this._call("POST", "/v1/consent/policy", body);
  }
  consentReceipt(userId) { return this._call("GET", `/v1/consent/${encodeURIComponent(userId)}`); }
  recordConsent(userId, method = "operator") {
    return this._call("POST", "/v1/consent/record", { user_id: userId, method });
  }
  /** Blocks the person's verification immediately and revokes their QR cards. */
  withdrawConsent(userId) { return this._call("POST", "/v1/consent/withdraw", { user_id: userId }); }

  /** Offline mirror of the service gates for hybrid devices. Admin key. */
  serviceState() { return this._call("GET", "/v1/service-state"); }

  usage() { return this._call("GET", "/v1/usage"); }
  health() { return this._call("GET", "/v1/health"); }
}
