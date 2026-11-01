# Known Limitations — HeritageGraph AI

This register describes what the current build does **not** do well or does not
do at all. It is based on the repository and the verification runs recorded in
the README. A limitation is not hidden behind a feature claim.

---

## 1. OCR

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| Handwriting and historical scripts are weakly supported | Handwritten ledgers, cursive letters and older typefaces may return little or no text | The run reports empty OCR honestly; users can supply a typed summary in the archive notes | Add a handwriting-capable provider or a specialist historical-script model; evaluate per script |
| OCR quality depends on provider and language | Low-contrast scans, skew, stains and mixed-script pages reduce accuracy | Pre-process images; try a different provider; the extracted text is editable only through a new upload | Add deskew/contrast preprocessing and per-language provider selection |
| OCR.space free-tier limits (file size, requests) | Large images may be compressed or rejected; repeated uploads can hit quota | The adapter recompresses oversized images where supported; quota errors are surfaced | Use a paid tier or Google Cloud Vision in deployment |
| Google Cloud Vision is mocked-only here | The GCP path is not proven against a live project in this workspace | OCR.space is the live-verified provider; GCP setup is documented but unexecuted | Run a controlled live test with a GCP project and service account |
| OCR is optional | A deployment without an OCR provider gets no text from images/scans | The UI shows an explicit warning and `partial` status | Treat OCR as required in production configuration checks |
| Region/page detail varies by provider | Word boxes are not shown in the UI | Page numbers are preserved for archive documents; OCR text is shown as a block | Add word/region overlays for inscriptions |

## 2. AI and vision

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| Vision identification is a candidate, not a fact | A monument may be misidentified, especially with cropped or unusual images | The UI labels the reading as a candidate and lists alternatives/uncertainty | Require a second provider or human confirmation before writing the identity |
| Provider availability and rate limits | Analysis can fail or slow down | Failures are explicit; retries/backoff exist for sources and some provider calls | Add a provider fallback chain and queued retries |
| No generative storyteller | Story Q&A only retrieves stored text; it cannot compose an answer to an uncovered question | It says the library does not cover the question | Add a grounded-generation layer with citation enforcement, behind a reviewed corpus |
| No entity-level story graph | Stories link to site, sources, versions and related stories, but not to person/deity/community nodes | Users can open source links and related stories | Curate entity links with evidence, then render them in the graph |
| Adaption synthesis is inert for the available key | The optional synthesis path is not demonstrated | Explanations remain extractive quotations | Configure a hosted model and re-enable the existing guarded path |

## 3. Historical sources

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| Mostly secondary sources (Wikipedia, UNESCO) | Depth varies; some local histories are absent | Sources are cited and labelled; the limitation list is shown with the briefing | Add ASI reports, museum catalogues and regional archives |
| Source coverage is uneven across India | Famous sites are well covered; smaller sites may return little | The UI reports the retrieved count and limitations instead of inventing content | Curated ingestion of state archives and regional publications |
| Conflicting accounts | A single “official” narrative can be misleading | Stories keep alternative versions separate and label them | Extend version-level comparisons and source criticism |
| Live source rate limits | Heavy demo use can be throttled by Wikimedia | A descriptive User-Agent with contact is configured; failures degrade visibly | Cache public responses and add a shared rate limiter |
| No archival primary-document OCR pipeline for external archives | User-uploaded documents are processed, but external archives are not crawled | Users upload their own material | Add connectors for open archive APIs with licence checks |

## 4. Heritage map

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| Coordinates are published figures marked `approximate` | Marker placement is not field-survey accurate | Coordinate source and accuracy are shown; no false precision | Field-survey or authoritative GIS coordinates |
| 67 curated records, not a complete national inventory | Coverage is representative, not exhaustive | The UI calls it a curated demonstration dataset and shows real counts | Expand the dataset with sourced records per state/UT |
| No road routing or travel times | The route builder is a straight-line estimate only | The result is labelled “Approximate heritage route”; distance method is returned | Integrate a routing provider and surface road distance/time |
| OSM tile availability | Map tiles can fail behind a firewall or if OSM is unreachable | The rest of the page still works; markers remain | Self-host tiles or use a commercial tile provider |
| Client-side grid clustering | Dense regions show large clusters at low zoom | Zooming in separates sites | Use a proper marker-cluster plugin or server-side clustering |
| No map screenshots of every state | Visual verification is representative | Filter and marker behaviour is covered by tests and a manual pass | Add visual regression coverage |

## 5. Knowledge graph

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| Graph is limited to extracted entities and persisted relationships | It is not a general historical knowledge graph | Evidence panel shows exactly what supports each edge | Ingest curated entity/relationship data with provenance |
| Entity disambiguation is basic | “Patan” or a common name may refer to multiple places/people | The graph shows the extracted label and evidence; users can use search | Add authority records (Wikidata QIDs) and disambiguation UI |
| No graph scaling beyond a single analysis | Very large archives are not rendered as one graph | Archive pages use search/timeline instead | Add subgraph queries and progressive loading |
| Story entities are absent | Stories cannot be explored as graph nodes | Related stories and source links remain available | See §2 |

## 6. Stories and content

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| 57 of 75 stories are unverified summaries | They were written from the site record and Wikipedia, and no person has checked them yet | They are marked `unverified` in the story data | Have a person check each story against its source |
| English-only curated text | Indian-language originals are not displayed | The schema/contribution form accept 14 language tags, and community submissions preserve their declared language | Add original-language seed stories and separately stored translations |
| No automated discovery/crawling | New stories require manual curation | The first 18 stories are source-checked by hand; the other 57 are marked unverified | Add a bounded, licensed discovery pipeline with human review |
| No story images | Story cards use a category tile | Images are not faked | Add licensed images with attribution and alt text |
| Moderation allowlist empty by default | Submitted stories stay pending forever in a default install | The UI explains the review state | Configure `STORY_MODERATOR_EMAILS`; add a review screen |
| Publication does not equal verification | An approved community account can be misread as proven history | The label says “community account”; the notice explains it | Keep the distinction prominent and add reviewer notes |

## 7. Database and storage

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| SQLite is the local default | Single-writer, no concurrent production scale | PostgreSQL is supported by config/driver/migrations | Use PostgreSQL in deployment |
| Local SQLite contains development users/runs | Copying it would leak local data | It is git-ignored and never included in docs or screenshots | Keep it out of releases; use seeded data only |
| No automated backup policy | Data loss risk in production | Documented as a deployment requirement | Managed database backups + restore drills |
| Uploaded originals live on local disk | Not shared across instances; needs a persistent volume | `HG_STORAGE_DIR` is configurable | Object storage (S3-compatible) with signed access |
| No data-retention/deletion workflow | Users cannot bulk-delete their history | Individual archive/file deletion exists | Add account export/delete and retention policy |

## 8. Deployment

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| Free hosting | Demo sleeps when idle; accounts, history and uploads reset on restart | Curated map and stories are re-seeded on every start | Paid hosting with PostgreSQL and a persistent disk |
| Single container | API and website run in one Docker image on Render | A `Dockerfile` is in the repository | Split services and add a worker queue |
| Single-process background tasks | Long archive jobs die if the process restarts | Status is persisted; reprocess is available | Durable queue (Celery/RQ/Arq) and workers |
| No HTTPS termination in-repo | Secure cookies depend on deployment | `TRUST_PROXY_HEADERS` + reverse proxy documented | Terminate TLS at the proxy/load balancer |
| Google Vision credentials not configured | GCP OCR unavailable in this workspace | OCR.space is live; GCP setup documented | Add a service account/ADC in deployment |
| No global rate limiting/WAF | Public endpoints could be abused | Upload limits and auth rate limits exist | Edge rate limiting and request quotas |

## 9. Testing

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| Live Google Vision test absent | GCP OCR is unverified live | Mocked contract tests + documented setup | Run the opt-in live test with credentials |
| Most provider/source tests are mocked | Real-world drift is possible | Opt-in live suites exist (`HERITAGE_LIVE_*`) | Run live suites on a schedule |
| No coverage measurement | Coverage is unknown | Test counts and suites are reported | Add coverage reporting |
| No lint configuration | Style is enforced by review only | TypeScript strict mode + tests | Add ESLint/Ruff if the team wants it |
| CI has not run on GitHub | Workflow correctness is unproven remotely | Commands pass locally; YAML reviewed | Push to a remote and observe the first run (owner action) |
| Browser checks are manual/opt-in | No cross-browser CI | CDP script for Chrome/Edge/Brave | Add Playwright CI with a seeded backend |
| No load/performance test | Concurrency limits are estimated | Documented scale limits | Add k6/Locust profiles |

## 10. Security and accounts

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| No MFA | Account takeover risk if a password is reused | Strong PBKDF2 hashing + rate-limited login | Add TOTP/WebAuthn |
| No email verification or password reset | Users cannot recover an account; emails are unverified | Accounts are local/demo; documented | Add email delivery and verification |
| No CSRF token | Relies on same-origin checks + SameSite=Lax | Tests cover origin rejection | Add double-submit tokens if the architecture changes |
| No audit log for admin/moderation | Moderation actions are stored on the row but not in a separate audit trail | Story status/updated fields exist | Add an append-only moderation log |
| Session tokens are bearer cookies | Theft of the cookie grants access | HttpOnly, SameSite=Lax, Secure when HTTPS; session expiry | Add device binding/rotation and shorter lifetimes |

## 11. Browser and accessibility

| Limitation | Impact | Current workaround | Possible improvement |
|---|---|---|---|
| No automated accessibility audit | Some controls may fall short of WCAG | Semantic roles/labels are used; keyboard paths exist | Run axe and fix findings |
| Map and graph are complex interactions | Screen-reader support is partial | Text lists/source panels provide alternatives | Add table/list views for graph and map data |
| Small screens | The map is usable but dense | Layout stacks; no horizontal overflow in the verified viewport | Further mobile-specific map controls |

## 12. Scope notes

These limitations do **not** mean the features are absent; they describe the
boundary of what was implemented and verified. The README distinguishes
implemented, partially implemented and future work, and the submission
checklist records the current status of each item.
