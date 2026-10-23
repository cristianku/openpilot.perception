# The app API of perception-web

perception-web (`web/server.py`) is the site of openpilot.perception and the API that **Perception for Android**
(`app/android`, `ch.bodhi.perception`) works with. The app knows only the address of the site, signs in there as
the administrator and sends its radar tests through the requests below. The app's side of this contract is
`app/android/app/src/main/java/ch/bodhi/perception/SiteApi.kt` (the source of truth for the field names); the
site's side is `web/app_api.py`, `web/radar.py` (`app_test_of`, `RadarTests.add_from_app`) and `web/comma.py`
(`public_key_line`, `Comma.add_ssh_key`, `Comma.host_key_fingerprints`). The app updates on its own (Google Play):
a phone can have an app newer or older than the site it talks to. Keep this contract stable.

## Rules

- JSON over HTTP on the address and port of the site (default 8767; on the LAN or ZeroTier, or HTTPS behind a
  reverse proxy).
- **Session**: `POST /api/auth/login` answers with a cookie `perception_web_<port>` (HttpOnly, SameSite=Strict,
  Secure behind HTTPS, 7 days without use, 30 days in all), the same session as the page. Every other call sends
  it back in `Cookie`. The app keeps it in the cookie store of its WebView, so its Site tab is signed in too.
- **POST**: `Content-Type: application/json` with a `Content-Length`, at most 64 KiB, one JSON object (`{}` when
  there is nothing to send), without NaN or repeated keys. A request with an `Origin` or `Sec-Fetch-Site` of
  another site is refused (403 `cross_origin`); the app sends neither, as a request of the same origin. Another
  content type is refused (415).
- **Errors**: any status but 200 has `{"ok": false, "error": "<code>", "message": "<a sentence for people>"}`; the
  app shows `message`. 401 `login_required` is "sign in" (and a wrong name or password at the login), 429 too many
  attempts, 403 another origin or host or not allowed, 409 not possible now, 400 `invalid_param` a wrong field,
  503 and 502 something to try again later.
- **Version**: `API_VERSION` in `web/app_api.py`, `SiteApi.API_VERSION` in the app: now **1**. `GET
  /api/auth/state` has it as `"api"`, `GET /api/app/v1` as `"version"` (with `"api": "perception-app"`). A new
  field or endpoint keeps the number. **A field the app reads is never removed or renamed, and never changes
  meaning, without raising the number** (and a site that keeps answering the old version too, under its path:
  `/api/app/v2` beside `/api/app/v1`). An app that finds a higher number than its own asks to be updated; a site
  without `/api/app/v1` (404) is too old for the app.

## Endpoints

| Request | Session | Answer |
| --- | --- | --- |
| `GET /api/auth/state` | no | `configured`, `signed_in`, `api`; signed in also `user`, `sessions` |
| `POST /api/auth/login` `{"user", "password"}` | no | `{"ok": true}` and the session cookie |
| `POST /api/auth/logout` `{}` | yes | ends this session; the cookie expires |
| `GET /api/app/v1` | yes | the site for the app, below |
| `POST /api/app/v1/tests` | yes | `{"ok", "test", "calibration"}`: a test made with the app, below |
| `GET /api/app/v1/tests` | yes | `{"ok", "tests": [...]}`: every test on the site |
| `POST /api/app/v1/comma-key` `{"public_key"}` | yes | `{"ok", "added", "already"}`: the phone's key on the comma, below |
| `GET /download/android` | yes | the newest APK built on this server (not JSON: the file) |

### `GET /api/app/v1`

```json
{"ok": true, "api": "perception-app", "version": 1, "code": "85c756f",
 "calibration": {"radar_to_camera_m": 1.5, "radar_correction_m": -0.89, "radar_to_reference_m": 0.61,
                 "source": "tests", "version": 3, "status": "close_only", "uncertainty_m": 0.09,
                 "tested_m": [0.9, 1.32]},
 "comma": {"host_key_fingerprints": ["SHA256:VmJ38dgdv0FQ7zoRV/9NCsqcmY6OpUPux0CXtxd9YxM"],
           "python": "/usr/local/venv/bin/python", "openpilot": "/data/openpilot"},
 "label_range_m": [5.0, 30.0]}
```

- `code`: the short commit of the site (empty when it cannot be read).
- `calibration`: the car's radar calibration (`web/radar.py` `calibration_view`): distance from the comma =
  reading + `radar_correction_m` + `radar_to_camera_m` = reading + `radar_to_reference_m` (null while the distance
  is not set). `source` `"tests"`, `"typed"`, `"none"` (the car has no radar) or null; `version` +1 at every
  change; `status` `not_set`, `typed`, `close_only`, `ready` or `no_radar`; `uncertainty_m` only for numbers from
  the tests; `tested_m` the tape distances of the kept tests, ascending.
- `comma.host_key_fingerprints`: the SHA-256 fingerprints (`ssh-keygen -l` form) of the comma's **ssh-ed25519**
  host keys in this server's `known_hosts`, for the host `web/comma.py` uses (`ssh -G` resolves the alias,
  `HostKeyAlias` and port). Empty when the server has none. Nothing goes to the comma for it.
- `comma.python`, `comma.openpilot`: where the app runs `app/comma_listener.py` on the comma.
- `label_range_m`: the distances where confirmed pairs become labels; a test there checks the calibration.

### `POST /api/app/v1/tests`

The body is exactly what `SiteApi.testBody` sends, every key present and no other (at each level):

```json
{"client_id": "6f1c1f9e-6c7e-4c64-9f43-1d1a9b0e2a11", "created_unix": 1792771200.5, "target": "wall",
 "tape_radar_m": 1.32, "tape_comma_m": 2.82,
 "capture": {"started_unix": 1792771170.0, "duration_s": 20.0, "samples": 200, "with_reading": 180,
             "reading_m": 2.25, "steady_share": 0.98, "histogram": {"2.25": 176, "2.50": 4},
             "speed_max_mps": 0.0, "onroad": true},
 "comma": {"route": "00000134--a5c8197883", "git_commit": "1b6a472a"},
 "note": ""}
```

| Field | Rule |
| --- | --- |
| `client_id` | the test's UUID made by the app, lowercase `8-4-4-4-12` hex |
| `created_unix`, `capture.started_unix` | seconds since 1970, from 2017 to 2100 |
| `target` | `"wall"` or `"car"` |
| `tape_radar_m`, `tape_comma_m` | metres, 0.3 to 100, the comma behind the radar, at most 5 m apart |
| `capture.duration_s` | more than 0, at most 600 |
| `capture.samples`, `capture.with_reading` | integers, `0 ≤ with_reading ≤ samples ≤ 100000` |
| `capture.reading_m` | the steady reading, more than 0, at most 300 m; null is refused (no test without a reading) |
| `capture.steady_share` | 0 to 1: the share of the lines with a reading that had `reading_m` |
| `capture.histogram` | `{"<metres, 2 decimals>": count ≥ 1}`; the counts add up to `with_reading`; it has `reading_m`, and its count over `with_reading` is `steady_share` (±0.005) |
| steadiness | the app's own rule (`perception/radar/series.py`): a reading at least half of the lines, `steady_share` at least 0.8 |
| `capture.speed_max_mps` | 0 to 100, or null |
| `capture.onroad` | true or false |
| `comma.route` | the comma's recording during the capture, `^[0-9a-f]{8}--[0-9a-f]{10}$`, or null |
| `comma.git_commit` | the comma's openpilot commit, 7 to 40 lowercase hex digits, or null |
| `note` | text of at most 500 characters, no control characters but new lines and tabs |
| correction | tape radar − reading within ±1 m (else "more than 1 m apart") |

Anything else is 400 `invalid_param` with a sentence the app shows (the one it shows most:
`"The comma sits behind the radar: its distance must be the larger."`).

The site keeps it as a calibration test with **source `"app"`** (the reading captured live on the phone), in the
same table as the tests from recordings (`web/store.py` `radar_tests`), kept, with its route to check it against
the recording later. While kept it counts in the means of the calibration like any test, and they are re-applied
at once when the calibration comes from the tests (or has none). Settings › Your car's radar and Radar list it
"from the app"; it can be left out or deleted there like any test.

**Idempotent on `client_id`**: the same test sent again (a reply lost on the way) answers 200 with the test already
stored, the same `id`, and changes nothing. When the first answer was an error after the test was stored (the
calibration could not follow it), the same test sent again makes the calibration follow before it answers 200, so a
200 always means that the test is stored and counted. Another test under a `client_id` already taken is 409
`client_id_taken`. A test deleted on the site frees its `client_id`: the app can send it again.

```json
{"ok": true,
 "test": {"id": "a1b2c3d4e5f6", "client_id": "6f1c1f9e-6c7e-4c64-9f43-1d1a9b0e2a11", "target": "wall",
          "route": "00000134--a5c8197883", "reading_m": 2.25, "tape_radar_m": 1.32, "tape_comma_m": 2.82,
          "correction_m": -0.93, "kept": true, "created": 1792771260.2},
 "calibration": {"radar_to_camera_m": 1.5, "radar_correction_m": -0.9, "radar_to_reference_m": 0.6,
                 "source": "tests", "version": 4, "status": "close_only", "uncertainty_m": 0.07,
                 "tested_m": [0.9, 1.32, 1.32]}}
```

`test.created` is when the site stored it; `calibration` the calibration after it, as in `GET /api/app/v1`.

### `GET /api/app/v1/tests`

```json
{"ok": true, "tests": [
 {"id": "0a0b0c0d0e0f", "target": "wall", "route": "00000120--1f2e3d4c5b", "reading_m": 1.75, "tape_radar_m": 0.9,
  "tape_comma_m": 2.4, "correction_m": -0.85, "kept": true, "created": 1792600000.0},
 {"id": "a1b2c3d4e5f6", "client_id": "6f1c1f9e-6c7e-4c64-9f43-1d1a9b0e2a11", "target": "wall",
  "route": "00000134--a5c8197883", "reading_m": 2.25, "tape_radar_m": 1.32, "tape_comma_m": 2.82,
  "correction_m": -0.93, "kept": true, "created": 1792771260.2}]}
```

Every test, in the order the site saved them, kept or left out: the app's own ones have `client_id` (the app
counts one as on the site once this list shows it), the ones from recordings have none. `route` is a drive or
recording (`<18 hex>` or `<8 hex>--<10 hex>`), or null for a test from the app made without a recording.

### `POST /api/app/v1/comma-key`

`{"public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI… perception-app@Pixel-8"}`: exactly one `ssh-ed25519` key
(the base64 of its wire form: the type, then 32 bytes) and an optional comment of 1 to 80 letters, digits and
`._@-`, one space between; anything else (another type, a second key, options, a new line, other characters) is
400 `invalid_param`.

The site appends it to the comma's allowed SSH keys, `/data/params/d/GithubSshKeys` (owner comma, mode 600), over
ssh with the server's own key (`web/comma.py` options: BatchMode, timeouts). The key goes on the standard input of a
fixed script and never into a command line. The script appends one line atomically: a temporary file next to the
list with the same owner and mode 600, then renamed over it. It is the only thing the site ever writes on the
comma; a GitHub refresh on the comma replaces the list (the phone must then be added again).

- **One phone at a time, with the password**: Settings › Perception for Android › "Let the app add phones to the
  comma" is off by default, and a session alone cannot turn it on (a key on the comma is a shell on the car
  computer). On is `POST /api/settings/app {"comma_keys": true, "password": "<the administrator's password>"}`
  from the page, the password checked again as at sign-in (403 `wrong_password`, the wrong attempts counted with
  those of the sign-in, 429 after too many); off is `{"comma_keys": false}`. Once on, the app may add **one**
  phone within **10 minutes** (`PERMISSION_SECONDS` in `web/app_api.py`): the permission ends by itself after a
  key the comma confirms (`added` or `already`), or when the time runs out. A failure (503, 502) leaves it on, so
  the app can try again. Every change is recorded in the store (`app_permissions`, append-only: on, off by the
  owner, used by a phone). Off: 403 `not_allowed`, nothing goes to the comma; for the app that means "ask the
  owner to allow it in Settings, then try again", not "never".
- Every addition is recorded in the store (`comma_keys`, append-only) **before** anything goes to the comma (a
  row `sending`; when it cannot be written, 503 `busy` and nothing is sent), then with what the comma answered
  (`added`, `already`, or `failed` with the error code), and printed in the site's log both times. Settings lists
  every key the site ever sent, each with its state: added, already there, sent but not confirmed (no answer in
  time, or the answer could not be recorded: it may be on the comma), or refused. Turning the permission off takes
  no key off the comma: a GitHub refresh on the comma does.
- `{"ok": true, "added": true, "already": false}`: added. The answer is the comma's, even when the store could not
  record it (Settings then shows the key as sent, not confirmed).
- `{"ok": true, "added": false, "already": true}`: the comma already had this key (whatever its comment).
- 503 `comma_unreachable` `"The comma cannot be reached from the site right now."`: the comma does not answer or does
  not let the server in. 502 `comma_refused`: it answered but its list could not be written.

### Downloads (signed in)

- `GET /download/android`: the APK named by `outputs/android/latest.json` (`file`, written by
  `helpers/build_android.sh`), `Content-Disposition: attachment`. 404 without a build.
- `GET /download/android/aab`: the bundle for Google Play when `latest.json` names one (`aab`,
  `perception-android-<version>.aab`; never a `-dev` one, which is never published): the first upload of a new app
  to Google Play is by hand. 404 otherwise.

Settings › Perception for Android offers both, with the version and size, and the Google Play internal test link.
A phone keeps one of the two: the APK of the site is signed with the server's upload key and the Google Play one
by Google (Play App Signing), so neither updates the other; switching means uninstalling, which deletes the tests
still on the phone and its SSH key. The card says so: send the tests first, add the phone to the comma again after.

## Keeping the two in step

- `tests/test_web_app.py` `AppContract` signs in, reads, sends and signs out as the app does, without an `Origin`,
  against the real handlers, and checks every answer against the app's own fixtures,
  `app/android/app/src/test/resources/fixtures/*.json` (the same keys at every level, the same error codes and
  messages), and sends `test_upload_body.json` as the app sends it. The app's `SiteApiTest` reads the same fixtures:
  change both together.
- The other tests of `tests/test_web_app.py` check every rule above: the store and its import, idempotency, the
  validation, the comma key (off, on only with the password, one phone then off, the time limit, recorded before
  the comma, unreachable, already, never in an argv; a fake ssh, never the real comma), the downloads.
