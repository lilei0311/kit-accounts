#!/usr/bin/env bash
# meter-hub-joint-debug-af.sh — local/pre-prod dual-ledger A–F harness (Hub side)
# Boundaries: NO deploy, NO NAS/production, NO real charge, NO minting real secrets.
# Asia/Shanghai wall clock assumed on this box.
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
OPS_DIR="$(cd "$(dirname "$0")" && pwd)"
# Copied from meter-side harness (A/B wired). C–F filled here; do not edit kit-meter.
ACCOUNTS_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEFAULT_REPO="${KIT_METER_REPO_ROOT:-/tmp/dual-ledger/kit-meter}"
REPO_ROOT="${KIT_METER_REPO_ROOT:-$DEFAULT_REPO}"
HUB_BASE="${METER_HUB_BASE_URL:-http://127.0.0.1:8088}"
ACCOUNT_ID="${JOINT_DEBUG_ACCOUNT_ID:-acct_joint_debug}"
POOR_ACCOUNT="${JOINT_DEBUG_POOR_ACCOUNT:-acct_joint_debug_poor}"
SHORT_ACCOUNT="${JOINT_DEBUG_SHORT_ACCOUNT:-acct_joint_debug_short}"
BOOTSTRAP_TOKEN="${KIT_METER_API_TOKEN:-joint-debug-bootstrap-only}"
PG_PORTABLE_HOME="${KIT_METER_PG_PORTABLE_HOME:-/workspace/tmp/pg-portable/postgresql-15.0.4-x86_64-unknown-linux-gnu}"
PG_DATA_DIR="${KIT_METER_JOINT_PG_DATA:-/tmp/kit-meter-joint-debug-pg.data}"
PG_PORT="${KIT_METER_JOINT_PG_PORT:-55433}"
PG_USER="${KIT_METER_JOINT_PG_USER:-kitmeter}"
PG_DB="${KIT_METER_JOINT_PG_DB:-kit_meter_joint}"
EVIDENCE_DIR="${JOINT_DEBUG_EVIDENCE_DIR:-$ACCOUNTS_ROOT/docs/joint-debug-af/evidence}"

PREP_ONLY=0
START_PG=0
SEQ="all"  # A–F; override with --seq A|B|AB|all
DO_MINT_KEY=0

usage() {
  cat <<USAGE
Usage: $SCRIPT_NAME [--prep-only] [--start-pg] [--seq A|B|AB|all] [--mint-key] [-h]

Env (required for live sequences; skipped for --prep-only):
  CALLBACK_URL or KIT_METER_POINTS_CALLBACK_URL
  TOKEN or KIT_METER_POINTS_CALLBACK_TOKEN          (fake joint-debug only)
  CREDENTIAL_ID or KIT_METER_POINTS_CALLBACK_CREDENTIAL_ID

Hub client:
  METER_HUB_BASE_URL          default http://127.0.0.1:8088
  METER_HUB_MSK_TOKEN         customer msk_… (or use --mint-key with Hub up)
  KIT_METER_API_TOKEN         bootstrap for --mint-key only (local fake)
  KIT_METER_REPO_ROOT         default $DEFAULT_REPO
  KIT_METER_PG_DSN            required for Hub PG path unless --start-pg
  JOINT_DEBUG_ALLOW_REMOTE=1  allow non-local callback host (still refuse known prod)

Flags:
  --prep-only   validate tree/uv/portable PG; print checklist; no HTTP posts
  --start-pg    init/start ephemeral portable PG on 127.0.0.1:$PG_PORT (throwaway)
  --seq         which sequences to run when env set (default AB)
  --mint-key    POST /v1alpha/keys once (local Hub) and print token
USAGE
}

log() { printf '[%s] %s\n' "$SCRIPT_NAME" "$*"; }
die() { log "ERROR: $*"; exit 1; }
die2() { log "ERROR: $*"; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prep-only) PREP_ONLY=1; shift ;;
    --start-pg) START_PG=1; shift ;;
    --seq) SEQ="${2:-}"; shift 2 ;;
    --mint-key) DO_MINT_KEY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown arg: $1 (see --help)" ;;
  esac
done

CALLBACK_URL="${CALLBACK_URL:-${KIT_METER_POINTS_CALLBACK_URL:-}}"
TOKEN="${TOKEN:-${KIT_METER_POINTS_CALLBACK_TOKEN:-}}"
CREDENTIAL_ID="${CREDENTIAL_ID:-${KIT_METER_POINTS_CALLBACK_CREDENTIAL_ID:-}}"

# --- prod hostname refuse ---
refuse_prod_callback() {
  local url="$1"
  [[ -z "$url" ]] && return 0
  local host
  host="$(printf '%s' "$url" | sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://##' | cut -d/ -f1 | cut -d@ -f2 | cut -d: -f1 | tr '[:upper:]' '[:lower:]')"
  case "$host" in
    *orangepiaipro*|*z4-he2g*|*tail778c28*)
      if [[ "${JOINT_DEBUG_ALLOW_REMOTE:-}" != "1" ]]; then
        die2 "CALLBACK_URL host looks like known prod/shared service ('$host'). Refusing. Override only with JOINT_DEBUG_ALLOW_REMOTE=1 for explicit pre-prod you own — still never NAS/billing."
      fi
      log "WARN: JOINT_DEBUG_ALLOW_REMOTE=1 with prod-fingerprint host '$host' — confirm this is NOT production billing."
      ;;
  esac
  if [[ "${JOINT_DEBUG_ALLOW_REMOTE:-}" != "1" ]]; then
    case "$host" in
      localhost|127.0.0.1|::1|\[::1\]) ;;
      *.ts.net|*.tailscale.net)
        log "WARN: MagicDNS callback host '$host' — allowed only as pre-prod; confirm not production billing."
        ;;
      *)
        die2 "CALLBACK_URL host '$host' is not loopback. Set JOINT_DEBUG_ALLOW_REMOTE=1 for intentional pre-prod, or use 127.0.0.1."
        ;;
    esac
  fi
}

require_callback_env() {
  local missing=()
  [[ -z "$CALLBACK_URL" ]] && missing+=("CALLBACK_URL|KIT_METER_POINTS_CALLBACK_URL")
  [[ -z "$TOKEN" ]] && missing+=("TOKEN|KIT_METER_POINTS_CALLBACK_TOKEN")
  [[ -z "$CREDENTIAL_ID" ]] && missing+=("CREDENTIAL_ID|KIT_METER_POINTS_CALLBACK_CREDENTIAL_ID")
  if ((${#missing[@]})); then
    die2 "missing env: ${missing[*]}. Accounts HTTP shell not ready? Leave unset and use --prep-only until they return URL/Bearer/credential_id. Do not mint real secrets."
  fi
  refuse_prod_callback "$CALLBACK_URL"
}

# --- helpers ---
json_get() {
  # tiny extractor: json_get <key> from stdin (string values / nested request_id)
  local key="$1"
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1],""))' "$key" 2>/dev/null || true
}

hub_curl() {
  local method="$1" path="$2" body="${3:-}"
  local auth="${METER_HUB_MSK_TOKEN:-}"
  local args=(-sS -X "$method" "${HUB_BASE}${path}" -H "Content-Type: application/json")
  if [[ -n "$auth" ]]; then
    args+=(-H "Authorization: Bearer $auth")
  fi
  if [[ -n "$body" ]]; then
    args+=(-d "$body")
  else
    args+=(-d '{}')
  fi
  curl "${args[@]}"
}

estimate_json='{"model_id":"demo-text-v0","modality":"text","unit_kind":"tokens","input_units":100,"output_units":20}'
usage_event_json='{"model_id":"demo-text-v0","modality":"text","unit_kind":"tokens","input_units":80,"output_units":16}'
estimate_c_json='{"model_id":"demo-text-v0","modality":"text","unit_kind":"tokens","input_units":0,"output_units":100000}'
estimate_f_json='{"model_id":"demo-text-v0","modality":"text","unit_kind":"tokens","input_units":0,"output_units":5000}'
usage_f_json='{"model_id":"demo-text-v0","modality":"text","unit_kind":"tokens","input_units":0,"output_units":10000}'

pump_outbox() {
  log "pump POST /v1/ops/dispatch-points-outbox"
  hub_curl POST /v1/ops/dispatch-points-outbox '{}' | tee "$EVIDENCE_DIR/dispatch-$(date +%Y%m%d-%H%M%S).json"
  echo
}

create_task() {
  local rid_hint="${1:-}"
  local account="${2:-$ACCOUNT_ID}"
  local estimate="${3:-$estimate_json}"
  local body
  if [[ -n "$rid_hint" ]]; then
    body=$(printf '{"account_id":"%s","billing":"platform","estimate":%s,"request_id":"%s"}' "$account" "$estimate" "$rid_hint")
  else
    body=$(printf '{"account_id":"%s","billing":"platform","estimate":%s}' "$account" "$estimate")
  fi
  hub_curl POST /v1/tasks "$body"
}

# --- ephemeral PG ---
start_ephemeral_pg() {
  local bin="$PG_PORTABLE_HOME/bin"
  [[ -x "$bin/initdb" && -x "$bin/pg_ctl" ]] || die "portable PG not found at $PG_PORTABLE_HOME (see runbook; set KIT_METER_PG_DSN yourself)"
  export PATH="$bin:$PATH"
  mkdir -p "$(dirname "$PG_DATA_DIR")"
  if [[ ! -f "$PG_DATA_DIR/PG_VERSION" ]]; then
    log "initdb $PG_DATA_DIR"
    initdb -D "$PG_DATA_DIR" --auth-local=trust --auth-host=trust -U "$PG_USER" --no-locale -E UTF8 >/tmp/kit-meter-joint-pg-init.log 2>&1
    {
      echo "listen_addresses = '127.0.0.1'"
      echo "port = $PG_PORT"
      echo "unix_socket_directories = '/tmp'"
    } >> "$PG_DATA_DIR/postgresql.conf"
  fi
  if pg_ctl -D "$PG_DATA_DIR" status >/dev/null 2>&1; then
    log "portable PG already running (data=$PG_DATA_DIR)"
  else
    log "pg_ctl start -D $PG_DATA_DIR -o '-p $PG_PORT -h 127.0.0.1'"
    pg_ctl -D "$PG_DATA_DIR" -l /tmp/kit-meter-joint-pg.log start -o "-p $PG_PORT -h 127.0.0.1" >/tmp/kit-meter-joint-pg-start.log 2>&1
    sleep 1
  fi
  if ! psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres -Atc "SELECT 1" >/dev/null 2>&1; then
    die "PG started but cannot connect on 127.0.0.1:$PG_PORT"
  fi
  psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" | grep -q 1 \
    || psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $PG_DB OWNER $PG_USER;"
  export KIT_METER_PG_DSN="postgresql://${PG_USER}@127.0.0.1:${PG_PORT}/${PG_DB}"
  log "KIT_METER_PG_DSN=$KIT_METER_PG_DSN (throwaway; never production)"
  apply_sql_local
}

apply_sql_local() {
  [[ -n "${KIT_METER_PG_DSN:-}" ]] || die "KIT_METER_PG_DSN unset; cannot apply SQL"
  local sql="$REPO_ROOT/packages/meter/sql"
  log "apply 001 + 003 only (skip production; 002 optional for key mint)"
  psql "$KIT_METER_PG_DSN" -v ON_ERROR_STOP=1 -f "$sql/001_meter_and_cost.sql"
  # 002 needed if minting msk keys against PG stores
  if [[ -f "$sql/002_meter_api_keys.sql" ]]; then
    log "also apply 002_meter_api_keys.sql (local key table; still not prod)"
    psql "$KIT_METER_PG_DSN" -v ON_ERROR_STOP=1 -f "$sql/002_meter_api_keys.sql"
  fi
  psql "$KIT_METER_PG_DSN" -v ON_ERROR_STOP=1 -f "$sql/003_points_outbox.sql"
}

# --- prep-only ---
run_prep_only() {
  log "=== --prep-only checklist ==="
  local ok=1
  echo "REPO_ROOT=$REPO_ROOT"
  if [[ -d "$REPO_ROOT/.git" ]] || [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
    echo "  [ok] tree present"
    if [[ -d "$REPO_ROOT/.git" ]]; then
      echo -n "  HEAD="; git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "(no git)"
    fi
  else
    echo "  [FAIL] missing kit-meter tree"; ok=0
  fi
  for f in docs/points-callback.md docs/http-api.md packages/meter/sql/001_meter_and_cost.sql packages/meter/sql/003_points_outbox.sql; do
    if [[ -f "$REPO_ROOT/$f" ]]; then echo "  [ok] $f"; else echo "  [FAIL] $f"; ok=0; fi
  done
  if command -v uv >/dev/null 2>&1; then
    echo "  [ok] uv $(uv --version 2>/dev/null | head -1)"
  else
    echo "  [FAIL] uv not on PATH"; ok=0
  fi
  if [[ -x "$REPO_ROOT/.venv/bin/kit-meter-hub" ]] || [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    echo "  [ok] venv present (.venv)"
  else
    echo "  [warn] no .venv yet — run: cd \$REPO_ROOT && uv sync --locked --extra http"
  fi
  if [[ -x "$PG_PORTABLE_HOME/bin/initdb" ]]; then
    echo "  [ok] portable PG at $PG_PORTABLE_HOME"
  else
    echo "  [warn] portable PG missing — operator must set KIT_METER_PG_DSN to a local throwaway"
  fi
  echo "  CALLBACK_URL=${CALLBACK_URL:-<unset>}"
  echo "  TOKEN=${TOKEN:+<set>}${TOKEN:-<unset>}"
  echo "  CREDENTIAL_ID=${CREDENTIAL_ID:-<unset>}"
  echo "  KIT_METER_PG_DSN=${KIT_METER_PG_DSN:-<unset>}"
  echo "  HUB_BASE=$HUB_BASE"
  echo
  cat <<'CHECK'
Next (operator / when accounts returns shell):
  1) export CALLBACK_URL TOKEN CREDENTIAL_ID  (fake joint-debug only)
  2) optional: $0 --start-pg   OR export KIT_METER_PG_DSN=...
  3) export KIT_METER_POINTS_CALLBACK_URL=$CALLBACK_URL \\
           KIT_METER_POINTS_CALLBACK_TOKEN=$TOKEN \\
           KIT_METER_POINTS_CALLBACK_CREDENTIAL_ID=$CREDENTIAL_ID \\
           KIT_METER_API_TOKEN=joint-debug-bootstrap-only \\
           KIT_METER_HTTP_HOST=127.0.0.1 KIT_METER_HTTP_PORT=8088
  4) cd $REPO_ROOT && uv run python -m maxkit_meter.http
  5) $0 --mint-key   then export METER_HUB_MSK_TOKEN=msk_…
  6) $0 --seq AB     # live A then B; pump outbox after each phase
Boundaries: no deploy, no NAS, no prod migration, no real charge, no real secrets.
CHECK
  if [[ "$ok" -ne 1 ]]; then
    die "prep-only found failures"
  fi
  log "prep-only OK"
}

mint_key() {
  log "mint local msk key via bootstrap (scopes read+write+ops)"
  local body='{"account_id":"'"$ACCOUNT_ID"'","name":"joint-debug","scopes":["hub.meter.read","hub.meter.write","hub.meter.ops"]}'
  local resp
  resp=$(curl -sS -X POST "$HUB_BASE/v1alpha/keys" \
    -H "Authorization: Bearer $BOOTSTRAP_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$body") || die "key mint curl failed — is Hub up with KIT_METER_API_TOKEN=$BOOTSTRAP_TOKEN?"
  echo "$resp" | tee "$EVIDENCE_DIR/mint-key.json"
  echo
  local tok
  tok=$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))')
  [[ -n "$tok" ]] || die "no token in mint response: $resp"
  log "export METER_HUB_MSK_TOKEN='$tok'  # shown once; local joint-debug only"
  export METER_HUB_MSK_TOKEN="$tok"
}

# --- sequences ---
seq_A() {
  # A: hold → start → freeze (settle). Same request_id both ledgers.
  log "=== Sequence A: hold→start→freeze (settle) ==="
  mkdir -p "$EVIDENCE_DIR"
  local create_resp rid
  create_resp=$(create_task)
  echo "$create_resp" | tee "$EVIDENCE_DIR/A-create.json"; echo
  rid=$(printf '%s' "$create_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
  log "request_id=$rid"
  hub_curl POST "/v1/tasks/${rid}/hold" '{}' | tee "$EVIDENCE_DIR/A-hold.json"; echo
  pump_outbox
  hub_curl POST "/v1/tasks/${rid}/start" '{}' | tee "$EVIDENCE_DIR/A-start.json"; echo
  local freeze_body
  freeze_body=$(printf '{"usage_event":%s,"estimated":null}' "$usage_event_json")
  hub_curl POST "/v1/tasks/${rid}/freeze" "$freeze_body" | tee "$EVIDENCE_DIR/A-freeze.json"; echo
  pump_outbox
  hub_curl GET "/v1/tasks/${rid}" | tee "$EVIDENCE_DIR/A-task.json"; echo
  log "A done. Expect accounts: hold applied(+snapshot_ref) then settle applied. event_id evt_hold_${rid} / evt_settle_${rid}"
}

seq_B() {
  # B: hold → expire-holds (release hold_timeout). Uses clock injection.
  log "=== Sequence B: hold→expire-holds (release/hold_timeout) ==="
  mkdir -p "$EVIDENCE_DIR"
  local create_resp rid
  create_resp=$(create_task)
  echo "$create_resp" | tee "$EVIDENCE_DIR/B-create.json"; echo
  rid=$(printf '%s' "$create_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
  log "request_id=$rid"
  local hold_resp held_at expires_at
  hold_resp=$(hub_curl POST "/v1/tasks/${rid}/hold" '{}')
  echo "$hold_resp" | tee "$EVIDENCE_DIR/B-hold.json"; echo
  pump_outbox
  # Inject now past hold_expires_at (default TTL 900s). Prefer response field; else +901s from held_at.
  expires_at=$(printf '%s' "$hold_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("hold_expires_at") or "")')
  local now_iso
  if [[ -n "$expires_at" ]]; then
    now_iso=$(python3 -c 'from datetime import datetime,timedelta,timezone; import sys
s=sys.argv[1].replace("Z","+00:00")
dt=datetime.fromisoformat(s)+timedelta(seconds=1)
print(dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$expires_at")
  else
    now_iso=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(seconds=901)).strftime("%Y-%m-%dT%H:%M:%SZ"))')
  fi
  log "expire-holds with now=$now_iso"
  hub_curl POST /v1/ops/expire-holds "$(printf '{"now":"%s"}' "$now_iso")" | tee "$EVIDENCE_DIR/B-expire.json"; echo
  pump_outbox
  hub_curl GET "/v1/tasks/${rid}" | tee "$EVIDENCE_DIR/B-task.json"; echo
  log "B done. Expect accounts: hold applied then release reason=hold_timeout. event_id evt_release_${rid}"
}

seq_C() {
  # C: poor account + large estimate → hold rejected. Hub must not start, must not invent release.
  log "=== Sequence C: hold rejected (insufficient) ==="
  mkdir -p "$EVIDENCE_DIR"
  local create_resp rid
  create_resp=$(create_task "" "$POOR_ACCOUNT" "$estimate_c_json")
  echo "$create_resp" | tee "$EVIDENCE_DIR/C-create.json"; echo
  rid=$(printf '%s' "$create_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
  log "request_id=$rid account=$POOR_ACCOUNT"
  hub_curl POST "/v1/tasks/${rid}/hold" '{}' | tee "$EVIDENCE_DIR/C-hold.json"; echo
  pump_outbox
  log "C: start should fail (no model)"
  hub_curl POST "/v1/tasks/${rid}/start" '{}' | tee "$EVIDENCE_DIR/C-start.json"; echo
  log "C: freeze/settle should fail or conflict"
  local freeze_body
  freeze_body=$(printf '{"usage_event":%s,"estimated":false}' "$estimate_c_json")
  hub_curl POST "/v1/tasks/${rid}/freeze" "$freeze_body" | tee "$EVIDENCE_DIR/C-freeze.json"; echo
  pump_outbox
  local now_iso
  now_iso=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(seconds=901)).strftime("%Y-%m-%dT%H:%M:%SZ"))')
  hub_curl POST /v1/ops/expire-holds "$(printf '{"now":"%s"}' "$now_iso")" | tee "$EVIDENCE_DIR/C-expire.json"; echo
  pump_outbox
  hub_curl GET "/v1/tasks/${rid}" | tee "$EVIDENCE_DIR/C-task.json"; echo
  log "C done. Expect accounts rejected; Hub no start; no invented release refund."
}

seq_D() {
  # D: hold applied, ops-replay same event_id (SQL pending), re-pump → duplicate.
  log "=== Sequence D: lost ACK / duplicate hold ==="
  mkdir -p "$EVIDENCE_DIR"
  local create_resp rid
  create_resp=$(create_task)
  echo "$create_resp" | tee "$EVIDENCE_DIR/D-create.json"; echo
  rid=$(printf '%s' "$create_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
  log "request_id=$rid"
  hub_curl POST "/v1/tasks/${rid}/hold" '{}' | tee "$EVIDENCE_DIR/D-hold.json"; echo
  pump_outbox
  if [[ -n "${KIT_METER_PG_DSN:-}" ]]; then
    log "D: reset outbox delivery_status=pending for evt_hold_${rid} (ops replay, not Hub API)"
    psql "$KIT_METER_PG_DSN" -v ON_ERROR_STOP=1 -c \
      "UPDATE meter.points_outbox SET delivery_status='pending' WHERE request_id='${rid}' AND event_id='evt_hold_${rid}';" \
      | tee "$EVIDENCE_DIR/D-outbox-reset.txt" || log "WARN: outbox reset SQL failed (table/column may differ)"
  else
    log "WARN: KIT_METER_PG_DSN unset; second pump may no-op if already delivered"
  fi
  pump_outbox
  hub_curl GET "/v1/tasks/${rid}" | tee "$EVIDENCE_DIR/D-task.json"; echo
  log "D done. Expect accounts: duplicate original_result=applied + original snapshot_ref"
}

seq_E() {
  # E: create without hold, probe fail/cancel/release, then late hold.
  log "=== Sequence E: release-before-hold tombstone ==="
  mkdir -p "$EVIDENCE_DIR"
  local create_resp rid
  create_resp=$(create_task)
  echo "$create_resp" | tee "$EVIDENCE_DIR/E-create.json"; echo
  rid=$(printf '%s' "$create_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
  log "request_id=$rid (no hold yet)"
  hub_curl POST "/v1/tasks/${rid}/fail" '{"reason":"client_abort"}' | tee "$EVIDENCE_DIR/E-fail.json"; echo
  hub_curl POST "/v1/tasks/${rid}/cancel" '{}' | tee "$EVIDENCE_DIR/E-cancel.json"; echo
  hub_curl POST "/v1/tasks/${rid}/release" '{"reason":"hold_timeout"}' | tee "$EVIDENCE_DIR/E-release.json"; echo
  local now_iso
  now_iso=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(seconds=901)).strftime("%Y-%m-%dT%H:%M:%SZ"))')
  hub_curl POST /v1/ops/expire-holds "$(printf '{"now":"%s"}' "$now_iso")" | tee "$EVIDENCE_DIR/E-expire.json"; echo
  pump_outbox
  log "E: late hold — expect rejected, no debit"
  hub_curl POST "/v1/tasks/${rid}/hold" '{}' | tee "$EVIDENCE_DIR/E-late-hold.json"; echo
  pump_outbox
  hub_curl GET "/v1/tasks/${rid}" | tee "$EVIDENCE_DIR/E-task.json"; echo
  log "E done. Expect cancelled_without_hold then late hold rejected."
}

seq_F() {
  # F: short account, hold 0/5000 (4 pts), freeze 0/10000 (8 pts) → shortfall, stop retry.
  log "=== Sequence F: settle shortfall ==="
  mkdir -p "$EVIDENCE_DIR"
  local create_resp rid
  create_resp=$(create_task "" "$SHORT_ACCOUNT" "$estimate_f_json")
  echo "$create_resp" | tee "$EVIDENCE_DIR/F-create.json"; echo
  rid=$(printf '%s' "$create_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
  log "request_id=$rid account=$SHORT_ACCOUNT"
  hub_curl POST "/v1/tasks/${rid}/hold" '{}' | tee "$EVIDENCE_DIR/F-hold.json"; echo
  pump_outbox
  hub_curl POST "/v1/tasks/${rid}/start" '{}' | tee "$EVIDENCE_DIR/F-start.json"; echo
  local freeze_body
  freeze_body=$(printf '{"usage_event":%s,"estimated":false}' "$usage_f_json")
  hub_curl POST "/v1/tasks/${rid}/freeze" "$freeze_body" | tee "$EVIDENCE_DIR/F-freeze.json"; echo
  pump_outbox
  log "F: second pump — Hub should not keep retrying after shortfall"
  pump_outbox
  hub_curl GET "/v1/tasks/${rid}" | tee "$EVIDENCE_DIR/F-task.json"; echo
  log "F done. Expect accounts shortfall; Hub outbox terminal / no retry."
}

run_sequences() {
  [[ -n "${METER_HUB_MSK_TOKEN:-}" ]] || die "METER_HUB_MSK_TOKEN unset (export msk_… or pass --mint-key with Hub up)"
  mkdir -p "$EVIDENCE_DIR"
  case "$SEQ" in
    A) seq_A ;;
    B) seq_B ;;
    AB) seq_A; seq_B ;;
    all)
      seq_A; seq_B; seq_C; seq_D; seq_E; seq_F
      ;;
    C) seq_C ;;
    D) seq_D ;;
    E) seq_E ;;
    F) seq_F ;;
    *) die "unknown --seq $SEQ" ;;
  esac
}

# --- main ---
main() {
  if [[ "$PREP_ONLY" -eq 1 ]]; then
    run_prep_only
    exit 0
  fi

  if [[ "$START_PG" -eq 1 ]]; then
    start_ephemeral_pg
  fi

  require_callback_env

  if [[ "$DO_MINT_KEY" -eq 1 ]]; then
    mkdir -p "$EVIDENCE_DIR"
    mint_key
  fi

  # If only mint/start-pg + env check requested without seq... still run seq when not prep
  run_sequences
}

main "$@"
