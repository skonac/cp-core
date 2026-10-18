# cp-core API (for cp-web)

The JSON API that cp-web (React) uses. Served by cp-core (Django) on the same domain as the
frontend, under `/api/`. This document is the contract; the machine-readable companion is
[`openapi.json`](openapi.json) (request shapes; live at `/api/openapi.json`, interactive at `/api/docs`).

Last updated: 2026-10-19 · API version 0.1

> **For an AI coding assistant working in cp-web:** read this whole file, then the
> "Changes from cp-web's draft contract" section, which lists every place where this API
> differs from `cp-web/docs/api/*.md` and the current MSW mocks. Update `src/features/*/api/*`
> and `src/mocks/handlers.ts` to match. Do not keep `accessToken`, `match-jobs`, or the
> event-scoped selfie/consent paths.

## 1. Basics

| Topic | Rule |
| --- | --- |
| Origin | Same origin as the frontend: `fetch("/api/...")`. No CORS, no absolute URLs. |
| Auth | Django session cookie (`sessionid`, HttpOnly), set by OTP verification. The browser sends it automatically. There is **no access token**. |
| CSRF | Every `POST`, `PATCH`, `PUT`, `DELETE` to a signed-in endpoint must send header `X-CSRFToken: <value of the csrftoken cookie>`. Read the cookie at request time; signing in rotates it. |
| JSON | camelCase field names, UTF-8. Timestamps ISO 8601 with offset (`2026-10-19T08:30:00+00:00`); dates `YYYY-MM-DD`. |
| IDs | Always strings. Photo ids are the photo's number in its event, zero-padded: `"00017"`. Unique only within an event. |
| Event codes | 5 characters, case-insensitive, digits 2–9 and letters without i/l/o. The API returns them uppercase (`"RK238"`); send them in any case. |
| Locale | Messages (`message`) are Turkish. The consent notice takes `?locale=tr|en`. |

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
| 404 | `event_not_found`, `photo_not_found`, `selfie_not_found`, `not_found` | Missing, or not visible to this user (never revealed which) |
| 409 | `email_already_in_use` | `PATCH /me` with another account's email |
| 410 | `event_expired`, `otp_expired` | |
| 422 | `validation_error`, `invalid_phone_number`, `invalid_role`, `invalid_otp`, `explicit_consent_required`, `unsupported_file_type`, `unreadable_image`, `file_too_large`, `file_required`, `invalid_slot` | |
| 423 | `event_access_closed` | The organizer closed access |
| 429 | `rate_limit_exceeded`, `download_limit_reached` | |
| 500 | `internal_error` | Bug; quote the `requestId` |

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
| `hasConsent` | KVKK explicit consent for face processing given. |
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

**`GET /api/legal/biometric-consent/current?locale=tr`** (no session needed) → `200 consent_notice_retrieved`:

```json
{ "id": "biometric-consent", "version": "draft-1", "effectiveAt": "2026-10-01T00:00:00Z",
  "title": "…", "summary": "…", "sections": [ { "id": "purpose", "title": "…", "body": "…" } ] }
```

Draft text until the legal text is approved.

**`POST /api/me/consent`** `{ "granted": true }` → `201 consent_recorded` `{ "hasConsent": true, "noticeVersion": "draft-1" }`.
`granted: false` → `422 explicit_consent_required`. Consent belongs to the account (not to an event) and is
asked once. There is no withdrawal; deleting the account is the way out.

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

Downloads are logged and capped: the same user may download the same photo **3 times per 24 h**;
the 4th answers `429 download_limit_reached` (JSON envelope). Show `downloadsLeftToday` from the photo detail and hide the button at 0.
A photo the user may not see answers `404 photo_not_found`.

## 4. Flows

**Guest via QR** (`/…?event=RK238` in cp-web):

1. `GET /api/public/events/RK238` → welcome screen (or closed/expired screen).
2. `GET /api/me`: `401` → sign-in; `200` → skip to step 5.
3. `POST /api/auth/otp-requests {phoneNumber, eventCode}` → code screen with countdown.
4. `POST /api/auth/otp-verifications {challengeId, code}` → follow `nextStep`.
5. `consent` → `GET /api/legal/biometric-consent/current` + `POST /api/me/consent {granted: true}`.
6. `selfie` → `POST /api/me/selfies` → poll `GET /api/me/selfies` until `ready` / `rejected`.
7. `gallery` → `GET /api/events/RK238/gallery` (+ `view=all` tab when `canBrowseAll`).

**Organizer sign-in** (organizer entry page): same OTP calls with `"role": "organizer"` and no `eventCode`;
`nextStep` is `organizer_home`. The organizer API (events, uploads, credits) is not in this version;
organizers use cp-core's Django pages at `/o/` for now.

## 5. Changes from cp-web's draft contract

Compared with `cp-web/docs/api/*.md` and `src/mocks/handlers.ts` as of 2026-10-18:

| # | Draft (cp-web) | cp-core | Action in cp-web |
| --- | --- | --- | --- |
| 1 | `accessToken` in OTP verification | Removed. Session cookie + `X-CSRFToken` | Drop token storage; use the fetch helper (§1); call `GET /api/auth/csrf` on start |
| 2 | OTP request `{eventCode, phoneNumber}` | Adds optional `role` (`participant` default, `organizer`) | Organizer entry sends `role: "organizer"` |
| 3 | OTP verify body has `eventCode` | Not needed (taken from the challenge) | Optional to remove |
| 4 | `expiresInSeconds: 120` | Same; `resendAfterSeconds: 30` | — |
| 5 | `user` = `{id, phoneNumber, profileCompleted, accountRoles}` | Adds `fullName`, `email`, `hasConsent`, `selfieState`, `selfieThumbnailUrl`; `accountRoles` lists `participant`, `organizer`, `photographer`, `admin` | `AccountRole` type: add `participant`, `photographer`, `admin` |
| 6 | `nextStep: consent \| account_home \| organizer_home` | Adds `selfie`, `gallery` | Handle the two new values |
| 7 | `POST /api/events/{code}/biometric-consents {noticeVersion, granted}` | `POST /api/me/consent {granted}`; consent is per account | Change path/body; skip the consent screen when `user.hasConsent` |
| 8 | `GET /api/legal/biometric-consent/current?locale=` | Same | — |
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

## 6. Not yet in this API

Organizer endpoints (create event, upload, credits), ZIP download of several photos, event cover
images, real SMS delivery, email login. Ask before building screens that need them.
