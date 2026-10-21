# cp-core API (for cp-web)

The JSON API that cp-web (React) uses. Served by cp-core (Django) on the same domain as the
frontend, under `/api/`. This document is the contract; the machine-readable companion is
[`openapi.json`](openapi.json) (live at `/api/openapi.json`, interactive at `/api/docs`). It describes
every request and every response: each status with its schema and an example, each endpoint's error
codes in its description. A test checks every real response against it, so it does not drift.
TypeScript types can be generated from it: `npx openapi-typescript https://cleverphotos.org/api/openapi.json -o src/api/schema.d.ts`
(behind the preview login: download the file first, or use `docs/openapi.json` from the repo).

Last updated: 2026-10-21 · API version 0.3 (shop with iyzico §3.7; ÖBF + MSS sales documents; KVKK as HTML §3.3)

> **For an AI coding assistant working in cp-web:** read this whole file, then the
> "Changes from cp-web's draft contract" section, which lists every place where this API
> differs from `cp-web/docs/api/*.md` and the current MSW mocks. Update `src/features/*/api/*`
> and `src/mocks/handlers.ts` to match. Do not keep `accessToken`, `match-jobs`, or the
> event-scoped selfie/consent paths. For the organizer screens: §3.6, §3.7, §3.8 and §5b. Multi-photo download: §3.5 `download.zip`.
>
> **Changed on 2026-10-21:** KVKK documents are HTML pages, not PDFs (`/api/kvkk/kvkk_tr_002.html`, §3.3). Buying needs
> the two sales documents: checkboxes + popups from `GET /api/shop/obf` / `GET /api/shop/mss`, and `documentsVersion`,
> `acceptedObf`, `acceptedMss` in `POST /api/shop/purchases`; each purchase has `documentsUrl` (§3.7). These HTML pages
> may be shown in an `iframe` (same origin).

## 1. Basics

| Topic | Rule |
| --- | --- |
| Origin | Same origin as the frontend: `fetch("/api/...")`. No CORS, no absolute URLs. |
| Auth | Django session cookie (`sessionid`, HttpOnly), set by OTP verification. The browser sends it automatically. There is **no access token**. |
| CSRF | Every `POST`, `PATCH`, `PUT`, `DELETE` to a signed-in endpoint must send header `X-CSRFToken: <value of the csrftoken cookie>`. Read the cookie at request time; signing in rotates it. |
| JSON | camelCase field names, UTF-8. Timestamps ISO 8601 with offset (`2026-10-19T08:30:00+00:00`); dates `YYYY-MM-DD`. |
| IDs | Always strings. Photo ids are the photo's number in its event, zero-padded: `"00017"`. Unique only within an event. |
| Event codes | 5 characters, case-insensitive, digits 2–9 and letters without i/l/o. The API returns them uppercase (`"RK238"`); send them in any case. |
| Locale | Messages (`message`) are Turkish. The KVKK document exists in `tr` and `en`. |

### Recommended fetch helper

```ts
const csrf = () => document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ?? "";

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    method,
    headers: {
      ...(body && !isForm ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRFToken": csrf() } : {}),
    },
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const json = await res.json();
  if (!json.success) throw { status: res.status, ...json };   // {code, message, errors?}
  return json.data as T;
}
```

On app start call `GET /api/auth/csrf` once, so the `csrftoken` cookie exists before the first POST.

## 2. Envelope and errors

Every response except files and `204` is:

```json
{ "success": true,  "code": "event_retrieved", "message": "", "data": { }, "requestId": "req_3dc994887c3ed300" }
{ "success": false, "code": "event_not_found", "message": "Etkinlik bulunamadı.", "data": null, "requestId": "req_…" }
```

- The HTTP status is the real result; `code` is the stable machine-readable reason; `message` is a
  human-readable Turkish text (safe to show for errors; never toast success messages).
- Validation errors (`422 validation_error`) add `"errors": {"phoneNumber": ["Field required"]}` (keys are request field names).
- `requestId` is also in the `X-Request-ID` response header; show it in support/error screens.

| HTTP | `code` | When |
| --- | --- | --- |
| 400 | `bad_request` | Body is not valid JSON |
| 401 | `authentication_required` | Not signed in (signed-in endpoint) |
| 403 | `csrf_failed` | Missing/wrong `X-CSRFToken` on POST/PATCH/DELETE |
| 403 | `consent_required` | Event needs consent and the user has not given it; selfie upload without consent |
| 403 | `browse_all_not_allowed` | `view=all` on an event that doesn't allow it |
| 403 | `organizer_access_required` | Organizer or shop endpoint, account without the organizer role |
| 404 | `event_not_found`, `photo_not_found`, `selfie_not_found`, `not_found` | Missing, or not visible to this user (never revealed which) |
| 409 | `email_already_in_use` | `PATCH /me` with another account's email |
| 409 | `consent_version_outdated` | Consent sent for a KVKK version that is no longer current |
| 409 | `pricing_changed` | Purchase with an old `pricingVersion` |
| 410 | `event_expired`, `otp_expired` | |
| 422 | `billing_profile_required` | Purchase before the billing profile is complete (§3.8) |
| 422 | `validation_error`, `invalid_phone_number`, `invalid_role`, `invalid_otp`, `explicit_consent_required`, `unsupported_file_type`, `unreadable_image`, `file_too_large`, `file_required`, `invalid_slot`, `invalid_event_settings`, `too_many_files` | |
| 423 | `event_access_closed` | The organizer closed access |
| 429 | `rate_limit_exceeded`, `download_limit_reached` | |
| 500 | `internal_error` | Bug; quote the `requestId` |
| 502 | `payment_provider_error`, `payment_provider_unreachable` | Purchase: iyzico refused the request or can't be reached |

## 3. Endpoints

### 3.1 Session and sign-in

One flow for login and registration: verifying a phone signs in, or creates the account if the
phone is new. There is no separate register screen and no password.

**`GET /api/auth/csrf`** → `200 csrf_set`, `data: null`. Sets the `csrftoken` cookie.

**`POST /api/auth/otp-requests`** (no session needed)

```json
{ "phoneNumber": "+905551234567", "eventCode": "RK238", "role": "participant" }
```

- `phoneNumber`: E.164 (spaces/dashes are stripped).
- `eventCode`: optional; send it when the user came from an event link/QR. Unknown code → `404 event_not_found`.
- `role`: optional, default `"participant"`. The organizer entry page sends `"organizer"` (later `"photographer"`). It is the role the account gains when the code is verified.

`201 otp_sent`:

```json
{ "challengeId": "otp_WcM7QQ-RIA9d2osCR_Mx6A", "maskedPhone": "•••••••••4567", "expiresInSeconds": 120, "resendAfterSeconds": 30 }
```

Errors: `422 invalid_phone_number`, `422 invalid_role`, `429 rate_limit_exceeded` (max 3 codes per phone per 15 min, and not within 30 s of the previous one).

> **No SMS is sent yet.** In development the code is written to the server log and shown in the
> admin (Accounts → Otp challenges → dev code). Ask Ozgur for it, or use the admin.

**`POST /api/auth/otp-verifications`** (no session needed)

```json
{ "challengeId": "otp_WcM7QQ-RIA9d2osCR_Mx6A", "code": "472398" }
```

The event context comes from the challenge; `eventCode` in this body is not needed (ignored).

`200 phone_verified` and the session cookie is set:

```json
{
  "user": { "id": "69", "phoneNumber": "+905551234567", "fullName": null, "email": null,
            "profileCompleted": false, "accountRoles": ["participant"], "hasConsent": false,
            "consentVersion": null,
            "kvkk": { "version": "002", "urls": { "tr": "/api/kvkk/kvkk_tr_002.html", "en": "/api/kvkk/kvkk_en_002.html" } },
            "selfieState": "no_selfie", "selfieThumbnailUrl": null },
  "nextStep": "consent",
  "isNewUser": true,
  "eventCode": "RK238",
  "eventMembership": { "eventCode": "RK238", "role": "participant" }
}
```

`nextStep`:

| Value | Meaning |
| --- | --- |
| `consent` | Event context; the user has no consent yet → consent screen |
| `selfie` | Event context; consent given, no usable selfie → selfie screen |
| `gallery` | Event context; ready → event gallery |
| `organizer_home` | No event context; the account is an organizer |
| `account_home` | No event context; anyone else |

Errors: `422 invalid_otp` (wrong code, unknown or already used challenge), `410 otp_expired`,
`429 rate_limit_exceeded` (5 wrong codes on one challenge; request a new code).

**`DELETE /api/auth/session`** → `204`. Signs out.

### 3.2 The signed-in user

**`GET /api/me`** → `200 profile_retrieved`, the `user` object above. `401` when signed out (use this on app start to know whether a session exists).

| Field | Notes |
| --- | --- |
| `accountRoles` | Any of `participant`, `organizer`, `photographer`, `admin`. UI visibility only; the server checks everything. `participant` = has joined at least one event. |
| `hasConsent` | KVKK explicit consent given **to the current document version**. A new version makes it `false` for everyone until they consent again. |
| `consentVersion` | The version the user last consented to (`null` = never). Differs from `kvkk.version` when the text was updated since. |
| `kvkk` | The current KVKK document: `version` and one HTML page URL per language (`tr`, `en`). Show these on the consent screen. |
| `selfieState` | `no_selfie` · `selfie_processing` · `selfie_rejected` · `ready` (see 3.4) |
| `profileCompleted` | `true` when `fullName` is set |
| `selfieThumbnailUrl` | The primary selfie's thumbnail, or `null` |

**`PATCH /api/me`** `{ "fullName": "Seko Konac", "email": "seko@example.com" }` → `200 profile_updated`, user object.
`fullName` required (max 150); `email` optional (`null` clears it). Errors: `422 validation_error`, `409 email_already_in_use`.

**`GET /api/me/events`** → `200 user_events_retrieved`: events the user has opened (newest activity first).

```json
{ "events": [ {
    "code": "RK238", "name": "Derya & Can", "type": "wedding", "date": "2026-06-12",
    "locationLabel": "İstanbul", "status": "active",
    "matchedPhotoCount": 24, "matching": "ready",
    "coverThumbnailUrl": null, "lastActivityAt": "2026-10-11T08:30:00+00:00", "membershipRole": "participant" } ],
  "total": 1 }
```

`status`: `active` · `access_closed` · `expired`. `matching`: same values as the gallery's (3.5). `coverThumbnailUrl` is always `null` for now.

### 3.3 Consent

The KVKK notice and explicit consent text is an HTML page, one per language, versioned as a pair
(`kvkk_tr_002.html` + `kvkk_en_002.html`; no PDFs). The current version and its URLs are in the user object
(`user.kvkk`, from `GET /api/me` or the OTP verification).

**`GET /api/kvkk/{file}`** (no session needed) → the page (`text/html`), e.g. `/api/kvkk/kvkk_tr_002.html`. Show it in
an `iframe` or a new tab. Older versions stay available. Unknown file → `404 not_found`.

**`POST /api/me/consent`** `{ "granted": true, "version": "002", "locale": "tr" }` → `201 consent_recorded` `{ "hasConsent": true, "version": "002" }`.

- `version`: required, the `user.kvkk.version` the screen showed. If the document was updated meanwhile →
  `409 consent_version_outdated`: re-fetch `GET /api/me` and show the new text.
- `locale`: the language the user read, `tr` (default) or `en`. Recorded with the consent.
- `granted: false` → `422 explicit_consent_required`.

Consent belongs to the account (not to an event) and is asked once per document version. Each consent is
recorded with the version, language, the file's SHA-256, IP and browser (user agent). There is no
withdrawal; deleting the account is the way out.

### 3.4 Selfies (the user's face profile)

Up to two selfies: slot `primary` (required for matching) and `secondary` (optional, recommended).
Uploading to a slot replaces that slot's selfie. Processing is **asynchronous** (usually under 2 s):
upload, then poll `GET /api/me/selfies` (every 1–2 s) until the selfie's `status` leaves `processing`.

**`GET /api/me/selfies`** → `200 selfies_retrieved`:

```json
{ "selfies": [ { "id": "68", "slot": "primary", "status": "ready", "reason": null,
                 "thumbnailUrl": "/api/me/selfies/68/thumb.jpg", "createdAt": "2026-10-19T08:30:00+00:00" } ],
  "selfieState": "ready" }
```

| `status` | `reason` | Show |
| --- | --- | --- |
| `processing` | `null` | spinner |
| `ready` | `null` | ok |
| `rejected` | `no_face` | "No face found, try again" |
| `rejected` | `multiple_faces` | "Only you in the photo" |
| `rejected` | `unreadable` | "Could not read the photo" |

**`POST /api/me/selfies`** — `multipart/form-data`: `file` (JPEG/PNG/WebP/HEIC, ≤ 50 MB), `slot` (`primary`|`secondary`, default `primary`).
→ `201 selfie_accepted`, one selfie object with `status: "processing"`.
Errors: `403 consent_required`, `422 unsupported_file_type | unreadable_image | file_too_large | invalid_slot`, `422 validation_error` (no file).

**`DELETE /api/me/selfies/{id}`** → `204`. Deletes the selfie and its face data.

**`GET /api/me/selfies/{id}/thumb.jpg`** → JPEG (owner only). Use as `<img src>`.

### 3.5 Events and gallery

**`GET /api/public/events/{code}`** (no session needed; the welcome screen) → `200 event_retrieved`:

```json
{ "code": "RK238", "name": "Derya & Can", "type": "wedding", "date": "2026-06-12", "locationLabel": "İstanbul",
  "status": "active", "hasFaceSearch": true, "allowsBrowseAll": false, "requiresConsent": true }
```

Errors: `404 event_not_found`, `410 event_expired`, `423 event_access_closed`, `429 rate_limit_exceeded` (30 lookups/min per IP).
No photos or counts are ever returned here. If a session exists, this call also adds the event to the user's events.

**`GET /api/events/{code}/gallery?view=mine&cursor=0&limit=100`** → `200 gallery_retrieved`

- `view=mine` (default): photos the user appears in, by face matching. `view=all`: every photo; only when `canBrowseAll`.
- Ordered by capture time. `cursor` is an offset; `nextCursor` is `null` on the last page. `limit` ≤ 500.
- Calling it adds the event to the user's events (any signed-in event access does).

```json
{ "eventCode": "RK238", "view": "mine", "matching": "ready", "canBrowseAll": false,
  "photosTotal": 3000, "photosPending": 120, "total": 24, "nextCursor": null,
  "photos": [ { "id": "00017", "width": 1600, "height": 1200, "takenAt": "2026-06-12T18:14:00+00:00",
                "thumbnailUrl": "/api/events/rk238/photos/00017/thumb.jpg",
                "displayUrl": "/api/events/rk238/photos/00017/display.jpg",
                "downloadUrl": "/api/events/rk238/photos/00017/download" } ] }
```

`matching` explains an empty "mine" list:

| `matching` | Meaning | UI |
| --- | --- | --- |
| `ready` | Matching ran; `photos` are the results (may be empty: no photos of you yet) | grid; if `photosPending > 0`, "N photos still processing" and re-fetch later |
| `no_selfie` | No selfie yet | link to selfie upload |
| `selfie_processing` | Selfie being processed | poll selfies, then re-fetch |
| `selfie_rejected` | No usable selfie | ask for a new selfie |
| `consent_required` | Event lets the user in without consent (browse-all only); no face search for them | offer consent |
| `matching_off` | The event has no face search | use `view=all` |

`width`/`height` are the display image's pixels (for layout before the image loads).
`photosTotal`/`photosPending`: all photos of the event and how many are still being processed.
Re-fetching is cheap: new photos and selfies show up on the next call (nothing to start or poll besides this).

Errors: `401`, `403 consent_required`, `403 browse_all_not_allowed`, `404 event_not_found`, `410 event_expired`, `423 event_access_closed`.

**`GET /api/events/{code}/gallery/photos/{photoId}?view=mine`** → `200 gallery_photo_retrieved`:

```json
{ "eventCode": "RK238", "view": "mine", "photo": { …same photo object… },
  "position": 4, "total": 24, "previousPhotoId": "00012", "nextPhotoId": "00031", "downloadsLeftToday": 3 }
```

Navigation follows the same view. A photo outside the view → `404 photo_not_found`.

**Files** (use directly as `<img src>` / `<a href download>`; session cookie required):

| URL | What |
| --- | --- |
| `GET /api/events/{code}/photos/{photoId}/thumb.jpg` | ~400 px, for grids |
| `GET /api/events/{code}/photos/{photoId}/display.jpg` | ~1600 px, for viewing |
| `GET /api/events/{code}/photos/{photoId}/download` | full size (≤ 4000 px), `Content-Disposition: attachment; filename="cleverphotos_rk238_00017.jpg"` |

**`GET /api/events/{code}/download.zip?view=mine&photos=00012,00017`** (or `&all=true` for the whole view): several
photos at once, full size, as one ZIP (`cleverphotos_rk238_mine.zip`; inside, the same names as single downloads).
Use it as a plain link (`<a href>` / `window.location`), not `fetch`: the browser streams it to disk. Max **50** photos per
request (server setting); split larger selections into several links. Each photo counts against the per-photo cap below;
photos outside the view or already at the cap are left out, and the response headers `X-Photos-Included` /
`X-Photos-Skipped` tell how many. Errors (JSON): `422 validation_error` (no photos given), `422 too_many_files`,
`404 photo_not_found` (none visible), `429 download_limit_reached` (all at the cap), plus the event errors above.

Downloads are logged and capped: the same user may download the same photo **3 times per 24 h**;
the 4th answers `429 download_limit_reached` (JSON envelope). Show `downloadsLeftToday` from the photo detail and hide the button at 0.
A photo the user may not see answers `404 photo_not_found`.

### 3.6 Organizer

For accounts with the `organizer` role (`accountRoles` contains `organizer`; the account gets it by
signing in with `"role": "organizer"`). Anyone else gets `403 organizer_access_required`. Events are
addressed by their **code** (`/api/organizer/events/RK238`, any case); `id` in the event object is the
same code. An organizer sees only their own events (another organizer's code answers `404 event_not_found`).

Events have three states: `active` (open; created this way, there is no draft), `access_closed`
(participants locked out; uploads and processing continue) and `expired` (final; set by an admin).
Credits: **1 credit per accepted photo**, charged at upload. Creating an event, the cover, and deleting
are free; deleting refunds nothing. `creditBalance` is the real balance and may go negative: the server
allows an overdraft down to a floor, so a balance of −12 is shown as −12.

**The event object** (`event` in the event endpoints, items of `events` in the lists):

```json
{ "id": "RK238", "code": "RK238", "name": "Derya & Can", "type": "wedding", "date": "2026-06-12",
  "city": "İstanbul", "venue": "Çırağan Sarayı", "status": "active",
  "hasFaceSearch": true, "allowsBrowseAll": false, "requiresConsent": true,
  "photoCount": 684, "processedPhotoCount": 680, "pendingPhotoCount": 4, "failedPhotoCount": 0,
  "participantCount": 213,
  "coverThumbnailUrl": "/api/organizer/events/RK238/cover/thumb.jpg", "coverIsUploaded": true,
  "joinUrl": "https://cleverphotos.org/?event=RK238", "createdAt": "2026-10-19T08:30:00+00:00" }
```

| Field | Notes |
| --- | --- |
| `type` | `wedding`, `sports`, `graduation`, `corporate`, `other` |
| `hasFaceSearch` | Photos go to face matching ("photos of me"). Off: participants only browse all |
| `allowsBrowseAll` | Participants may browse all photos. Must be `true` when `hasFaceSearch` is `false` |
| `requiresConsent` | Participants need KVKK consent to enter (default `true`) |
| `processedPhotoCount` / `pendingPhotoCount` / `failedPhotoCount` | Face processing: finished / waiting or running / failed |
| `participantCount` | People who opened the event (the organizer not counted) |
| `coverThumbnailUrl` | The uploaded cover; without one, the first photo; `null` when neither exists. `coverIsUploaded` tells which |
| `joinUrl` | The link for participants; put it in the QR code |

**`GET /api/organizer/dashboard`** → `200 organizer_dashboard_retrieved`:
`{ organizer: {displayName, creditBalance}, totals: {activeEvents, totalPhotos, totalParticipants}, events: [event…], recentActivity: [] }`.
`events`: the ones that are not expired, newest first. `recentActivity` is always empty in this version.

**`GET /api/organizer/events?includeExpired=false`** → `200 organizer_events_retrieved` `{ events: [event…], total }`.

**`POST /api/organizer/events`** — `multipart/form-data`: `name` (required), `date` (required, `YYYY-MM-DD`),
`type` (default `wedding`), `city`, `venue`, `hasFaceSearch` (default `true`), `allowsBrowseAll` (default `false`),
`requiresConsent` (default `true`), `cover` (optional image file).
→ `201 organizer_event_created` `{ event }`. The event is active at once and its code is generated.
Errors: `422 validation_error`, `invalid_event_settings` (face search off without browse-all), and for the cover
`unsupported_file_type`, `unreadable_image`, `file_too_large`.

**`GET /api/organizer/events/{code}`** → `200 organizer_event_retrieved` `{ event }`.

**`PATCH /api/organizer/events/{code}`** — JSON, any subset of `name`, `type`, `date`, `city`, `venue`,
`hasFaceSearch`, `allowsBrowseAll`, `requiresConsent`, `status` (`active` | `access_closed`: open/close access).
→ `200 organizer_event_updated` `{ event }`. Errors: `410 event_expired`, `422 validation_error`, `invalid_event_settings`.

**Cover.** `POST /api/organizer/events/{code}/cover` — `multipart/form-data`: `file`. Free, not face-processed,
not in the gallery; replaces the previous uploaded cover. `DELETE /api/organizer/events/{code}/cover` removes it
(the first photo is shown instead). Both → `200 organizer_cover_updated` `{ event }`.
Files: `GET …/cover/thumb.jpg` (~400 px), `GET …/cover/display.jpg` (~1600 px).

**`POST /api/organizer/events/{code}/photo-upload-check`** `{ "photoCount": 240 }` → `200 photo_upload_eligibility_checked`:

```json
{ "eligible": false, "requiredCredits": 240, "creditBalance": 180, "remainingCredits": null, "missingCredits": 10 }
```

Not enough credit is not an error: `eligible: false` and `missingCredits` (credits to buy first). `remainingCredits`
is the balance after the upload when eligible (may be negative within the overdraft). The upload checks every
photo again. Errors: `410 event_expired`.

**`POST /api/organizer/events/{code}/photos`** — `multipart/form-data`: repeated `files` (max **20** per request,
≤ 50 MB each, **the whole request ≤ 60 MB**) and optionally the same number of repeated `clientIds`, echoed back.
Send large selections as several requests of a few files each (2 in parallel is plenty).
→ `200 organizer_photos_uploaded`:

```json
{ "acceptedCount": 2, "rejectedCount": 1, "creditBalance": 178,
  "results": [
    { "clientId": "local-1", "photoId": "00045", "fileName": "00045.jpg", "status": "queued", "errorCode": null },
    { "clientId": "local-2", "photoId": "00046", "fileName": "00046.jpg", "status": "queued", "errorCode": null },
    { "clientId": "local-3", "photoId": null, "fileName": null, "status": "rejected", "errorCode": "duplicate_photo" } ] }
```

Each file is accepted or rejected on its own. `status`: `queued` (waiting for face processing), `ready`
(event without face search), `rejected`. `fileName` is our name; the uploaded file name is not kept.
Per-file `errorCode`: `unsupported_file_type`, `unreadable_image`, `file_too_large`, `duplicate_photo`
(same file already in this event, not charged), `insufficient_credits` (balance at the floor: buy credits).
Errors for the whole request: `410 event_expired`, `422 too_many_files`, `422 validation_error` (clientIds count).

**`GET /api/organizer/events/{code}/photos?status=&cursor=0&limit=100`** → `200 organizer_photos_retrieved`:

```json
{ "photos": [ { "id": "00017", "fileName": "00017.jpg", "status": "ready", "faceCount": 3,
                "thumbnailUrl": "/api/organizer/events/RK238/photos/00017/thumb.jpg",
                "displayUrl": "/api/organizer/events/RK238/photos/00017/display.jpg",
                "width": 1600, "height": 1200, "takenAt": "2026-06-12T18:14:00+00:00",
                "uploadedAt": "2026-10-18T18:30:00+00:00", "errorCode": null } ],
  "total": 1, "counts": { "queued": 0, "processing": 0, "ready": 1, "failed": 0 }, "nextCursor": null }
```

Upload order. `status` filter: `queued`, `processing`, `ready`, `failed`; `counts` always covers the whole event.
`faceCount` is `null` until processed. `failed` (rare) means face processing gave up on the photo; it stays
in the event and the gallery's "all photos", and we look at it on our side; there is no retry button.
Files: `GET …/photos/{photoId}/thumb.jpg`, `…/display.jpg` (the organizer sees every photo).

**`DELETE /api/organizer/events/{code}/photos`** — JSON `{ "photoIds": ["00017", "00018"] }` →
`200 organizer_photos_deleted` `{ affectedCount, creditBalance }`. Files and face data go at once; no refund.
Unknown ids are skipped.

**`GET /api/organizer/events/{code}/participants`** → `200 organizer_participants_retrieved`:

```json
{ "participants": [ { "membershipId": "123", "displayName": "Ece Yılmaz", "maskedPhone": "•••••••••4218",
                      "joinedAt": "2026-10-18T15:42:00+00:00", "lastActiveAt": "2026-10-18T20:12:00+00:00",
                      "hasConsent": true, "matchStatus": "completed", "matchedPhotoCount": 18 } ],
  "total": 1, "counts": { "completed": 1, "pending": 0, "failed": 0 } }
```

Most recently active first. `matchStatus`: `consent_pending` (no KVKK consent yet) → `selfie_pending` (no selfie)
→ `processing` (selfie being checked) → `completed` (matching runs; `matchedPhotoCount` = photos they are in), or
`failed` (selfie rejected; they need to upload a new one). `counts.pending` = consent_pending + selfie_pending +
processing. No phone numbers, selfies or face data are returned.

### 3.7 Shop

What organizers buy: today every product adds credits to the account (`2000cr` = 2000 credits). One product per
purchase, no cart. The product list is public (landing/pricing pages); purchases need an organizer account
(`401` signed out, `403 organizer_access_required` otherwise). Products are managed in the
admin: name, title, description (subtitle) in Turkish and English, credits, price, currency, on sale, featured, order.
Shown texts come in both languages, `{"tr": "…", "en": "…"}`; pick by the interface language. An empty string means
not filled in (fall back to the other language, or hide it).

**`GET /api/shop/products`** → `200 products_retrieved`:

```json
{ "creditBalance": 1840, "pricingVersion": "3f9a1c0b2d4e",
  "products": [ { "id": "2", "name": { "tr": "2000cr", "en": "2000cr" },
                  "title": { "tr": "Mini paket", "en": "Small pack" },
                  "description": { "tr": "Küçük düğünler için", "en": "For small weddings" },
                  "creditAmount": 2000, "price": 3600.0, "currency": "TRY",
                  "unitPrice": 1.8, "discountPercent": 10, "featured": true } ],
  "documentsVersion": "001" }
```

No session needed. `creditBalance` is `null` when signed out. `products`: the ones on sale, in display order. `discountPercent` is against the product with the highest unit price.
`pricingVersion` changes whenever a product or price changes; send it back with a purchase. `documentsVersion` is the
version of the sales documents below; send it back with a purchase too.

**Sales documents.** Before paying, the buyer must read and accept two texts: the **Ön Bilgilendirme Formu** (ÖBF,
preliminary information form) and the **Mesafeli Satış Sözleşmesi** (MSS, distance sales agreement). Near the pay
button show two checkboxes, unticked, each with a link to its text:

- ☐ [Ön Bilgilendirme Formu](#)'nu okudum ve onaylıyorum. / I have read and accept the [Preliminary Information Form](#).
- ☐ [Mesafeli Satış Sözleşmesi](#)'ni okudum ve onaylıyorum. / I have read and accept the [Distance Sales Agreement](#).

The pay button stays disabled until both are ticked. A link opens the text in a popup (or a new tab) with a TR / EN
switch; the text is an HTML page filled for the signed-in user and the chosen product:

**`GET /api/shop/obf?product=<id>&locale=tr|en`**, **`GET /api/shop/mss?product=<id>&locale=tr|en`** →
`200 text/html` (a complete page; show it in an `iframe` with `src` = the URL, or fetch it and use `srcdoc`). The
order number shows as "–" and the date is now; the stored copy gets both. Errors (JSON): `404 not_found` (product not
on sale), `422 billing_profile_required` (fill the billing profile first: the texts show the buyer's details).

When the purchase is created (below), the four texts (ÖBF and MSS, Turkish and English) are stored with it, with the
time, IP and browser: that is the legal record. The buyer can open it later from `documentsUrl`.

**`GET /api/shop/purchases`** → `200 purchases_retrieved`:

```json
{ "purchases": [ { "id": "7", "productId": "2", "productName": { "tr": "2000cr", "en": "2000cr" },
                   "creditAmount": 2000, "amount": 3600.0,
                   "currency": "TRY", "status": "paid", "purchasedAt": "2026-10-19T08:30:00+00:00",
                   "paidAt": "2026-10-19T08:30:05+00:00", "documentsUrl": "/api/shop/purchases/7/documents" } ],
  "total": 1 }
```

This account's purchases, newest first (max 100). `productName`, `creditAmount` and `amount` are as they were at
purchase time. `status`: `pending` (payment not finished), `paid` (credits added), `failed`. `documentsUrl`: the ÖBF + MSS
the buyer accepted for this purchase (`null` on purchases from before the documents existed); show it as a link
("Sözleşme" / "Agreement") in the purchase history.

**`POST /api/shop/purchases`** `{ "productId": "2", "pricingVersion": "3f9a1c0b2d4e", "documentsVersion": "001",
"acceptedObf": true, "acceptedMss": true }` →
`201 purchase_started` `{ "purchase": {…}, "paymentPageUrl": "https://sandbox-cpp.iyzipay.com?token=…", "creditBalance": 1840 }`.

The payment runs on iyzico's page (card data never reaches us):

1. This call creates the purchase (`status: "pending"`) and returns iyzico's `paymentPageUrl`. Send the browser there
   (`window.location.href = paymentPageUrl`).
2. The organizer pays (or cancels) on iyzico's page.
3. iyzico sends the browser back to us; we ask iyzico for the result, settle the purchase (credits are added when paid)
   and redirect the browser to **`/organizer/credits?purchase=<id>&status=paid|failed|pending`**.
4. On that page, read the result with `GET /api/shop/purchases/{id}` (and refresh the balance). `pending` means we could
   not confirm with iyzico yet; ask again after a few seconds. `status=unknown` (no id) means the return could not be
   matched to a purchase.

On development servers the payment can also be **simulated**: then the purchase is `paid` at once, the credits are
added, and `paymentPageUrl` is `null`; skip the redirect. Handle both: `paymentPageUrl` null → done, else redirect.
Errors: `404 not_found` (product not on sale), `409 pricing_changed` (reload the products), `422 validation_error`,
`422 billing_profile_required` (show the billing form, §3.8, then buy again), `409 documents_changed` (the texts
changed: reload the products, untick both boxes, show the texts again), `422 documents_not_accepted` (a checkbox not
ticked), `502 payment_provider_error` /
`502 payment_provider_unreachable` (iyzico refused the request or can't be reached; `message` says why).
The billing profile at purchase time is kept with the purchase.

**`GET /api/shop/purchases/{id}`** → `200 purchase_retrieved` `{ "purchase": {…}, "creditBalance": 3840 }`: one of this
account's purchases (`404 not_found` otherwise).

**`GET /api/shop/purchases/{id}/documents`** → `200 text/html`: the stored page, ÖBF-TR, MSS-TR, ÖBF-EN, MSS-EN, exactly
as accepted (printable; the browser can save it as PDF). `404 not_found` when it is not this account's purchase or has
no documents.

### 3.8 Billing profile

Who pays, as the payment provider and the invoice need it: an **individual** (TC kimlik no) or a **company** (unvan, vergi
dairesi, VKN). One per account; asked at the first purchase (`422 billing_profile_required`), editable in the profile.
The phone number is the account's.

**`GET /api/me/billing`** → `200 billing_retrieved` `{ "billing": {…} | null, "isComplete": false }` (`null` before the first save).

**`PUT /api/me/billing`** (the whole profile) → `200 billing_saved` `{ "billing": {…}, "isComplete": true }`:

```json
{ "type": "company", "firstName": "Ece", "lastName": "Yılmaz", "identityNumber": "",
  "companyName": "Foto Ltd. Şti.", "taxOffice": "Çankaya", "taxNumber": "1234567890",
  "email": "ece@example.com", "address": "Atatürk Cad. No: 1 D: 2", "district": "Çankaya", "city": "Ankara",
  "postalCode": "06680", "country": "Türkiye" }
```

| Field | Rule |
| --- | --- |
| `type` | `individual` or `company` |
| `firstName`, `lastName` | always; for a company, the contact person |
| `identityNumber` | TC kimlik no, 11 digits with the official checksum; required for individuals, optional for companies |
| `companyName`, `taxOffice`, `taxNumber` | companies only, all required; `taxNumber` = VKN, 10 digits with the official checksum |
| `email`, `address`, `district` (ilçe), `city` (il) | always required |
| `postalCode` | optional; `country` defaults to `Türkiye` |

Errors: `422 validation_error` with `errors` per field, e.g. `{"identityNumber": ["invalid TC kimlik no"], "city": ["required"]}`.

## 4. Flows

**Guest via QR** (`/…?event=RK238` in cp-web):

1. `GET /api/public/events/RK238` → welcome screen (or closed/expired screen).
2. `GET /api/me`: `401` → sign-in; `200` → skip to step 5.
3. `POST /api/auth/otp-requests {phoneNumber, eventCode}` → code screen with countdown.
4. `POST /api/auth/otp-verifications {challengeId, code}` → follow `nextStep`.
5. `consent` → show the page from `user.kvkk.urls[locale]` + `POST /api/me/consent {granted: true, version: user.kvkk.version, locale}`.
6. `selfie` → `POST /api/me/selfies` → poll `GET /api/me/selfies` until `ready` / `rejected`.
7. `gallery` → `GET /api/events/RK238/gallery` (+ `view=all` tab when `canBrowseAll`).

**Organizer sign-in** (organizer entry page): same OTP calls with `"role": "organizer"` and no `eventCode`;
`nextStep` is `organizer_home`. Then `GET /api/organizer/dashboard`; create with `POST /api/organizer/events`;
upload: `photo-upload-check` with the selected count → (not eligible: credit store) → `POST …/photos` in batches →
poll `GET …/photos` (or the event) while `pendingPhotoCount > 0`. Share `joinUrl`.

## 5. Changes from cp-web's draft contract

Compared with `cp-web/docs/api/*.md` and `src/mocks/handlers.ts` as of 2026-10-18:

| # | Draft (cp-web) | cp-core | Action in cp-web |
| --- | --- | --- | --- |
| 1 | `accessToken` in OTP verification | Removed. Session cookie + `X-CSRFToken` | Drop token storage; use the fetch helper (§1); call `GET /api/auth/csrf` on start |
| 2 | OTP request `{eventCode, phoneNumber}` | Adds optional `role` (`participant` default, `organizer`) | Organizer entry sends `role: "organizer"` |
| 3 | OTP verify body has `eventCode` | Not needed (taken from the challenge) | Optional to remove |
| 4 | `expiresInSeconds: 120` | Same; `resendAfterSeconds: 30` | — |
| 5 | `user` = `{id, phoneNumber, profileCompleted, accountRoles}` | Adds `fullName`, `email`, `hasConsent`, `consentVersion`, `kvkk`, `selfieState`, `selfieThumbnailUrl`; `accountRoles` lists `participant`, `organizer`, `photographer`, `admin` | `AccountRole` type: add `participant`, `photographer`, `admin` |
| 6 | `nextStep: consent \| account_home \| organizer_home` | Adds `selfie`, `gallery` | Handle the two new values |
| 7 | `POST /api/events/{code}/biometric-consents {noticeVersion, granted}` | `POST /api/me/consent {granted, version, locale}`; consent is per account and per document version; `409 consent_version_outdated` | Change path/body; skip the consent screen when `user.hasConsent` |
| 8 | `GET /api/legal/biometric-consent/current?locale=` (JSON sections) | Removed. The text is an HTML page per language: `user.kvkk.urls.tr` / `.en` → `GET /api/kvkk/{file}` | Show/link the page instead of rendering sections; drop the notice fetch and its types/mocks |
| 9 | `POST /api/events/{code}/face-profile/uploads` (sync `faceCount`, `face_not_detected`…) | `POST /api/me/selfies` (async); results via `GET /api/me/selfies` `status`/`reason` | Upload, then poll; map `reason` to messages |
| 10 | `PUT /api/me/face-profile` | `POST /api/me/selfies` with `slot=primary` | Same endpoint as #9 |
| 11 | `POST/GET /api/events/{code}/match-jobs` | Removed. The gallery is always current; `matching` + `photosPending` tell the state | Delete match-job calls/types/mocks; after the selfie is `ready`, load the gallery |
| 12 | `GET /api/events/{code}/gallery` | Same path; adds `view`, `matching`, `canBrowseAll`, `photosTotal`, `photosPending`, `displayUrl`; `nextCursor` is an offset | Add the "All photos" tab when `canBrowseAll` |
| 13 | Photo ids `photo_123` | `"00017"` (per event) | Treat as opaque strings |
| 14 | `GET …/gallery/photos/{photoId}` | Same; adds `view` param, `downloadsLeftToday` | Pass the current tab's `view` |
| 15 | `downloadUrl` = CDN URL | Same-origin URL; capped (3/photo/day) | Hide/disable at `downloadsLeftToday: 0` |
| 16 | Public event `{code, name, type, date, locationLabel, status}` | Adds `hasFaceSearch`, `allowsBrowseAll`, `requiresConsent`; codes are 5 chars (`DERYACAN` was a name) | Update fixtures to 5-char codes |
| 17 | `GET /api/me`, `PATCH /api/me` | Same | — |
| 18 | `GET /api/me/events` | Same; adds `matching`; `coverThumbnailUrl` always `null` for now | — |
| 19 | `DELETE /api/auth/session` | Same | — |
| 20 | Error `code` values | See §2 table | Map codes to messages |
| 21 | "Download selected" fires one download per photo (`downloadUrl`, saved as `.svg`) | `GET /api/events/{code}/download.zip?view=…&photos=…` (max 50 per request) | One link per batch of ≤ 50; read `X-Photos-Skipped` if you want to tell the user |
| 22 | Credit purchase without buyer data | `422 billing_profile_required` → `GET`/`PUT /api/me/billing` (§3.8), individual or company | Billing form at the first purchase; editable in the profile |

### 5b. Organizer API vs cp-web's draft (`docs/api/organizer-dashboard.md`, 2026-10-19)

| # | Draft (cp-web) | cp-core | Action in cp-web |
| --- | --- | --- | --- |
| O1 | `eventId` (`evt_123`) in paths | The event **code** (`RK238`); `id` = code in the event object | Use `id` or `code` in paths, as before |
| O2 | Status `draft` / `active` / `archived`; `PUT …/activation {confirmed}`; `activatedAt` | No draft: `active` on create, `access_closed` (organizer closed it), `expired`. No activation endpoint, no `activatedAt` | Drop the activation step/modal; `joinUrl` is always set. Open/close with `PATCH {status}` |
| O3 | `POST /events` returns `{event, nextStep: "event_setup"}` | `{event}` | Ignore `nextStep` |
| O4 | `availableCredits` | `creditBalance` (dashboard, upload check, upload, delete, store); may be negative | Rename; show negative balances as they are |
| O5 | Event detail `matchedPhotoCount` | Not provided; per-participant counts are in the participants list | Drop the stat or sum the participants' counts |
| O6 | Event object without settings | Adds `hasFaceSearch`, `allowsBrowseAll`, `requiresConsent`, `pendingPhotoCount`, `failedPhotoCount`, `coverIsUploaded`, `createdAt`; `PATCH` changes them | Settings tab can use them |
| O7 | Upload result `{clientId, fileName, status: ready\|rejected, errorCode: photo_invalid}` | Adds `photoId`; `fileName` is our name (`00045.jpg`, `null` when rejected); `status` `queued` \| `ready` \| `rejected`; specific `errorCode`s | Map the codes; match results by `clientId`. Max 20 files and 60 MB per request |
| O8 | Photo list `{id, fileName, thumbnailUrl, status, uploadedAt, errorCode}` | Adds `faceCount`, `displayUrl`, `width`, `height`, `takenAt`, `nextCursor`; `?status=` filter, paging | — |
| O9 | `POST …/photos/retry` | Removed: a failed photo is rare and fixed on our side | Remove the retry action |
| O10 | Participants: `joinMethod`, `accessStatus`, `DELETE …/participants/{id}/access` (revoke) | Not in v1 (no revoke); `matchStatus` adds `consent_pending`, drops `matching_pending`; adds `hasConsent`; `counts` has no `active` | Remove revoke and those fields |
| O11 | `GET /api/organizer/credits` → `{availableCredits, pricingVersion, packages, purchases}` | Split (§3.7): `GET /api/shop/products` → `{creditBalance, pricingVersion, products}` and `GET /api/shop/purchases` → `{purchases, total}`. A product adds `name`, `title`, `description`, each `{tr, en}`; amounts from the admin, not fixed to 500/1000/3000 | Change the URLs; `packages` → `products`; type `creditAmount` as `number`; pick texts by language |
| O12 | `POST /api/organizer/credit-purchases {packageId, pricingVersion}` → `{purchase, availableCredits}`, paid at once | `POST /api/shop/purchases {productId, pricingVersion}` → `201 purchase_started` `{purchase, paymentPageUrl, creditBalance}`: with iyzico the purchase is `pending` and the browser goes to `paymentPageUrl`, then comes back to `/organizer/credits?purchase=<id>&status=…`; read it with `GET /api/shop/purchases/{id}`. Simulated: `paid`, `paymentPageUrl` null | Change the URL; `packageId` → `productId`; redirect to `paymentPageUrl` when set; result handling on `/organizer/credits` |
| O13 | Cover: only at create, max 8 MB | Also `POST …/cover` / `DELETE …/cover` any time; cover URL is same-origin | — |
| O14 | Plain `fetch` for POST/PUT/DELETE in `organizer-service.ts` | Every unsafe request needs `X-CSRFToken` | Use `apiMutation` for all of them (today they fail with `403 csrf_failed`) |

## 6. Not yet in this API

Live iyzico payments (sandbox now), emailing the documents after payment, account-based discounts, organizer activity feed, participant access revoke, choosing the cover from
the event's photos, covers for participants (`coverThumbnailUrl` in `/api/me/events`),
real SMS delivery, email login. Ask before building screens that need them.
