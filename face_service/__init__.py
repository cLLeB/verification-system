"""HTTP integration layer for the face verification backbone.

Exposes a versioned, API-key-authenticated REST API (/v1) that other apps call
to enroll/verify (managed) or to embed/compare their own data (stateless), with
per-tenant isolation and signed results. The face/ package is the engine; this
package is how external developers integrate with it.
"""
