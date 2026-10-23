#!/usr/bin/env bash
# Builds Perception for Android (app/android) on this server: the unit tests, Android lint and the release APK
# the site offers for download, in outputs/android/. Never built by GitHub.
# What is missing is set up first, so a fresh server needs nothing by hand:
#   - the tools, in /opt/android (or PERCEPTION_ANDROID_TOOLS): JDK 17 (Temurin 17.0.21+9), Android SDK platform 36 and
#     build-tools 35, Gradle 8.14.3 and its cache in /opt/android/gradle-home (about 1.5 GB; x86_64 only, as the
#     SDK build tools). The same tools as openpilot.distill's app: one copy serves both.
#   - the signing key of this server, shared with openpilot.distill's app (the owner's one upload key for Google
#     Play): ~/.config/distill-android (or PERCEPTION_ANDROID_KEYS) with distill-release.p12 (PKCS12, alias
#     "distill", RSA 4096, ~27 years) and its passwords, made once by whichever app is built first, exactly as
#     openpilot.distill's helpers/build_android.sh makes it. Never in git, never printed; keep a backup: a phone
#     installs an update only when it is signed with the same key as the app it has.
#
#   helpers/build_android.sh [--no-lint] [--setup-only] [--update-verification] [--help]
#     (nothing)              tests, lint, release APK
#     --no-lint              skip Android lint (a quicker build)
#     --setup-only           install the tools and make the key when missing, do not build
#     --update-verification  the full build, writing the SHA-256 of every dependency it resolves to
#                            app/android/gradle/verification-metadata.xml: only after changing a dependency or a
#                            plugin on purpose (review the diff of that file)
#
# The toolchain runs next to the signing key, so every download is pinned: the JDK, the command-line tools and
# Gradle by an exact version and a SHA-256 written here; the Android SDK packages by sdkmanager's own checksums;
# every Maven artifact of the build by Gradle's dependency verification (verification-metadata.xml).
#
# The comma side of the radar screen, app/comma_listener.py, is copied into the APK by the build (never a second
# copy in app/android).
# Version: 0.1.<commits of HEAD>, versionCode 1000 + <commits>: every commit has its own, always higher. "-dev" is
#   added when app/ has local changes.
# Output: outputs/android/perception-android-<version>.apk (the last 3 are kept) and outputs/android/latest.json
#   {version, version_code, file, size, sha256, built, commit}: what the site shows of the last build.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TOOLS="${PERCEPTION_ANDROID_TOOLS:-/opt/android}"
KEYS="${PERCEPTION_ANDROID_KEYS:-$HOME/.config/distill-android}"
KEY_FILE="distill-release.p12"
OUT="$ROOT/outputs/android"
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
for argument in "$@"; do
  case "$argument" in
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    --setup-only) SETUP_ONLY=1 ;;
    --no-lint) LINT=0 ;;
    --update-verification) VERIFICATION=1 ;;
    *) die "invalid option: $argument (see --help)" ;;
  esac
done

need() {
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || die "$command_name is missing (apt install $command_name)"
  done
}

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
ANDROID_KEY_ALIAS.txt.
EOF
  )
}

setup_tools
setup_key
[ "$SETUP_ONLY" -eq 1 ] && { say "Android tools in $TOOLS, signing key in $KEYS: ready"; exit 0; }

[ -s "$ROOT/app/comma_listener.py" ] || die "app/comma_listener.py is missing: the app bundles it"

export JAVA_HOME="$JDK_DIR" ANDROID_HOME="$TOOLS/sdk" GRADLE_USER_HOME="$TOOLS/gradle-home"

count="$(git -C "$ROOT" rev-list --count HEAD)"
commit="$(git -C "$ROOT" rev-parse --short HEAD)"
version="0.1.$count"
# The APK holds app/android and app/comma_listener.py: a change in either is a development build.
if [ -n "$(git -C "$ROOT" status --porcelain -- app)" ]; then
  version="$version-dev"
fi
code=$((1000 + count))

export PERCEPTION_KEYSTORE="$KEYS/$KEY_FILE"
PERCEPTION_KEYSTORE_PASSWORD="$(cat "$KEYS/ANDROID_KEYSTORE_PASSWORD.txt")"
PERCEPTION_KEY_ALIAS="$(cat "$KEYS/ANDROID_KEY_ALIAS.txt")"
PERCEPTION_KEY_PASSWORD="$(cat "$KEYS/ANDROID_KEY_PASSWORD.txt")"
export PERCEPTION_KEYSTORE_PASSWORD PERCEPTION_KEY_ALIAS PERCEPTION_KEY_PASSWORD

gradle_run() {
  (cd "$ROOT/app/android" && nice -n 10 "$TOOLS/gradle-$GRADLE_VERSION/bin/gradle" "$@" \
    --no-daemon --console=plain -q -PperceptionVersionCode="$code" -PperceptionVersionName="$version")
}

say "Perception for Android $version (versionCode $code, commit $commit), signed with the key of $KEYS"
tasks=(:app:testDebugUnitTest)
[ "$LINT" -eq 1 ] && tasks+=(:app:lintRelease)
tasks+=(:app:assembleRelease)
if [ "$VERIFICATION" -eq 1 ]; then
  say "writing the SHA-256 of every dependency to app/android/gradle/verification-metadata.xml"
  tasks+=(--write-verification-metadata sha256)
else
  [ -s "$ROOT/app/android/gradle/verification-metadata.xml" ] \
    || die "app/android/gradle/verification-metadata.xml is missing: the dependencies would not be checked"
fi
gradle_run "${tasks[@]}"

apk="$ROOT/app/android/app/build/outputs/apk/release/app-release.apk"
[ -s "$apk" ] || die "the build made no APK"
mkdir -p "$OUT"
name="perception-android-$version.apk"
cp "$apk" "$OUT/$name.part" && mv "$OUT/$name.part" "$OUT/$name"
sha="$(sha256sum "$OUT/$name" | cut -d' ' -f1)"
# The Python of the project (install.sh made it); a system python3 otherwise.
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
"$PYTHON" -I - "$OUT/latest.json" "$name" "$version" "$code" "$commit" "$sha" <<'EOF'
import json, os, sys, time
path, name, version, code, commit, sha = sys.argv[1:]
folder = os.path.dirname(path)
data = {'version': version, 'version_code': int(code), 'file': name, 'size': os.path.getsize(os.path.join(folder, name)),
        'sha256': sha, 'built': time.time(), 'commit': commit}
with open(path + '.part', 'w') as out:
    json.dump(data, out, indent=1)
os.replace(path + '.part', path)
EOF
# Keep the last 3 APKs.
find "$OUT" -maxdepth 1 -name 'perception-android-*.apk' -printf '%T@ %p\n' | sort -rn | tail -n +4 | cut -d' ' -f2- \
  | xargs -r rm -f
# The signature checked as a phone checks it (APK Signature Scheme v2; minSdk 26 needs no v1 JAR signature).
signer="$(PATH="$JAVA_HOME/bin:$PATH" "$ANDROID_HOME/build-tools/$BUILD_TOOLS/apksigner" verify --print-certs "$OUT/$name" 2>/dev/null \
  | awk -F': ' '/Signer #1 certificate SHA-256 digest/ { print $2; exit }')"
[ -n "$signer" ] || die "the signature of $name does not verify"
say "APK: outputs/android/$name ($(awk -v b="$(stat -c %s "$OUT/$name")" 'BEGIN { printf "%.1f MB", b / 1e6 }'), sha256 $sha)"
say "Signing certificate SHA-256: $signer"
