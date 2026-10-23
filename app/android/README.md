# Perception for Android

The app of openpilot.perception: the car's radar reading, live, and the radar calibration test with a tape
measure, **with or without internet**. In the garage (no internet) the comma joins the phone's hotspot and the app
talks to it directly over SSH; tests are stored on the phone. When the phone reaches the site (home Wi-Fi or
ZeroTier, `http://10.10.10.213:8767` by default) it sends them to perception-web and shows the whole site.

Nothing of openpilot is modified and nothing is installed or written on the comma: the only thing that runs there
is [`app/comma_listener.py`](../comma_listener.py), read-only, while a radar screen is open. Package
`ch.bodhi.perception`, Android 8.0 (API 26) or newer.

## Use

1. **Setup** (first start, every step skippable, again from Settings):
   - *Your perception site*: its address and the administrator's name and password (`POST /api/auth/login`).
     The app keeps the site's session cookie (private, shared with the Site tab), never the password. The network
     the owner signs in on becomes one of his (see *The site's networks* below).
   - *Connect this phone to your comma*: the app makes its own Ed25519 SSH key; the private key never leaves the
     phone (encrypted with an AES-256-GCM key of the Android Keystore, usable without unlocking so the radar works
     with the phone in a car holder). The comma gets the public key with **one tap** (the best way:
     `POST /api/app/v1/comma-key`, the site adds it, at home, when the owner allows it) or through **GitHub**:
     copy the key, add it at github.com/settings/ssh/new, then on the comma (the comma four's mici menus, with
     internet) *settings › developer › SSH keys*: tap it, keep the GitHub username, confirm; the comma downloads
     the GitHub keys again, now with the phone's. Then *delete the key from GitHub*: while it is there it can push
     to the owner's repositories, the comma's openpilot fork too, and the comma keeps its own copy. Every download
     of the GitHub keys on the comma replaces all its keys with the ones on GitHub then (a phone added with one
     tap is dropped and must be added again), and a failed download leaves it with none. *settings › developer ›
     enable SSH* stays on. *Test the connection* finds the comma, logs in and checks that openpilot and its Python
     are there (`test -d`, `test -x`), with a clear result.
   - *For the garage*: the comma joins the phone's hotspot once (phone: hotspot on; comma: *settings › network ›
     wi-fi* › the hotspot); it joins it by itself afterwards.
2. **Radar**: the reading live. The big number is the distance from the comma (reading + correction + radar to
   comma, with the calibration last received from the site), with the raw reading under it; "No reading" when the
   radar has no target, "Switch the car on" while the car is off, "steady 12 s at 2.25 m", the speed, the last 30 s
   as a chart and the state of the connection. Without a calibration it shows the raw reading.
3. **Radar test** (fully offline): wall close or parked car farther, how to park, a 20 s capture of the live
   stream with the car on and standing still (good with at least 10 s of one steady reading at under 0.05 m/s; it
   says what is wrong otherwise), the two tapes (steppers in metres, 1 cm steps, "1,32" accepted, the checks of
   the site), the result ("reads 0.93 m too long", what the calibration becomes with this test). *Save* puts it in
   the phone's queue.
4. **Tests**: every test with its state (on the phone / sent / on the site / refused, with the site's reason /
   no longer on the site; refused and vanished ones can be sent again, any can be deleted), the site's own tests
   and the calibration status. Waiting tests are sent while the app is open where the site answers
   (`POST /api/app/v1/tests`, retried with the same `client_id`: the site takes a test once however many times
   it is sent); there is no background job, so after the garage they go the next time the app is opened at home
   or with ZeroTier on. A reply alone does not make a test "on the site": it is *sent* until the site's own list
   (`GET /api/app/v1/tests`) shows it, and goes again if the next list does not. A test the list showed and no
   longer has (deleted on the site) is left out of the means and can be sent again.
5. **Site**: the whole site in a WebView with the app's session when reachable on one of the owner's networks
   (its `confirm()`/`alert()`/`prompt()` as Android dialogs; a download, such as the APK of `/download/android`,
   goes to the system's downloads with the session); a question on a new network; a clear offline screen
   otherwise. A refresh in the background never takes the page off the screen.
6. **Settings**: site address, sign in or out, the comma's address and SSH host key (forget), the phone key (show,
   copy, add to the comma, make a new one), the garage help, about and the licences.

## How it works

| File | What |
| --- | --- |
| `SiteApi.kt` | the whole app ↔ site contract (endpoints, JSON both ways, errors), in one file |
| `Http.kt` | HttpURLConnection transport and the WebView cookie store behind small interfaces |
| `SiteAddress.kt` | the site address rules: plain HTTP only to a private IP literal, same-origin checks |
| `SiteNetworks.kt` | which network carries the traffic to the site (never mobile data) and its name, for the owner's networks |
| `ListenerProtocol.kt` | the JSON lines of `app/comma_listener.py`, and the command that starts it |
| `CommaSsh.kt` | sshj with Bouncy Castle: modern algorithms only, host key pinned by SHA-256 fingerprint, login with the phone key, the listener channel |
| `CommaFinder.kt`, `LocalNetworks.kt` | finding the comma: the last address that worked, then the /24 of the hotspot, then of the Wi-Fi, on port 22 with short timeouts; only an SSH server with a trusted host key is the comma |
| `RadarSession.kt` | one listener for the radar screens, shared by the Radar tab and the whole test, stopped 45 s after they go away; reconnects with a backoff (5 s, 15 s, 30 s, 60 s, 120 s, then stops); at most 6 starts per drive |
| `Calibration.kt`, `Capture.kt` | the site's calibration math and tape checks; the capture and steadiness rules |
| `TestQueue.kt` | the tests on the phone (a JSON file written atomically) and their sending |
| `PhoneKey.kt` | the phone's Ed25519 key, its OpenSSH forms and its Keystore encryption |
| `AppModel.kt` | the state of the app; the site every 30 s while the app is open, and when a network comes |

**The comma's host key** is pinned on the phone: the one the owner accepted when the app found the comma (before
the first sync it shows the SSH servers it found with their fingerprints: trust on first use), or the one the site
tells (`GET /api/app/v1` → `comma.host_key_fingerprints`), pinned at the first connection that shows it. What the
site says later never replaces a pinned key by itself: the app keeps it, tells the owner the site knows another,
and changes only when he confirms the comma was reset. A host key that does not match is never accepted: the app
says the key changed and stops. Only `ssh-ed25519` host keys are negotiated, the ones whose fingerprint is
pinned. The Python the listener runs must be a plain path to `python`, `python3` or `python3.N`.

**The site's networks**: plain HTTP cannot prove which machine answers at `10.10.10.213:8767`, and that address
can be another machine on a café, work or carrier network. So the app sends its session, its tests and believes
the site's answers (the calibration, the comma's paths, the list of tests) only on the owner's networks: the one he
signed in on and the ones he confirmed ("Is this your network?" when the site answers on a new one, asked without
the session). A network is named by what stays the same between visits: a VPN (ZeroTier) by the phone's own
address on it, a Wi-Fi or Ethernet by its subnet and router. The route is the VPN that routes the site, else the
Wi-Fi or Ethernet the address is on (bound to it when it is not the default network, e.g. no internet), else the
default Wi-Fi; never mobile data. The WebView's requests to the site are held back on other networks and in the
moment after a network change. An `https://` site proves itself with its certificate and needs none of this.

**The listener** (`app/comma_listener.py`) is copied into the APK by the build (`bundleCommaListener` in
`app/build.gradle.kts`), never duplicated in `app/android`. The app runs
`cd /data/openpilot && exec nice -n 19 /usr/local/venv/bin/python -B -u -` (paths from the site, checked), writes
the script to the channel and closes its input; Python starts the script at the end of its input. Closing the
channel stops it (its next write fails). Each start takes a carState reader slot on the comma until the next
ignition (msgq has 25, openpilot's processes take about 14 onroad, and with all taken msgq makes every reader
reconnect, openpilot's too), so the app never retries in a fast loop, keeps one listener for the Radar tab and the
whole test (45 s of grace for a tab switch or the screen off), and starts at most 6 per drive: then it pauses
("Paused to protect openpilot") and starts again only once a read-only check (`IsOffroad`, the newest route)
shows another drive or the car off.

**Plain HTTP**: the site is HTTP on the LAN or ZeroTier. A network security config can only name hosts, not
ranges, and the address is chosen in the app, so the app enforces the rule itself: `http://` only for a private,
shared (100.64/10), link-local or loopback IP literal; every request and every WebView load to the configured site
only (`res/xml/network_security_config.xml`). HTTPS trusts the system certificate authorities only.

**Privacy**: no backup and no device transfer; the cookie, the key and the tests are in the app's private storage;
the app logs nothing (no logger is bundled for sshj's SLF4J). Only the launcher activity is exported.

## Build

On the server, never by GitHub:

```bash
helpers/build_android.sh              # unit tests, Android lint, signed release APK
helpers/build_android.sh --no-lint    # quicker
helpers/build_android.sh --setup-only # tools and signing key only
```

It sets up what is missing (JDK 17, Android SDK platform 36 and build-tools 35, Gradle 8.14.3 and its cache in
`/opt/android`, shared with openpilot.distill's app), every download pinned (the Temurin 17.0.21+9 JDK, the
command-line tools and Gradle by a SHA-256 written in the script, the SDK packages by sdkmanager's checksums, every
Maven artifact by Gradle's dependency verification in `gradle/verification-metadata.xml`; after changing a
dependency on purpose, `helpers/build_android.sh --update-verification` writes it again: review its diff), and the
signing key of the server, the same as openpilot.distill's app (the owner's one upload key for Google Play), in
`~/.config/distill-android/` (`distill-release.p12`, PKCS12, alias `distill`, made once by whichever app is built
first, never in git, never printed; **keep a backup**: a phone installs an update only with the same key). Output: `outputs/android/perception-android-<version>.apk`
(the last 3 are kept) and `outputs/android/latest.json` (`version`, `version_code`, `file`, `size`, `sha256`,
`built`, `commit`). Version `0.1.<commits of HEAD>`, versionCode `1000 + <commits>`, `-dev` when `app/` has local
changes. A release without the server's key stops: it is never signed with a debug key.

Toolchain pinned as openpilot.distill's app: Android Gradle Plugin 8.13.2, Kotlin 2.2.21, Compose BOM 2025.12.01,
`compileSdk`/`targetSdk` 36, `minSdk` 26. No wrapper jar is committed; by hand, with the tools of `/opt/android`:
`gradle :app:testDebugUnitTest :app:lintRelease :app:assembleDebug` in `app/android`.

## Tests

JVM unit tests (`app/src/test`), no phone needed: the listener lines (the ones the comma printed), the calibration
math and tape checks, the capture and steadiness rules, the queue and its sending against a fake site, the subnet
scan against a fake network, the site API against fixtures of the contract (`src/test/resources/fixtures`), the
phone key against RFC 8032 and `ssh-keygen`. `CommaSshIT` runs the real SSH code against a comma, only when asked
(it takes one carState reader slot until the next ignition):

```bash
PERCEPTION_SSH_IT_HOST=<comma ip> PERCEPTION_SSH_IT_KEY=~/.ssh/id_ed25519 \
PERCEPTION_SSH_IT_FINGERPRINT=SHA256:… gradle :app:testDebugUnitTest --tests '*CommaSshIT*'
```

## Libraries

| Library | Licence |
| --- | --- |
| sshj 0.41.2, asn-one 0.6.0 (com.hierynomus) | Apache-2.0 |
| Bouncy Castle 1.84 (bcprov, bcpkix, bcutil; jdk18on) | Bouncy Castle Licence (MIT style) |
| SLF4J API 2.0.17 | MIT |
| AndroidX, Jetpack Compose, Material 3, Guava ListenableFuture 1.0 | Apache-2.0 |
| Kotlin, kotlinx.coroutines, JetBrains annotations 23.0.0, JSpecify 1.0.0 | Apache-2.0 |
| JUnit 4.13.2, org.json 20240303 (tests only, not in the app) | EPL-1.0, public domain |

The full texts the licences ask to ship (the Bouncy Castle and SLF4J notices, sshj's NOTICE, Apache-2.0) are in
the APK, `app/src/main/assets/third_party_licences.txt`, shown in Settings › Licences.
