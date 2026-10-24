# SYRIX Spark & Zenith 1.1 threat model

Status: pre-implementation release gate  
Date: 2026-07-29  
Scope: normal SYRIX chat, Spark, Zenith, Atlas, browser research, tools,
connectors, documents, model providers, HUD delivery, local services, storage,
and rollback. Super Computer is a separate trust domain and is not redesigned
by this release.

This is a bounded threat model, not a claim that SYRIX is secure in absolute
terms. It records the controls observed in the current repository, the gaps
that must be closed for this release, and the behaviour expected when a control
fails.

## Security invariants

1. Secrets remain local, are read from the git-ignored `.env` or the existing
   encrypted connector store, and are never exposed to a renderer, untrusted
   child process, log, model prompt, test artifact, or third-party service.
2. External content is evidence, never an instruction source. It cannot change
   policy, identity, permissions, mode, tool arguments, paths, recipients, or
   the original user task.
3. A tool remains available only through its existing permission boundary plus
   strict schema, task identity, freshness, replay, and result-binding checks.
   This release must not remove tools to make a security test pass.
4. Normal research permits only validated public HTTP(S) destinations. Every
   redirect and browser request must be checked before access; local files,
   loopback, private/LAN, link-local, reserved, and metadata destinations fail
   closed.
5. Every result and render update is owned by one immutable conversation, turn,
   task, request, generation, mode, and tool-call identity. An obsolete result
   cannot complete or overwrite a newer turn.
6. Connector and account actions remain read-only by default. Existing
   permission and confirmation gates remain authoritative and are rechecked
   immediately before execution.
7. Spark and Zenith share the same safety boundary. Zenith may use more
   research and validation, but it receives no additional permissions.
8. Super Computer state, tools, prompts, queues, and results cannot cross into
   normal chat. Existing Super Computer safety controls are preserved.
9. All persisted diagnostics are bounded, locally retained, and redacted before
   write. Raw conversations, connector content, hidden reasoning, cookies,
   headers, tool payloads, and full sensitive URLs are not telemetry.
10. The old Core V2/legacy path and its kill switches remain the explicit
    rollback path until shadow, canary, regression, security, and live gates
    pass.

## Assets and data classification

| Class | Examples | Handling |
|---|---|---|
| Secret | API/OAuth/session keys, cookies, pairing tokens, connector tokens | Local secret store or `.env` only; never renderer/model/log visible |
| Private | Chat, email/document bodies, local files, account identity, signed URLs | Minimum necessary task scope; local-first boundary; redacted/bounded persistence |
| Internal | System prompts, tool schemas, capability state, diagnostics, paths | Not user-rendered or embedded in external evidence |
| Untrusted | User text, web pages, snippets, PDFs, email bodies, tool/model output, filenames, code comments | Typed, length-bounded data with provenance; injection fenced and sanitized |
| Public evidence | Authoritative public pages and APIs | Still untrusted for instructions; source, time, and retrieval provenance retained |

## Threat actors and failure sources

- A malicious or compromised webpage, search result, document, email, connector
  record, MCP server, model provider, package, model file, or CDN asset.
- A webpage using cross-site requests or WebSockets against loopback services.
- An unauthorised LAN/mobile client when an opt-in remote feature is enabled.
- A malformed or adversarial user request attempting injection, resource
  exhaustion, path traversal, command execution, or data exfiltration.
- A delayed worker, retry, cache collision, stale browser result, or concurrent
  turn that is accidentally bound to the wrong task.
- A provider outage or weak fallback that returns unvalidated text.
- An operator error, unsafe update, dependency compromise, or incomplete
  rollback.
- Concurrent development agents modifying the shared worktree.

## Trust boundaries

### 1. User/HUD to Python chat runtime

- **Trusted:** the selected local session after authentication and the explicit
  current user instruction.
- **Untrusted:** chat text, mode markers, attachments, request IDs, timestamps,
  WebSocket frames, renderer scripts, and any claimed capability or permission.
- **Privileges:** submit chat/control events, display state/chat, trigger normal
  routing and allowed tools.
- **Data at risk:** recent chat, HUD state, local paths/status, connector state,
  provider credentials if exposed to the renderer.
- **Destructive potential:** command/control injection, replay, duplicate sends,
  stale-turn completion, oversized uploads, or invoking a privileged tool.
- **Existing containment:** loopback-default HUD bridge; renderer context
  isolation; DOMPurify answer rendering; per-request final latch and cancel
  register in `syrix_host.append_hud_chat_line`; upload/archive budgets.
- **Confirmed gaps:** `syrix_host_hud_bridge.HudWebSocketBridge` has no session
  authentication or Origin/Host validation, accepts control frames, and does
  not structurally force loopback; new sends do not cancel superseded backend
  jobs; the renderer can accept a mismatched late request ID by timestamp;
  queued typed-via-voice delivery can lose its request ID. The HUD preload/main
  process exposes a Groq secret to renderer code while the page loads remote
  scripts.
- **Required validation:** authenticated boot-scoped session, loopback-only
  bind unless an existing explicit remote feature is used, strict Origin/Host,
  frame schema/size/rate/connection caps, server-minted IDs, supersede
  cancellation, and exact generation ownership.
- **Audit:** identifiers, route, state transitions, size, and reject reason
  only; no message body or token.
- **Failure behaviour:** reject the frame or stale update, preserve the active
  turn, and show one specific local transport error. Never fall through to an
  unrelated answer.

### 2. Normal chat to planning, context, queues, caches, and fallback

- **Trusted:** verified runtime configuration, canonical session/task state,
  capability snapshot, and current authorization.
- **Untrusted:** model classification, inferred referents, cached rewrites,
  historical assistant output, old jobs, and fallback responses.
- **Privileges:** choose a route/provider/tool, attach history/evidence, and
  generate the final reply.
- **Data at risk:** cross-session chat, private context, mode identity, current
  task, and exact-output instructions.
- **Destructive potential:** wrong-topic answer, stale overwrite, duplicate
  side effects, raw internal trace, or a fallback replacing a valid response.
- **Existing containment:** Core V2 and legacy rollback flags; session transcript
  facade; request IDs; per-request final latch; bounded context; exact-output
  contract; reply cleaner; factual and freshness validators.
- **Confirmed gaps:** turn-resolution cache keys omit session/history; task
  memory is process-global and weakly scoped; some early agent paths discard
  evidence and bypass common validation; legacy/raw-brain fallbacks bypass
  quality gates; Spark fallback can be labelled Zenith; the universal evidence
  fallback is reachable; same-request latching does not alone reject an older
  request after a newer turn.
- **Required validation:** one session-scoped task ledger; typed state
  transitions; generation ownership; cache keys including session/context
  identity; one final validator outcome; fallback precedence below a valid
  answer; effective-engine truth.
- **Audit:** bounded stage codes and timing, never hidden reasoning or full
  prompts.
- **Failure behaviour:** retain the last verified partial result, state the
  exact failed dependency, and never resurrect a closed task or universal
  refusal.

### 3. Atlas, search, browser, URLs, pages, and downloads

- **Trusted:** the original task, URL policy decision, configured research
  budget, and verified public-source provenance.
- **Untrusted:** queries produced by a model, URLs, DNS, redirects, subresources,
  snippets, HTML/scripts, page text, metadata, PDFs, and downloads.
- **Privileges:** public network access and isolated browser subprocesses.
- **Data at risk:** local services, LAN devices, cloud metadata, signed/private
  URLs, cookies, `.env` inherited by a child, filesystem, clipboard, camera,
  microphone, and location.
- **Destructive potential:** SSRF, DNS rebinding, redirect/subresource pivot,
  drive-by download, browser-process leak, prompt injection, or resource abuse.
- **Existing containment:** `syrix_web.policy` restricts ordinary URLs to public
  HTTP(S), rejects credentials/private IPs, and exposes redirect checking;
  browser MCP is headless/read-only with timeouts and an idle shutdown; page and
  context budgets exist.
- **Confirmed gaps:** Atlas direct fetch and the built-in `read_url` bypass the
  canonical policy; `read_url` may disclose a full URL to a public proxy;
  transports do not consistently revalidate redirects; browser redirects and
  subresources can bypass the initial check; DNS decisions and later transport
  resolution are separated; child environments inherit unrelated secrets;
  response bytes are sometimes capped only after a full download.
- **Required validation:** one gateway for every fetch/read/open; public-IP
  resolution at connect time; validation of every redirect and browser request;
  scheme/credential/port/domain/IP/size/time/tab/download limits; sanitized
  stored URL; minimal child environment; isolated unauthenticated research
  profile; close all contexts/processes.
- **Audit:** request ID, hashed/sanitized URL, domain, policy code, byte count,
  redirect count, elapsed time, and close status.
- **Failure behaviour:** try another authorised public source within a bounded
  budget, then return supported material plus the specific network failure.
  Never access a blocked target or execute a download.

### 4. Evidence packs, synthesis, and prompt injection

- **Trusted:** system/developer policy, current explicit task, immutable task
  identity, capability/permission state, and deterministic validation.
- **Untrusted:** every fact/snippet/page/document/email/tool/model result,
  including encoded, nested, multilingual, hidden, and obfuscated instructions.
- **Privileges:** influence factual synthesis only.
- **Data at risk:** prompts, secrets, provider boundary, paths, recipients,
  permissions, and tool arguments.
- **Destructive potential:** indirect injection, data exfiltration, permission
  escalation, malicious tool invocation, or factual corruption.
- **Existing containment:** `syrix_web.injection` scanner/wrapper, source
  records, grounding/claim checks, answer cleaner, connector choke point.
- **Confirmed gaps:** the injection wrapper is used only on one last-resort
  path; Spark, Zenith, connectors/email, DocIntel, generic tools, and MCP output
  can enter prompts as raw text; trust labels are not consistently carried into
  the evidence object.
- **Required validation:** typed evidence with source type, origin, retrieval
  time, trust level, injection findings, content hash, and task binding;
  deterministic instruction fencing before synthesis; strip executable markup;
  never derive actions/arguments from evidence; explicit user authorization for
  privileged action suggested by evidence.
- **Audit:** source identifiers, trust/finding codes, and hashes only.
- **Failure behaviour:** discard or quote-safe the malicious segment, continue
  with unaffected evidence, and report missing coverage only if it prevents the
  requested answer.

### 5. Tools, MCP, connectors, and side effects

- **Trusted:** canonical capability registry, runtime availability, strict local
  schema, existing connector permission/confirmation decision, and current
  user authorization.
- **Untrusted:** model tool name/arguments, MCP-advertised schema, tool output,
  external-content instructions, retries, and late/replayed results.
- **Privileges:** public reads, local status/time, connector/account access,
  filesystem/process/browser operations according to the individual tool.
- **Data at risk:** accounts, local files, secrets, recipients, and private tool
  output.
- **Destructive potential:** duplicate sends/writes, wrong account/task, command
  injection, path escape, replay, or permission bypass.
- **Existing containment:** built-in registry; connector
  `manager.execute` kill switch, version quarantine, connection check,
  deny-unknown permission, confirmation, timeout, and redacted audit; DPAPI
  token storage.
- **Confirmed gaps:** execution generally carries only request ID; no universal
  immutable conversation/turn/task/generation/tool-call binding, strict
  `additionalProperties: false`, replay check, or idempotency key; MCP tools and
  child environments bypass connector-style mediation; some timed-out workers
  cannot cancel a side effect; connector content can be forwarded to a provider.
- **Required validation:** per-tool strict schema and size bounds, immutable
  task binding, capability scope, authorization recheck immediately before
  execution, result binding immediately after, read-only default, confirmation
  for side effects, idempotency for duplicate-sensitive actions, bounded retry,
  minimal child environment, and reliable process cleanup.
- **Audit:** identity tuple, tool/action name, permission outcome, idempotency
  status, elapsed time, and redacted result type/count.
- **Failure behaviour:** deny unknown or stale calls without execution; preserve
  the tool and report a specific permission/schema/dependency error.

### 6. Local files, paths, generated code, HTML, and subprocesses

- **Trusted:** explicitly user-authorised root, canonical path, allowlisted
  executable/action, and sandbox policy.
- **Untrusted:** filenames, archive entries, model-generated code/commands,
  paths, environment variables, HTML/SVG/Markdown, downloaded files, and tool
  output.
- **Privileges:** only those of the narrow tool or preview sandbox.
- **Data at risk:** user files, `.env`, source tree, browser profile, and local
  network.
- **Destructive potential:** traversal/junction escape, overwrite/delete,
  macro/binary execution, shell injection, process storm, XSS, or iframe escape.
- **Existing containment:** repository execution sandbox; DocIntel ZIP
  traversal/executable/ratio/entry/size guards; Super Computer workspace
  containment; tokenized loopback preview with CSP; sandboxed generated preview
  iframe; DOMPurify.
- **Confirmed gaps:** equivalent expansion budgets are incomplete for some
  archive formats; normal document text is not injection-fenced; backend upload
  limits must not depend on the renderer; full parsed documents may persist
  without TTL; some browser/UI surfaces load remote scripts and run without the
  strongest sandbox.
- **Required validation:** canonical real path under an approved root, device/
  UNC/junction rejection, atomic write/backup, no implicit execution, argument
  arrays (not shell text), no secrets/network in generated-code tests, bounded
  CPU/RAM/time/processes, uniform archive budgets, strict sanitizer/CSP and
  preview sandbox.
- **Audit:** operation category, approved root identifier, sizes/counts, child
  PID lifecycle, and redacted failure code.
- **Failure behaviour:** no write/process/render on validation failure; retain
  original files and return the exact rejected boundary.

### 7. Model providers, private data, cache, and retention

- **Trusted:** explicit provider policy, task data classification, configured
  local-only/private mode, and minimized prompt.
- **Untrusted:** provider output, provider availability claims, model-generated
  tool calls, cache entries, and fallback ordering.
- **Privileges:** receive the minimized prompt/evidence allowed for this task
  and return text/tool proposals only.
- **Data at risk:** conversation, connector/document content, personal context,
  identifiers, secrets, and provider choice.
- **Destructive potential:** silent cloud disclosure, cache leakage, weaker
  model hallucination, or response-driven permission escalation.
- **Existing containment:** configured provider allowlists, local Ollama
  fallback, timeouts, redaction utilities, bounded brain cache.
- **Confirmed gaps:** the cloud-capable brain pool can run before the legacy
  local-sensitive routing heuristic; data classification is keyword-based
  rather than structural; connector/document synthesis can use ordinary cloud
  routing; full replies and retrieved material can persist with count-only or
  read-time TTL.
- **Required validation:** provider decision before any dispatch, structured
  sensitivity/local-only flag, same identity/permissions/evidence provenance
  across fallbacks, no-cache for private/tool/account data, session/account in
  permissible cache keys, age and byte pruning, verified deletion and
  disconnect/reset cleanup.
- **Audit:** provider/model/version, data-class code, local/cloud decision,
  cache hit/miss, elapsed time, and failure code; no private content.
- **Failure behaviour:** fail to an authorised provider with the same
  constraints or give a specific unavailable-provider result. Never silently
  relax the privacy boundary.

### 8. Logs, traces, telemetry, crash reports, and learning

- **Trusted:** bounded structured event schema and local retention settings.
- **Untrusted:** user/tool/model text, exceptions, terminal escapes, URLs,
  headers, filenames, and corrections.
- **Privileges:** local diagnostic persistence only.
- **Data at risk:** secrets, conversations, email/document bodies, full local
  paths, prompts, and tool payloads.
- **Destructive potential:** secret persistence, log injection, disk exhaustion,
  or autonomous behavioural change.
- **Existing containment:** `syrix_redact`, connector redacted/count-only audit,
  failure journal, bounded event patterns, no autonomous upload requirement.
- **Confirmed gaps:** redaction is not universal before persistence; some
  exception and URL paths may retain raw material; several caches/stores have no
  age/byte retention; pairing-token logging is reachable in the optional remote
  API.
- **Required validation:** redact and normalize before every persistent write,
  fixed event schemas and lengths, finite age/byte retention, local-only
  corrections, no chain-of-thought, no self-modifying production path, and
  tests generated only through reviewed code changes.
- **Audit:** audit-store health and prune counts without persisted content.
- **Failure behaviour:** drop unsafe diagnostic content rather than write it;
  conversational operation may continue with a minimal error code.

### 9. Local listeners, LAN/mobile access, and OAuth callbacks

- **Trusted:** loopback or an explicitly enabled, authenticated, rate-limited
  remote device/session.
- **Untrusted:** network client, Host/Origin, CORS preflight, request body,
  WebSocket frame, OAuth callback, and pairing request.
- **Privileges:** endpoint-specific control or account connection.
- **Data at risk:** chat/state, account tokens, pairing token, local control
  surface, and detailed health.
- **Destructive potential:** unauthorised commands, CSRF/cross-site WebSocket
  use, token replay, LAN exposure, or denial of service.
- **Observed listener inventory:** HUD WebSocket ports 5111 and 5112 are
  loopback-default; the remote API is disabled and loopback-default, with LAN
  bind requiring two explicit gates; the FastAPI bridge is opt-in and
  loopback-default on 5180; Google OAuth uses a temporary loopback callback;
  Super Computer preview uses an ephemeral loopback port and random path token.
- **Confirmed gaps:** HUD WebSocket authentication/Origin/Host/caps; optional
  FastAPI auth/caps; optional remote API query-token/logging/session-expiry and
  Origin/Host controls. Configurable hosts must not silently make ordinary chat
  non-loopback.
- **Required validation:** loopback enforcement, authenticated boot/session
  tokens, strict Host/Origin/CORS, CSRF for state change, token expiry/revocation,
  request/frame/rate/connection/idle caps, redacted OAuth state/PKCE checks, no
  new firewall rule.
- **Audit:** bind address/port, endpoint class, auth outcome, device/session ID,
  and rate-limit outcome.
- **Failure behaviour:** refuse startup on an unsafe bind or reject the client
  without revealing state.

### 10. Dependencies, plugins, models, GitHub code, and renderer assets

- **Trusted:** repository-pinned, reviewed dependencies from an authoritative
  publisher with lockfile/checksum controls available to the project.
- **Untrusted:** install scripts, transitive packages, remote CDN JavaScript,
  MCP executables, model files, prompt packs, plugins, downloaded assets, and
  GitHub content.
- **Privileges:** code execution in their hosting process and access to its
  environment/data.
- **Data at risk:** all process-visible secrets, chat, files, and IPC tools.
- **Destructive potential:** supply-chain execution, credential theft, IPC
  abuse, persistence, or data exfiltration.
- **Existing containment:** Python/Node lockfiles and audit commands; no new
  dependency is required for this release; sandboxed browser/preview surfaces.
- **Confirmed gaps:** execution-critical HUD scripts are loaded remotely without
  SRI while renderer IPC is powerful; MCP/browser children inherit the parent
  environment; executable provenance/pinning must be verified.
- **Required validation:** prefer existing dependencies; vendor or
  cryptographically pin renderer assets; narrow child environments; review
  license/publisher/version/install scripts; dependency audits; secret scan;
  no remote script execution pipelines or surprise paid/telemetry services.
- **Audit:** package/model name, pinned version/hash/source, scan result, and
  documented exception.
- **Failure behaviour:** keep the last known-good pinned component and disable
  only the affected optional integration through its kill switch.

### 11. Concurrent development, update, canary, and rollback

- **Trusted:** reviewed patch, explicit file ownership, clean milestone commit,
  deterministic tests, and signed/private repository history.
- **Untrusted:** dirty worktree assumptions, concurrent-agent edits, partial
  migration, stale generated artifacts, and unverified release claims.
- **Privileges:** modify local source and activate the normal-chat route.
- **Data at risk:** all existing features, private source, uncommitted work, and
  rollback ability.
- **Destructive potential:** overwrite another task, weaken a safety control,
  publish private code, or leave two conflicting runtimes active.
- **Existing containment:** private repository rule; lossless stash checkpoint;
  explicit Core V2/legacy/Atlas/browser/connectors/Super Computer kill switches;
  full regression suites.
- **Current baseline:** pre-existing Super Computer work is preserved in
  `stash@{0}` and remains visible and untouched on the release branch. The
  binary patch identity was verified before implementation.
- **Required validation:** explicit-path staging only; no unrelated cleanup;
  threat model before architecture; milestone commits; shadow diagnostics;
  deterministic canary selection; full/offline/security/live gates; rollback
  test with the legacy flag; final diff and secret scan.
- **Audit:** branch, commit, test command/result, config selection reason, and
  rollback result. Do not log source content to an external service.
- **Failure behaviour:** turn off the new normal-chat flag and retain the
  previously working backend. Never reset or delete uncommitted user work.

## Release-blocking confirmed vulnerabilities

The following are findings to fix or explicitly keep the release blocked:

1. Raw Groq credential exposure to HUD renderer scripts and IPC.
2. Unauthenticated/control-capable HUD WebSocket with missing Origin/Host
   validation.
3. SSRF-policy bypasses in Atlas direct fetch and built-in `read_url`, plus
   redirect/subresource/DNS-rebinding gaps.
4. Raw untrusted web/document/email/tool evidence entering Spark/Zenith/provider
   prompts without consistent provenance and injection fencing.
5. Missing immutable full-task binding, strict schema enforcement, replay
   rejection, and idempotency at the common tool boundary.
6. Cloud-capable provider dispatch occurring before dependable private/local
   data classification.
7. Secret-bearing parent environments inherited by browser/MCP subprocesses.
8. Optional remote pairing token written to logs.
9. Cross-session turn-resolution cache and late/mismatched response ownership.
10. Raw tool syntax and unsupported synthesis paths that can bypass common
    evidence validation.
11. Retention stores with incomplete age/byte pruning and deletion behaviour.
12. Remote renderer dependencies without a cryptographic integrity boundary.

## Required adversarial verification

Before activation, tests must cover:

- direct, webpage, snippet, document/PDF/email, tool-result, nested, encoded,
  obfuscated, and multilingual prompt injection;
- strict unknown/malformed/oversized tool fields, command/path/URL injection,
  revoked permission, wrong identity, replay, late result, and duplicate
  side-effect calls;
- loopback/private/link-local/metadata/credential/dangerous-scheme URLs,
  redirects to private IPs, DNS rebinding simulation, browser subresources,
  unauthorized downloads, and child-environment secret absence;
- source-title, filename, Markdown, citation, tool-output, SVG, iframe,
  `javascript:` URL, and generated-preview XSS;
- path traversal, alternate separators, junction/symlink escape, overwrite/
  delete without permission, shell injection, runaway processes, archive
  expansion, and upload-limit bypass;
- redaction before persistence, provider-boundary enforcement, cache/session/
  account isolation, TTL/byte pruning, and verified deletion;
- loopback listener inventory, unsafe bind rejection, authenticated HUD
  WebSocket, Origin/Host/CSRF/CORS/rate/payload/connection limits, remote-session
  expiry, and no firewall modification;
- pinned dependency/renderer assets, secret scanning, and no unexpected install
  script or binary;
- Spark/Zenith/Super Computer isolation and rollback with all tools still
  present.

## Rollout and rollback boundary

The 1.1 path must be additive and selected by a dedicated feature flag with
audit/shadow and deterministic canary modes. The existing
`syrix_core_v2_enabled` and `syrix_core_v2_force_legacy` controls remain the
outer rollback. Atlas, browser, connector, MCP, remote API, and Super Computer
kill switches remain unchanged. A rollback test must show that disabling 1.1
restores the prior Core V2 behaviour without changing tool availability,
listener configuration, connector permission state, or stored user data.

No architecture implementation may begin until this document exists. Activation
remains blocked until the listed security tests, existing security suites,
dependency scans, listener comparison, secret scan, full regression suite,
Super Computer isolation check, and rollback proof have recorded results.
