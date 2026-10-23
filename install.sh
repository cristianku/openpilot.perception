#!/usr/bin/env bash
# All-in-one install of openpilot.perception on a Linux server (Debian/Ubuntu, systemd), from a fresh clone
# to its control site running. Safe to run again: every step checks what is already there and only does what
# is missing.
#
#   1. system packages: git, curl, ca-certificates, util-linux (flock), procps, unzip (the Android build
#      tools), ffmpeg (the experiments decode the road videos with it; apt-get, only the missing ones)
#   2. uv (the Python package manager, https://docs.astral.sh/uv/) in ~/.local/bin, when missing
#   3. the GPU: a Tesla V100 (Volta, compute capability 7.0) needs the CUDA 12 builds of torch, so
#      PERCEPTION_UV_EXTRA=v100 is written in the .env of this clone (permissions 600, outside git); a newer
#      GPU uses the default stack; no GPU is only a warning (the depth baseline needs one)
#   4. the drives: the experiments read the drives of an openpilot.distill clone on this server (next to
#      this one, or the one this repo is a submodule of). Without one it is only a warning: install
#      openpilot.distill too, its drives show up in the site by themselves
#   5. the Python environment: uv sync --locked (with the extra of step 3); uv also downloads the right
#      Python. Refused while an experiment of this clone runs (it would change packages under it)
#   6. the depth model of Experiment 1 (Depth Anything V2 Small, ~100 MB, Apache-2.0): its code and weights
#      at pinned versions, checked against their SHA-256, in ~/.cache/openpilot-perception. A failure (no
#      network) is only a warning: the experiment downloads them when it first runs
#   7. the Android app, Perception for Android (helpers/build_android.sh --no-play): its build tools in
#      /opt/android and the signing key of this server in ~/.config/distill-android (both shared with
#      openpilot.distill's app; the key made once, never in git), then its unit tests, the APK and the AAB
#      in outputs/android. The installer never publishes it: the code update of the site does, on Google
#      Play, when the key of the Play service account is there too (app/android/README.md). A failure (no
#      network, not x86_64) is only a warning: the site does not need the app
#   8. the control site as the systemd service perception-web (deploy/install_web_service.sh): started now
#      and at every boot on port 8767; it prints the address and, until the administrator exists, the setup
#      code to create it on the first visit
#
# Usage: sudo ./install.sh [--dry-run] [--no-model] [--no-android] [--no-service] [--help]
#   --dry-run      show what would be done, change nothing (no root needed)
#   --no-model     skip step 6
#   --no-android   skip step 7 (no Android app on this server)
#   --no-service   stop after step 7 (start the site by hand with web/run.sh)
# Then open the printed address from the PC or the phone: everything else is set from the Settings page.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ENV="$ROOT/.env"
PACKAGES=(git curl ca-certificates util-linux procps unzip ffmpeg)
# Experiments that run Python from .venv: uv sync must not change the packages under them.
BUSY_PATTERN='perception[.](audit|experiments)[.]'

DRY_RUN=0
SERVICE=1
MODEL=1
ANDROID=1

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-service) SERVICE=0 ;;
    --no-model) MODEL=0 ;;
    --no-android) ANDROID=0 ;;
    -h|--help) usage ;;
    *) die "invalid option: $1 (see --help)" ;;
  esac
  shift
done

# Runs a command, or only prints it with --dry-run.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    say "[dry run] $*"
  else
    "$@"
  fi
}

# Sets KEY=value in the .env of the clone, keeping every other line; permissions 600.
set_env_key() {
  local key="$1" value="$2" temporary="$REPO_ENV.tmp"
  if [ "$DRY_RUN" -eq 1 ]; then
    say "[dry run] would write $key=$value in $REPO_ENV (permissions 600)"
    return 0
  fi
  (
    umask 077
    if [ -f "$REPO_ENV" ]; then
      grep -v "^$key=" "$REPO_ENV" > "$temporary" || true
    else
      say '# Secrets and local settings of this clone: the file is in .gitignore, it never goes into git.' > "$temporary"
    fi
    say "$key=$value" >> "$temporary"
    mv "$temporary" "$REPO_ENV"
  )
  chmod 600 "$REPO_ENV"
}

env_value() {
  [ -f "$REPO_ENV" ] || return 0
  sed -n "s/^$1=//p" "$REPO_ENV" | tail -1
}

# True when the folder is a clone of openpilot.distill.
is_distill() {
  [ -f "$1/pyproject.toml" ] && grep -q '^name = "openpilot-distill"' "$1/pyproject.toml"
}

# -- 0. checks --------------------------------------------------------------------------------------
[ "$(uname -s)" = "Linux" ] || die "this installer is for a Linux server"
if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  die "run it as root: sudo $0 (or --dry-run to only see what it would do)"
fi
[ -f "$ROOT/pyproject.toml" ] && [ -f "$ROOT/web/server.py" ] || die "$ROOT is not a clone of openpilot.perception"
say "openpilot.perception: all-in-one install in $ROOT"

# -- 1. system packages -----------------------------------------------------------------------------
step "1. system packages"
missing=()
for package in "${PACKAGES[@]}"; do
  if command -v dpkg-query >/dev/null 2>&1; then
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      missing+=("$package")
    fi
  fi
done
if ! command -v apt-get >/dev/null 2>&1; then
  for command_name in git curl flock ffmpeg; do
    command -v "$command_name" >/dev/null 2>&1 || die "no apt-get on this machine and $command_name is missing: install it first"
  done
  say "no apt-get: the needed commands are there"
elif [ "${#missing[@]}" -eq 0 ]; then
  say "all there"
else
  say "installing: ${missing[*]}"
  run apt-get update -q
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${missing[@]}"
fi

# -- 2. uv ------------------------------------------------------------------------------------------
step "2. uv"
export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
  say "already there: $(uv --version) ($(command -v uv))"
else
  say "installing uv in $HOME/.local/bin (official installer from astral.sh)"
  if [ "$DRY_RUN" -eq 1 ]; then
    say "[dry run] curl -LsSf https://astral.sh/uv/install.sh | sh"
  else
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    command -v uv >/dev/null 2>&1 || die "uv did not install in $HOME/.local/bin"
    say "installed: $(uv --version)"
  fi
fi

# -- 3. GPU -----------------------------------------------------------------------------------------
step "3. GPU"
extra="$(env_value PERCEPTION_UV_EXTRA)"
if command -v nvidia-smi >/dev/null 2>&1 && gpu="$(nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader 2>/dev/null | head -1)" \
    && [ -n "$gpu" ]; then
  say "found: $gpu"
  capability="$(printf '%s' "$gpu" | awk -F', *' '{print $NF}')"
  if [ "$capability" = "7.0" ]; then
    if [ "$extra" != "v100" ]; then
      say "Volta GPU: the CUDA 12 builds of torch are needed (PERCEPTION_UV_EXTRA=v100)"
      set_env_key PERCEPTION_UV_EXTRA v100
    fi
    extra="v100"
  elif [ -n "$extra" ]; then
    say "keeping PERCEPTION_UV_EXTRA=$extra from $REPO_ENV"
  fi
else
  say "WARNING: no NVIDIA GPU found (nvidia-smi). The site and the geometry audit work; the depth baseline"
  say "needs an NVIDIA GPU and its driver."
fi
say "Python packages: default stack${extra:+ + extra $extra}"

# -- 4. drives --------------------------------------------------------------------------------------
step "4. drives (openpilot.distill)"
found=""
configured="$(env_value PERCEPTION_DATA_DIR)"
if [ -n "$configured" ] && [ -d "$configured" ]; then
  found="$configured"
  say "set in $REPO_ENV (PERCEPTION_DATA_DIR)"
elif is_distill "$ROOT/.."; then
  found="$(cd "$ROOT/.." && pwd -P)/data"
  say "this repo is part of openpilot.distill"
else
  for folder in "$ROOT"/../*/; do
    folder="$(cd "$folder" && pwd -P)"
    if [ "$folder" != "$ROOT" ] && is_distill "$folder"; then
      found="$folder/data"
      say "openpilot.distill next to this clone: $folder"
      break
    fi
  done
fi
if [ -n "$found" ] && [ -d "$found" ]; then
  segments="$(find "$found" -mindepth 1 -maxdepth 1 -type d | wc -l)"
  ready="$(find "$found" -mindepth 2 -maxdepth 2 -name localizer.safetensors | wc -l)"
  say "drives: $segments segments, $ready localized (Settings > Drives on the site chooses among the clones)"
else
  say "WARNING: no openpilot.distill clone with drives on this server. Install it next to this one"
  say "(https://github.com/cristianku/openpilot.distill), download and localize drives there: the site"
  say "finds them by itself. Until then the experiments have nothing to run on."
fi

# -- 5. Python environment --------------------------------------------------------------------------
step "5. Python environment (uv sync)"
busy="$(pgrep -a -f -- "$BUSY_PATTERN" || true)"
if [ -n "$busy" ]; then
  say "$busy"
  die "an experiment of this clone is running (above): run the installer again when it has finished"
fi
sync_command=(uv sync --locked)
if [ -n "$extra" ]; then
  sync_command+=(--extra "$extra")
fi
if [ "$DRY_RUN" -eq 1 ]; then
  say "[dry run] cd $ROOT && ${sync_command[*]}"
else
  (cd "$ROOT" && "${sync_command[@]}") || die "uv sync failed (above)"
  (cd "$ROOT" && "$ROOT/.venv/bin/python" -c 'import web.server') || die "the environment does not load the site"
  say "environment ready: $ROOT/.venv"
fi

# -- 6. depth model ---------------------------------------------------------------------------------
step "6. depth model of Experiment 1 (Depth Anything V2 Small)"
if [ "$MODEL" -eq 0 ]; then
  say "skipped (--no-model): the experiment downloads it when it first runs"
elif [ "$DRY_RUN" -eq 1 ]; then
  say "[dry run] download the pinned code and weights into ~/.cache/openpilot-perception (SHA-256 checked)"
elif (cd "$ROOT" && "$ROOT/.venv/bin/python" -c \
    'from perception.models import depth_anything as da; da.ensure_code(); print("weights:", da.ensure_weights())'); then
  say "model ready"
else
  say "WARNING: the model was not downloaded (above). The site works; the depth baseline downloads it when it"
  say "first runs, or run the installer again."
fi

# -- 7. Android app ---------------------------------------------------------------------------------
step "7. Android app (tools, signing key, APK and AAB)"
if [ "$ANDROID" -eq 0 ]; then
  say "skipped (--no-android)"
elif [ "$DRY_RUN" -eq 1 ]; then
  say "[dry run] $ROOT/helpers/build_android.sh --no-play"
elif "$ROOT/helpers/build_android.sh" --no-play; then
  say "Android app ready in outputs/android; the code update of the site publishes it on Google Play"
else
  say "WARNING: the Android app was not built (above). The site works without it; run the installer again,"
  say "or helpers/build_android.sh --no-play, to retry."
fi

# -- 8. site ----------------------------------------------------------------------------------------
step "8. control site"
if [ "$SERVICE" -eq 0 ]; then
  say "skipped (--no-service): start it with  web/run.sh"
  exit 0
fi
if ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
  say "no systemd on this machine: start the site with  web/run.sh  (it prints the address)"
  exit 0
fi
if [ "$DRY_RUN" -eq 1 ]; then
  "$ROOT/deploy/install_web_service.sh" --dry-run
else
  was_active=0
  if systemctl is-active --quiet perception-web.service; then
    was_active=1
  fi
  "$ROOT/deploy/install_web_service.sh"
  # Already running before: restart it so it runs the code and packages just installed (the experiments
  # started from the site keep running, KillMode=process).
  if [ "$was_active" -eq 1 ]; then
    systemctl restart perception-web.service
    say "Site restarted with the installed code."
  fi
fi

say ""
say "Done. Open the address above from the PC or the phone."
