#!/usr/bin/env bash
# Builds Perception for Android (app/android) on this server, never by GitHub, and publishes it on Google Play
# ("Openpilot Perception", ch.bodhi.perception) by itself: the unit tests, Android lint, the release APK that the
# site offers for download and the Android App Bundle (AAB) that Google Play takes, in outputs/android/. Then, when
# this server has the key of the Play service account, the AAB goes to the internal testing track of Google Play.
# What is missing is set up first, so a fresh server needs nothing by hand (install.sh runs this script with
# --no-play, the code update of the site runs it again when app/ or this script changes):
#   - the tools, in /opt/android (or PERCEPTION_ANDROID_TOOLS): JDK 17 (Temurin 17.0.21+9), Android SDK platform 36 and
#     build-tools 35, Gradle 8.14.3 and its cache in /opt/android/gradle-home (about 1.5 GB; x86_64 only, as the
#     SDK build tools). The same tools as openpilot.distill's app: one copy serves both.
#   - the signing key of this server, shared with openpilot.distill's app (the owner's one upload key for Google
#     Play): ~/.config/distill-android (or PERCEPTION_ANDROID_KEYS) with distill-release.p12 (PKCS12, alias
#     "distill", RSA 4096, ~27 years) and its passwords, made once by whichever app is built first, exactly as
#     openpilot.distill's helpers/build_android.sh makes it. Never in git, never printed; keep a backup: a phone
#     installs an update only when it is signed with the same key as the app it has.
#
#   helpers/build_android.sh [--no-play | --play=TRACK | --play-dry-run[=TRACK]] [--listing] [--no-lint]
#                            [--setup-only] [--update-verification] [--help]
#     (nothing)              build, then publish on the internal track of Google Play when it can: the key of the
#                            service account is in ~/.config/distill-android/play-service-account.json (the same
#                            file as openpilot.distill's app), app/ has no local changes, HEAD is on GitHub
#                            (origin), neither changed during the build, and this version is not on Google Play
#                            yet. Otherwise it says why not; a failed upload is a warning, the APK of the site is
#                            built all the same. Google Play gets the very bundle checked here (never one built
#                            again), and the tag goes on the commit it was built from.
#     --play=TRACK           the same on track internal, alpha (closed testing), beta (open testing) or production,
#                            and every problem is an error. A version already on Google Play is promoted from the
#                            internal track to TRACK.
#     --play-dry-run[=TRACK] build and go through the publishing without changing anything: the checks above are
#                            reported, and the upload goes into an edit of Google Play that Google validates and
#                            that is never committed (Gradle Play Publisher --no-commit); no tag
#     --no-play              build only (install.sh)
#     --listing              also upload the store listing of app/android/app/src/main/play/listings
#     --no-lint              skip Android lint (a quicker build)
#     --setup-only           install the tools and make the key when missing, do not build
#     --update-verification  the full build, writing the SHA-256 of every dependency it resolves to
#                            app/android/gradle/verification-metadata.xml: only after changing a dependency or a
#                            plugin on purpose (review the diff of that file)
#   Every version this script publishes is tagged perception-android-v<version>, and the tag pushed to GitHub.
#   Google takes the first version of a new app only by hand, in Play Console: the script recognises its answer
#   for an app with no upload yet and says which AAB to upload (app/android/README.md, Google Play).
#
# The toolchain runs next to the signing key, so every download is pinned: the JDK, the command-line tools and
# Gradle by an exact version and a SHA-256 written here; the Android SDK packages by sdkmanager's own checksums;
# every Maven artifact of the build, Gradle Play Publisher too, by Gradle's dependency verification
# (verification-metadata.xml).
#
# The comma side of the radar screen, app/comma_listener.py, is copied into the APK by the build (never a second
# copy in app/android).
# Version: 0.1.<commits of HEAD>, versionCode 1000 + <commits>: every commit has its own, always higher, as Google
#   Play requires. "-dev" is added when app/ has local changes, or when HEAD or app/ changed during the build
#   (never published).
# Output: outputs/android/perception-android-<version>.apk and .aab (the last 3 of each are kept) and
#   outputs/android/latest.json {version, version_code, file, size, sha256, aab, aab_size, aab_sha256, built,
#   commit}: what the site shows of the last build. One build at a time: a second one waits for the first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TOOLS="${PERCEPTION_ANDROID_TOOLS:-/opt/android}"
KEYS="${PERCEPTION_ANDROID_KEYS:-$HOME/.config/distill-android}"
KEY_FILE="distill-release.p12"
PLAY_KEY="$KEYS/play-service-account.json"
PACKAGE="ch.bodhi.perception"
PLAY_APP="Openpilot Perception"
OUT="$ROOT/outputs/android"
# The edit of Google Play that Gradle Play Publisher keeps between two runs when it is not committed.
PLAY_EDIT="$ROOT/app/android/app/build/gpp"
LISTINGS="$ROOT/app/android/app/src/main/play/listings"
JDK_VERSION="17.0.21+9"
JDK_URL="https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/OpenJDK17U-jdk_x64_linux_hotspot_17.0.21_9.tar.gz"
JDK_SHA256="8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711"
CMDLINE_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
CMDLINE_SHA256="2d2d50857e4eb553af5a6dc3ad507a17adf43d115264b1afc116f95c92e5e258"
GRADLE_VERSION="8.14.3"
GRADLE_URL="https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
GRADLE_SHA256="bd71102213493060956ec229d946beee57158dbd89d0e62b91bca0fa2c5f3531"
# The SDK packages of app/android/app/build.gradle.kts (compileSdk 36; AGP 8.13 builds with build-tools 35).
PLATFORM="android-36"
BUILD_TOOLS="35.0.0"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

SETUP_ONLY=0
LINT=1
VERIFICATION=0
# auto: publish on the internal track when everything is in place; strict: --play=TRACK; dry: --play-dry-run;
# off: --no-play.
PLAY=auto
TRACK=internal
LISTING=0
for argument in "$@"; do
  case "$argument" in
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    --setup-only) SETUP_ONLY=1 ;;
    --no-lint) LINT=0 ;;
    --update-verification) VERIFICATION=1 ;;
    --no-play) PLAY=off ;;
    --play) PLAY=strict ;;
    --play=*) PLAY=strict; TRACK="${argument#--play=}" ;;
    --play-dry-run) PLAY=dry ;;
    --play-dry-run=*) PLAY=dry; TRACK="${argument#--play-dry-run=}" ;;
    --listing) LISTING=1 ;;
    *) die "invalid option: $argument (see --help)" ;;
  esac
done
case "$TRACK" in
  internal|alpha|beta|production) ;;
  *) die "unknown Google Play track: $TRACK (internal, alpha, beta or production)" ;;
esac
if [ "$SETUP_ONLY" -eq 0 ]; then
  # What cannot work is said before a build of minutes.
  if [ "$PLAY" = dry ] && [ ! -f "$PLAY_KEY" ]; then
    die "--play-dry-run needs the key of the Google Play service account in $PLAY_KEY (app/android/README.md, Google Play)"
  fi
  if [ "$LISTING" -eq 1 ]; then
    [ "$PLAY" != off ] || die "--listing goes to Google Play: not with --no-play"
    [ -f "$PLAY_KEY" ] || die "no key of the Google Play service account in $PLAY_KEY"
    [ -d "$LISTINGS" ] || die "no store listing in app/android/app/src/main/play/listings: edit it in Play Console"
  fi
fi

need() {
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || die "$command_name is missing (apt install $command_name)"
  done
}

# One build at a time: two Gradle runs in app/android would write the same build folder.
need flock
mkdir -p "$OUT"
exec 9> "$OUT/.build.lock"
if ! flock -n 9; then
  say "another build of the app is running: waiting for it to end"
  flock 9
fi

JDK_DIR="$TOOLS/jdk"
jdk_is_pinned() { grep -qF "JAVA_RUNTIME_VERSION=\"$JDK_VERSION\"" "$1/release" 2>/dev/null && [ -x "$1/bin/java" ]; }

# A download used only when its SHA-256 is the one written in this script.
fetch_pinned() {
  local url="$1" expected="$2" target="$3"
  need curl sha256sum
  curl -fsSL -o "$target" "$url"
  [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$expected" ] || { rm -f "$target"; die "$url does not match its pinned SHA-256"; }
}

# -- tools: each one only when missing, unpacked next to its final place and moved there at the end, so an
#    interrupted download leaves nothing half installed --
setup_tools() {
  [ "$(uname -m)" = x86_64 ] || die "the Android build tools exist for x86_64 only (this machine: $(uname -m))"
  mkdir -p "$TOOLS"
  # Its own folder: openpilot.distill's build may be setting up the same tools at the same time.
  local work="$TOOLS/.download-perception"
  rm -rf "$work"
  mkdir -p "$work"
  # The shared $TOOLS/jdk when it is the pinned release (or missing); a JDK of another release there (put by an
  # older build or by openpilot.distill's) is left alone and the pinned one goes next to it.
  if [ -x "$TOOLS/jdk/bin/java" ] && ! jdk_is_pinned "$TOOLS/jdk"; then
    JDK_DIR="$TOOLS/jdk-$JDK_VERSION"
  fi
  if ! jdk_is_pinned "$JDK_DIR"; then
    say "downloading JDK $JDK_VERSION (Eclipse Temurin) into $JDK_DIR"
    need tar
    fetch_pinned "$JDK_URL" "$JDK_SHA256" "$work/jdk.tar.gz"
    mkdir -p "$work/jdk"
    tar -xzf "$work/jdk.tar.gz" -C "$work/jdk" --strip-components=1
    rm -rf "$JDK_DIR"
    mv "$work/jdk" "$JDK_DIR"
    jdk_is_pinned "$JDK_DIR" || die "the JDK in $JDK_DIR is not Temurin $JDK_VERSION"
  fi
  if [ ! -x "$TOOLS/sdk/cmdline-tools/latest/bin/sdkmanager" ]; then
    say "downloading the Android command-line tools into $TOOLS/sdk"
    need unzip
    fetch_pinned "$CMDLINE_URL" "$CMDLINE_SHA256" "$work/cmdline.zip"
    unzip -q "$work/cmdline.zip" -d "$work/cmdline"
    mkdir -p "$TOOLS/sdk/cmdline-tools"
    mv "$work/cmdline/cmdline-tools" "$TOOLS/sdk/cmdline-tools/latest"
  fi
  if [ ! -d "$TOOLS/sdk/platforms/$PLATFORM" ] || [ ! -d "$TOOLS/sdk/build-tools/$BUILD_TOOLS" ]; then
    say "installing Android SDK $PLATFORM and build-tools $BUILD_TOOLS (licenses accepted)"
    local sdkmanager="$TOOLS/sdk/cmdline-tools/latest/bin/sdkmanager"
    yes | JAVA_HOME="$JDK_DIR" "$sdkmanager" --sdk_root="$TOOLS/sdk" --licenses > "$work/licenses.log" 2>&1 || true
    JAVA_HOME="$JDK_DIR" "$sdkmanager" --sdk_root="$TOOLS/sdk" "platforms;$PLATFORM" "build-tools;$BUILD_TOOLS" \
      > "$work/sdk.log" 2>&1 || { tail -5 "$work/sdk.log" >&2; die "the Android SDK did not install"; }
  fi
  if [ ! -x "$TOOLS/gradle-$GRADLE_VERSION/bin/gradle" ]; then
    say "downloading Gradle $GRADLE_VERSION into $TOOLS/gradle-$GRADLE_VERSION"
    need unzip
    fetch_pinned "$GRADLE_URL" "$GRADLE_SHA256" "$work/gradle.zip"
    unzip -q "$work/gradle.zip" -d "$work"
    mv "$work/gradle-$GRADLE_VERSION" "$TOOLS/gradle-$GRADLE_VERSION"
  fi
  rm -rf "$work"
}

# -- the signing key of this server, made once; its password goes to files of mode 600 only --
setup_key() {
  [ -f "$KEYS/$KEY_FILE" ] && return 0
  say "making the signing key of this server in $KEYS"
  (
    umask 077
    mkdir -p "$KEYS"
    chmod 700 "$KEYS"
    PW="$(head -c 18 /dev/urandom | base64 | tr '+/' '-_')"
    export PW
    # The same key and names as openpilot.distill's script, so either app can be built first on a new server.
    "$JDK_DIR/bin/keytool" -genkeypair -noprompt -keystore "$KEYS/$KEY_FILE.part" -storetype PKCS12 \
      -alias distill -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Distill for Android, O=openpilot.distill" \
      -storepass:env PW -keypass:env PW > /dev/null 2>&1
    mv "$KEYS/$KEY_FILE.part" "$KEYS/$KEY_FILE"
    printf '%s' "$PW" > "$KEYS/ANDROID_KEYSTORE_PASSWORD.txt"
    printf '%s' "$PW" > "$KEYS/ANDROID_KEY_PASSWORD.txt"
    printf '%s' distill > "$KEYS/ANDROID_KEY_ALIAS.txt"
    cat > "$KEYS/README.txt" <<'EOF'
Signing key of the owner's Android apps on this server (Distill and Perception; PKCS12, alias "distill", RSA 4096,
valid ~27 years; for Google Play it is the upload key), made by helpers/build_android.sh. KEEP A BACKUP of
distill-release.p12 and its password somewhere safe: a lost key means every phone must uninstall the apps to get
the next version. Never put these files in git.

The passwords are in ANDROID_KEYSTORE_PASSWORD.txt and ANDROID_KEY_PASSWORD.txt, the alias in
ANDROID_KEY_ALIAS.txt. The key of the Google Play service account goes next to it, as play-service-account.json
(app/android/README.md of openpilot.perception, Google Play).
EOF
  )
}

setup_tools
setup_key
[ "$SETUP_ONLY" -eq 1 ] && { say "Android tools in $TOOLS, signing key in $KEYS: ready"; exit 0; }

[ -s "$ROOT/app/comma_listener.py" ] || die "app/comma_listener.py is missing: the app bundles it"

export JAVA_HOME="$JDK_DIR" ANDROID_HOME="$TOOLS/sdk" GRADLE_USER_HOME="$TOOLS/gradle-home"
# A remote that wants a password fails instead of waiting for it (the code update runs without a terminal).
export GIT_TERMINAL_PROMPT=0

# The commit this build is made of, in full: the version, the check that GitHub has it and the tag all use it,
# never whatever HEAD is later (other sessions edit and commit in this clone while a build runs).
full_commit="$(git -C "$ROOT" rev-parse HEAD)"
count="$(git -C "$ROOT" rev-list --count "$full_commit")"
commit="$(git -C "$ROOT" rev-parse --short "$full_commit")"
version="0.1.$count"
# The APK holds app/android and app/comma_listener.py: a change in either is a development build.
if [ -n "$(git -C "$ROOT" status --porcelain -- app)" ]; then
  version="$version-dev"
fi
clean_version="${version%-dev}"
code=$((1000 + count))
# The version name inside the app: every Gradle run of this script passes the same one.
app_version="$version"

# Why the tree is no longer the commit of this build, '' while it is: HEAD moved or app/ has local changes.
source_moved() {
  if [ "$(git -C "$ROOT" rev-parse HEAD)" != "$full_commit" ]; then
    printf '%s' "HEAD moved from $commit to $(git -C "$ROOT" rev-parse --short HEAD) during the build"
  elif [ -n "$(git -C "$ROOT" status --porcelain -- app)" ]; then
    printf '%s' "app/ changed during the build"
  fi
}

export PERCEPTION_KEYSTORE="$KEYS/$KEY_FILE"
PERCEPTION_KEYSTORE_PASSWORD="$(cat "$KEYS/ANDROID_KEYSTORE_PASSWORD.txt")"
PERCEPTION_KEY_ALIAS="$(cat "$KEYS/ANDROID_KEY_ALIAS.txt")"
PERCEPTION_KEY_PASSWORD="$(cat "$KEYS/ANDROID_KEY_PASSWORD.txt")"
export PERCEPTION_KEYSTORE_PASSWORD PERCEPTION_KEY_ALIAS PERCEPTION_KEY_PASSWORD

gradle_run() {
  (cd "$ROOT/app/android" && nice -n 10 "$TOOLS/gradle-$GRADLE_VERSION/bin/gradle" "$@" \
    --no-daemon --console=plain -q -PperceptionVersionCode="$code" -PperceptionVersionName="$app_version")
}

say "Perception for Android $version (versionCode $code, commit $commit), signed with the key of $KEYS"
tasks=(:app:testDebugUnitTest)
[ "$LINT" -eq 1 ] && tasks+=(:app:lintRelease)
tasks+=(:app:assembleRelease :app:bundleRelease)
if [ "$VERIFICATION" -eq 1 ]; then
  say "writing the SHA-256 of every dependency to app/android/gradle/verification-metadata.xml"
  tasks+=(--write-verification-metadata sha256)
else
  [ -s "$ROOT/app/android/gradle/verification-metadata.xml" ] \
    || die "app/android/gradle/verification-metadata.xml is missing: the dependencies would not be checked"
fi
gradle_run "${tasks[@]}"
# A build whose tree did not stay its commit until the end may hold changes of no commit: a development build,
# never published (checked once Gradle has read everything; Google Play then gets this very bundle, never one
# built again, and the tag goes on $full_commit).
moved=""
if [ "$clean_version" = "$version" ]; then
  moved="$(source_moved)"
  if [ -n "$moved" ]; then
    say "WARNING: $moved: this build is $version-dev, never published"
    version="$version-dev"
  fi
fi

apk="$ROOT/app/android/app/build/outputs/apk/release/app-release.apk"
aab="$ROOT/app/android/app/build/outputs/bundle/release/app-release.aab"
[ -s "$apk" ] || die "the build made no APK"
[ -s "$aab" ] || die "the build made no AAB"

# -- the signatures, checked before anything is offered: the APK as a phone checks it (APK Signature Scheme v2;
#    minSdk 26 needs no v1 JAR signature), the AAB as Google Play checks the upload (a JAR signature) --
# (Each output read whole, never cut short by the first match: a broken pipe would fail the check.)
signer="$(PATH="$JAVA_HOME/bin:$PATH" "$ANDROID_HOME/build-tools/$BUILD_TOOLS/apksigner" verify --print-certs "$apk" 2>/dev/null \
  | awk -F': ' '/Signer #1 certificate SHA-256 digest/ && !found { print $2; found = 1 }')" || signer=""
[ -n "$signer" ] || die "the signature of the APK does not verify"
aab_check="$("$JAVA_HOME/bin/jarsigner" -J-Duser.language=en -verify "$aab" 2>&1)" || aab_check=""
grep -q '^jar verified[.]' <<< "$aab_check" || die "the signature of the AAB does not verify (jarsigner -verify)"
# The certificate Play Console shows for the upload key (Test and release > App integrity).
certificate="$("$JAVA_HOME/bin/keytool" -J-Duser.language=en -printcert -jarfile "$aab" \
  | awk '/SHA256:/ && !found { print $2; found = 1 }')" || certificate=""
[ "$(printf '%s' "$certificate" | tr -d ':' | tr 'A-F' 'a-f')" = "$signer" ] \
  || die "the AAB is not signed with the key of the APK"

name="perception-android-$version.apk"
bundle="perception-android-$version.aab"
cp "$apk" "$OUT/$name.part" && mv "$OUT/$name.part" "$OUT/$name"
cp "$aab" "$OUT/$bundle.part" && mv "$OUT/$bundle.part" "$OUT/$bundle"
sha="$(sha256sum "$OUT/$name" | cut -d' ' -f1)"
aab_sha="$(sha256sum "$OUT/$bundle" | cut -d' ' -f1)"
# The Python of the project (install.sh made it); a system python3 otherwise.
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
"$PYTHON" -I - "$OUT/latest.json" "$name" "$version" "$code" "$commit" "$sha" "$bundle" "$aab_sha" <<'EOF'
import json, os, sys, time
path, name, version, code, commit, sha, bundle, bundle_sha = sys.argv[1:]
folder = os.path.dirname(path)
data = {'version': version, 'version_code': int(code), 'file': name, 'size': os.path.getsize(os.path.join(folder, name)),
        'sha256': sha, 'aab': bundle, 'aab_size': os.path.getsize(os.path.join(folder, bundle)), 'aab_sha256': bundle_sha,
        'built': time.time(), 'commit': commit}
with open(path + '.part', 'w') as out:
    json.dump(data, out, indent=1)
os.replace(path + '.part', path)
EOF
# Keep the last 3 APKs and AABs.
for kind in apk aab; do
  find "$OUT" -maxdepth 1 -name "perception-android-*.$kind" -printf '%T@ %p\n' | sort -rn | tail -n +4 | cut -d' ' -f2- \
    | xargs -r rm -f
done
megabytes() { awk -v b="$(stat -c %s "$1")" 'BEGIN { printf "%.1f MB", b / 1e6 }'; }
say "APK: outputs/android/$name ($(megabytes "$OUT/$name"), sha256 $sha)"
say "AAB: outputs/android/$bundle ($(megabytes "$OUT/$bundle"), sha256 $aab_sha)"
say "Signing certificate SHA-256 (the upload key of Google Play): $certificate"

# -- Google Play --
tag="perception-android-v$version"
# Why a real run would not publish this build, '' when it would: only a committed version that GitHub has too.
why_not=""
published=0
if [ "$PLAY" = off ]; then
  why_not="--no-play"
elif [ ! -f "$PLAY_KEY" ]; then
  why_not="no key of the Google Play service account in $PLAY_KEY (app/android/README.md, Google Play)"
elif [ -n "$moved" ]; then
  why_not="$moved: build again once nothing changes app/"
elif [ "$clean_version" != "$version" ]; then
  why_not="app/ has local changes: commit and push them first"
else
  branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  if [ "$branch" = HEAD ]; then
    why_not="HEAD is not on a branch"
  elif ! git -C "$ROOT" fetch -q origin "$branch"; then
    why_not="GitHub (origin) cannot be reached to check that $branch is pushed"
  elif [ "$full_commit" != "$(git -C "$ROOT" rev-parse "origin/$branch")" ]; then
    why_not="$commit is not origin/$branch: push first, Google Play gets the pushed commit"
  elif git -C "$ROOT" rev-parse -q --verify "refs/tags/$tag" > /dev/null \
      || git -C "$ROOT" ls-remote --exit-code --tags origin "refs/tags/$tag" > /dev/null 2>&1; then
    published=1
  fi
fi
export PERCEPTION_PLAY_CREDENTIALS="$PLAY_KEY"

PLAY_LOG="$(mktemp)"
# The bundle Google Play gets: a copy of the one checked above, alone in its folder (Gradle Play Publisher uploads
# every bundle of the folder it is given, and builds none).
PLAY_BUNDLE="$(mktemp -d)"
trap 'rm -f "$PLAY_LOG"; rm -rf "$PLAY_BUNDLE"' EXIT
cp "$OUT/$bundle" "$PLAY_BUNDLE/$bundle"
[ "$(sha256sum "$PLAY_BUNDLE/$bundle" | cut -d' ' -f1)" = "$aab_sha" ] || die "the copy of $bundle for Google Play differs"
# A task of Gradle Play Publisher, its output shown and kept in $PLAY_LOG. Every run starts a new edit of Google
# Play: a real run never commits what an earlier dry run (or an interrupted run) left in the saved one.
play_gradle() {
  local status=0
  rm -rf "$PLAY_EDIT"
  if [ "$PLAY" = dry ]; then
    gradle_run "$@" -PperceptionPlayArtifactDir="$PLAY_BUNDLE" --no-commit 2>&1 | tee "$PLAY_LOG" || status=$?
    rm -rf "$PLAY_EDIT"
  else
    gradle_run "$@" -PperceptionPlayArtifactDir="$PLAY_BUNDLE" 2>&1 | tee "$PLAY_LOG" || status=$?
  fi
  return "$status"
}

# A version that Google Play has (published by this script, or uploaded by hand): tagged on the commit it was built
# from, and the tag pushed.
tag_published() {
  if git -C "$ROOT" tag "$tag" "$full_commit" && git -C "$ROOT" push -q origin "refs/tags/$tag"; then
    say "tagged and pushed $tag"
  else
    say "WARNING: the tag $tag was not pushed (above); Google Play has the version"
  fi
}

# What Google Play answered, in plain words, after a failed task. first_upload: the app has no version on Google
# Play yet; already_there: Google Play has this versionCode (an upload by hand).
first_upload=0
already_there=0
explain_failure() {
  if grep -q -e 'No application found for the package name' -e 'applicationNotFound' -e 'Package not found' "$PLAY_LOG"; then
    first_upload=1
    say ""
    say "Google Play has no version of $PLAY_APP ($PACKAGE) yet, and Google takes the first version of a new app"
    say "only by hand. ACTION NEEDED:"
    if [ "$clean_version" != "$version" ]; then
      say "  commit and push app/, run helpers/build_android.sh again, then"
      say "  upload outputs/android/perception-android-<version>.aab of that build by hand in Play Console > Internal testing > Create release; from the next version on this script uploads by itself."
    else
      say "  upload outputs/android/$bundle by hand in Play Console > Internal testing > Create release; from the next version on this script uploads by itself."
    fi
    say "  (Play Console already shows a version of $PLAY_APP? Then the service account has no access to it:"
    say "  Play Console > Users and permissions > the service account > App permissions.)"
  elif grep -qi 'has already been used' "$PLAY_LOG"; then
    already_there=1
    say "Google Play already has versionCode $code ($version, uploaded by hand?)."
  elif grep -q -e 'The caller does not have permission' -e '403 Forbidden' "$PLAY_LOG"; then
    say "The service account has no permission on $PLAY_APP: Play Console > Users and permissions > the service"
    say "account > App permissions > $PLAY_APP: Release to testing tracks, Manage testing tracks (and Manage store"
    say "presence for --listing)."
  elif grep -q 'Only releases with status draft may be created on draft app' "$PLAY_LOG"; then
    say "$PLAY_APP is still a draft in Play Console: roll out its first internal release there by hand and finish"
    say "the set-up of the app (Dashboard), then this script publishes by itself."
  elif grep -q -e 'not authenticated' -e '401 Unauthorized' -e 'invalid_grant' "$PLAY_LOG"; then
    say "Google refuses the key of the service account in $PLAY_KEY: make a new JSON key in Google Cloud"
    say "(IAM > Service accounts > Keys) and put it there."
  fi
}

problem() {
  if [ "$PLAY" = strict ]; then
    die "$1"
  fi
  say "WARNING: $1"
}

dry_note=""
if [ "$PLAY" = dry ]; then
  dry_note=" (dry run: nothing is committed on Google Play, no tag)"
  if [ -n "$why_not" ]; then
    say "A real run would not publish: $why_not. The dry run tries Google Play all the same."
  fi
  why_not=""
fi
if [ -n "$why_not" ]; then
  if [ "$PLAY" = strict ]; then
    die "not published on Google Play: $why_not"
  fi
  say "Not published on Google Play: $why_not."
elif [ "$published" -eq 1 ] && [ "$TRACK" = internal ]; then
  say "Google Play already has $version ($tag)."
elif [ "$published" -eq 1 ]; then
  # A version code goes up once: on another track it is a promotion of the internal release.
  if play_gradle :app:promoteReleaseArtifact --from-track internal --promote-track "$TRACK" --version-code "$code"; then
    if [ "$PLAY" = dry ]; then
      say "Dry run: Google Play validated the promotion of $version to the $TRACK track$dry_note."
    else
      say "Promoted $version to the $TRACK track of Google Play: https://play.google.com/console"
    fi
  else
    explain_failure
    problem "$version was not promoted to the $TRACK track of Google Play (above)$dry_note"
  fi
elif play_gradle :app:publishReleaseBundle --track "$TRACK"; then
  if [ "$PLAY" = dry ]; then
    say "Dry run: Google Play took and validated $version for the $TRACK track$dry_note."
  else
    say "Published $version on the $TRACK track of Google Play: https://play.google.com/console"
    tag_published
  fi
else
  explain_failure
  if [ "$already_there" -eq 1 ] && [ "$PLAY" != dry ]; then
    tag_published
  elif [ "$first_upload" -eq 1 ] && [ "$PLAY" != strict ]; then
    say "Not published on Google Play: the first version goes by hand (above)$dry_note."
  else
    problem "$version was not published on Google Play (above); the APK of the site is built$dry_note"
  fi
fi
if [ "$LISTING" -eq 1 ]; then
  # --rerun: Gradle would take the listing for done after an upload that Google Play then refused.
  if play_gradle :app:publishReleaseListing --rerun; then
    say "Store listing uploaded to Google Play$dry_note."
  else
    explain_failure
    die "the store listing was not uploaded to Google Play (above)$dry_note"
  fi
fi
say "The APK for the site: outputs/android/$name."
