# Security status and reporting

The Phase 0 foundation implements local-development controls only (loopback binding, generated local token, HttpOnly sessions, workspace-scoped authorization, artifact containment, redaction); see "Implemented controls" in docs/SECURITY_MODEL.md. These are not hosted or multi-tenant security guarantees, and no independent security review has been performed. Before a public/private enterprise release, follow docs/SECURITY_MODEL.md and record a security review.

Do not upload sensitive customer repositories to public issues, logs, model providers or plugins. Use synthetic fixtures for development. Report suspected vulnerabilities through a maintainer-approved private channel; the maintainer must configure that channel before release. Do not invent a reporting address or service-level promise.

Repository contents and executable analysis configuration are untrusted. Approved policy and tool permissions live in the control plane. No automatic source execution, production credentials, unrestricted source-path endpoints or default external model transmission.

Before release, document supported versions, patch policy, disclosure process, retention/deletion, incident response and external assessment results. Do not claim compliance certification from static scanning.
