#!/usr/bin/env bash
set -euo pipefail

# recover-orphaned-archives.sh — rescue download folders unpackerr can never
# reach, on its own, because it only ever acts on items still present in an
# *arr queue.
#
# Ansible-managed: installed on cmp01 by
# roles/services/media/arr/tasks/main.yml ("Install orphaned-archive recovery
# script") from this file. Edit here, not on the host — a redeploy overwrites
# any local change.
#
# THE PROBLEM
# -----------
# Scene releases are frequently posted as obfuscated OUTER archives
# (5rpm6ZPLeTV8sMN69.part01.rar ...). nzbget unpacks those ONCE, producing an
# INNER scene rar set (name.rar, name.r00-name.r26) inside a nested folder,
# alongside Sample/ and Proof/. nzbget does not recurse: it reports
# UnpackStatus=SUCCESS and moves on. Sonarr/Radarr then scan the folder, find
# only Sample/*.mkv (~28 MB), reject it as "Sample", and park the grab at
# importPending forever.
#
# unpackerr (see defaults/main.yml) fixes this going forward by polling the
# *arr queues and extracting whatever those queue items still point at. But
# by definition it can only act on a download that is STILL a queue item. A
# grab that Sonarr/Radarr already gave up on and dropped from the queue — or
# one that was manually removed without "delete files" — leaves an orphaned
# folder on disk that unpackerr will never look at again, because nothing
# ever asks it to.
#
# Confirmed orphans as of 2026-09-20: "Batman: Caped Crusader" S01+S02 (20
# folders, series sits at 0/20 episodes in Sonarr) and
# "Once.Upon.a.Time.in.Hollywood.2019..." (Radarr hasFile: false). Run this
# script BY HAND on cmp01 to sweep and recover them; it is not on a timer.
#
# WHY EVERYTHING RUNS THROUGH `docker exec nzbget`
# -------------------------------------------------
# All filesystem work below (find, unrar) runs inside the nzbget container
# against ITS paths (/data/downloads/usenet/...), never the host's. Two
# reasons: it sidesteps host<->container path translation entirely (the host
# mounts the same data pool at a different path), and nzbget's own image
# already ships unrar 7.23 at /usr/bin/unrar — the host has no unrar at all.
#
# USAGE
#   recover-orphaned-archives.sh            # dry run (default): print, do nothing
#   recover-orphaned-archives.sh --apply    # actually extract + trigger import scans

APPLY=false
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=true
fi

if $APPLY; then
  echo "== mode: APPLY (will extract archives and trigger *arr import scans) =="
else
  echo "== mode: DRY RUN (default; pass --apply to actually recover) =="
fi

SONARR_URL="http://localhost:8989/api/v3"
RADARR_URL="http://localhost:7878/api/v3"
USENET_ROOT="/data/downloads/usenet"

# ---- API keys, read straight out of each app's config.xml ----
SONARR_KEY="$(docker exec sonarr sed -n 's|.*<ApiKey>\(.*\)</ApiKey>.*|\1|p' /config/config.xml)"
RADARR_KEY="$(docker exec radarr sed -n 's|.*<ApiKey>\(.*\)</ApiKey>.*|\1|p' /config/config.xml)"

if [[ -z "$SONARR_KEY" || -z "$RADARR_KEY" ]]; then
  echo "ERROR: could not read an API key out of sonarr/radarr config.xml — is the stack up?" >&2
  exit 1
fi

# ---- SKIP set: release names still owned by unpackerr (present in a queue) ----
# Anything still in a queue belongs to unpackerr, not us — touching it here
# would race unpackerr's own extract/import. Parsed with python3 (present on
# cmp01) rather than jq, per house convention for one-off scripts like this.
echo "-- fetching Sonarr/Radarr queues to build the skip set --"
SKIP_NAMES="$(python3 - "$SONARR_URL" "$SONARR_KEY" "$RADARR_URL" "$RADARR_KEY" <<'PYEOF'
import json
import sys
import urllib.request

sonarr_url, sonarr_key, radarr_url, radarr_key = sys.argv[1:5]

names = set()
for url, key in ((sonarr_url, sonarr_key), (radarr_url, radarr_key)):
    req = urllib.request.Request(
        f"{url}/queue?pageSize=200", headers={"X-Api-Key": key}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    for rec in data.get("records", []):
        title = rec.get("title")
        if title:
            names.add(title)

for name in sorted(names):
    print(name)
PYEOF
)"

skip_count=0
if [[ -n "$SKIP_NAMES" ]]; then
  skip_count="$(printf '%s\n' "$SKIP_NAMES" | wc -l | tr -d ' ')"
fi
echo "-- ${skip_count} release(s) currently owned by an *arr queue (unpackerr's territory) --"

is_skipped() {
  # Exact match against the SKIP set built above. A release folder name that
  # does not literally match its queue "title" (e.g. dots vs spaces) will
  # not be recognised as skipped — acceptable here: worst case we re-extract
  # a release unpackerr is also about to handle, which is a harmless no-op
  # (unrar -o- never overwrites).
  local name="$1"
  [[ -n "$SKIP_NAMES" ]] && grep -qxF "$name" <<< "$SKIP_NAMES"
}

# ---- candidate dirs: anything holding an inner scene rar set (*.r00) ----
echo "-- scanning ${USENET_ROOT} for inner scene rar sets (*.r00) --"
CANDIDATES="$(docker exec nzbget sh -c "find '${USENET_ROOT}' -name '*.r00' -printf '%h\n' | sort -u")"

if [[ -z "$CANDIDATES" ]]; then
  echo "No orphaned archives found under ${USENET_ROOT}."
  echo "== mode: $($APPLY && echo APPLY || echo 'DRY RUN') — nothing to do =="
  exit 0
fi

skipped=0
extracted=0
failed=0
scans_triggered=0
declare -a series_scan_dirs=()
declare -a movies_scan_dirs=()

while IFS= read -r cand; do
  [[ -z "$cand" ]] && continue

  # Derive the TOP-LEVEL release dir (child of USENET_ROOT/<Category>/) and
  # its category, from a candidate that may be nested arbitrarily deeper.
  rel="${cand#"${USENET_ROOT}"/}"
  category="${rel%%/*}"
  rest="${rel#*/}"
  release_name="${rest%%/*}"
  top_dir="${USENET_ROOT}/${category}/${release_name}"

  if is_skipped "$release_name"; then
    echo "SKIP  (queue-owned, unpackerr's job): ${release_name}"
    skipped=$((skipped + 1))
    continue
  fi

  rar_file="$(docker exec nzbget sh -c "find '${cand}' -maxdepth 1 -iname '*.rar' | sort | head -1")"
  if [[ -z "$rar_file" ]]; then
    echo "FAIL  no .rar found alongside *.r00 in: ${cand}"
    failed=$((failed + 1))
    continue
  fi

  echo "EXTRACT ${release_name}  (${cand})"
  if $APPLY; then
    # One bad archive set must not abort the whole sweep — guard set -e
    # around this single extract, capture its exit code, and keep going.
    set +e
    docker exec nzbget unrar x -o- -y "$rar_file" "${cand}/"
    rc=$?
    set -e

    if [[ $rc -ne 0 ]]; then
      echo "FAIL  unrar exited ${rc} for: ${cand}"
      failed=$((failed + 1))
      continue
    fi

    # Verify: a real video file, not the Sample, must now exist under the
    # top-level release dir.
    found="$(docker exec nzbget sh -c "find '${top_dir}' -type f \
      \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' \) \
      -size +100M -not -path '*/Sample/*' | head -1")"

    if [[ -z "$found" ]]; then
      echo "FAIL  extracted but no >100MB video found (excluding Sample/) under: ${top_dir}"
      failed=$((failed + 1))
      continue
    fi

    echo "OK    extracted -> ${found}"
    extracted=$((extracted + 1))
  else
    echo "  [dry-run] would: unrar x -o- -y '${rar_file}' '${cand}/'"
    extracted=$((extracted + 1))
  fi

  case "$category" in
    Series)
      if [[ ! " ${series_scan_dirs[*]-} " == *" ${top_dir} "* ]]; then
        series_scan_dirs+=("$top_dir")
      fi
      ;;
    Movies)
      if [[ ! " ${movies_scan_dirs[*]-} " == *" ${top_dir} "* ]]; then
        movies_scan_dirs+=("$top_dir")
      fi
      ;;
    *)
      echo "  note: category '${category}' has no import-scan mapping (Series/Movies only) — skipping scan trigger"
      ;;
  esac
done <<< "$CANDIDATES"

# ---- trigger import scans on the TOP-LEVEL release dirs we recovered ----
# Both command names are verified working against Sonarr/Radarr (return
# 201). importMode is deliberately not set — let each app use its own
# configured Completed Download Handling.
trigger_scan() {
  local url="$1" key="$2" command_name="$3" path="$4"
  echo "SCAN  ${command_name} -> ${path}"
  if $APPLY; then
    curl -sS -o /dev/null -w '%{http_code}\n' \
      -X POST "${url}/command" \
      -H "X-Api-Key: ${key}" \
      -H 'Content-Type: application/json' \
      -d "{\"name\":\"${command_name}\",\"path\":\"${path}\"}"
  else
    echo "  [dry-run] would POST ${command_name} for '${path}'"
  fi
  scans_triggered=$((scans_triggered + 1))
}

for d in "${series_scan_dirs[@]-}"; do
  [[ -z "$d" ]] && continue
  trigger_scan "$SONARR_URL" "$SONARR_KEY" "DownloadedEpisodesScan" "$d"
done

for d in "${movies_scan_dirs[@]-}"; do
  [[ -z "$d" ]] && continue
  trigger_scan "$RADARR_URL" "$RADARR_KEY" "DownloadedMoviesScan" "$d"
done

echo "----"
echo "summary: skipped=${skipped} (queue-owned) extracted=${extracted} failed=${failed} scans_triggered=${scans_triggered}"
if $APPLY; then
  echo "== mode: APPLY — done =="
else
  echo "== mode: DRY RUN — nothing was changed; re-run with --apply to recover =="
fi
