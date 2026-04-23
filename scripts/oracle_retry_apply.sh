#!/bin/bash
# Oracle Resource Manager stack — auto-retry apply until capacity frees up.
#
# Designed to run in Oracle Cloud Shell (terminal in the OCI console):
#   1. Open Oracle Console → top-right "Cloud Shell" icon (>_)
#   2. Paste the two lines below and hit enter:
#        curl -fsSL https://raw.githubusercontent.com/Codetivate/stock-profit-bot/main/scripts/oracle_retry_apply.sh -o ~/retry.sh
#        chmod +x ~/retry.sh && nohup ~/retry.sh <STACK_OCID> > ~/retry.log 2>&1 & disown
#   3. Close the Cloud Shell tab — the script keeps running (nohup + disown).
#   4. Check progress: open Cloud Shell again → `tail -f ~/retry.log`.

set -u

STACK_ID="${1:?Usage: $0 <STACK_OCID> [retry_seconds]}"
SLEEP="${2:-300}"           # 5 min default — create-apply-job has a tenant rate-limit
THROTTLE_SLEEP=900          # 15 min when Oracle returns HTTP 429
MAX_JOB_WAIT_SECONDS=600    # max seconds to wait for one apply job
MAX_LOCK_WAIT_SECONDS=600   # max seconds to wait for a prior job to clear

echo "════════════════════════════════════════════════════════════"
echo "  Oracle auto-retry apply"
echo "    stack : $STACK_ID"
echo "    retry : every ${SLEEP}s when out of capacity"
echo "    start : $(date)"
echo "════════════════════════════════════════════════════════════"

wait_for_stack_idle() {
    # RM serializes jobs per stack. If a prior job is still pending,
    # `create-apply-job` rejects with "stack busy" style errors. Wait
    # for the stack to be idle before enqueuing a new one.
    local waited=0
    while [ $waited -lt $MAX_LOCK_WAIT_SECONDS ]; do
        local running
        running=$(oci resource-manager job list \
            --stack-id "$STACK_ID" \
            --lifecycle-state IN_PROGRESS \
            --all \
            --query 'data[] | length(@)' \
            --raw-output 2>/dev/null || echo 0)
        local accepted
        accepted=$(oci resource-manager job list \
            --stack-id "$STACK_ID" \
            --lifecycle-state ACCEPTED \
            --all \
            --query 'data[] | length(@)' \
            --raw-output 2>/dev/null || echo 0)
        local busy=$(( ${running:-0} + ${accepted:-0} ))
        if [ "$busy" = "0" ]; then
            return 0
        fi
        echo "  ⏳ stack busy ($busy in-flight job(s)), waiting 30s…"
        sleep 30
        waited=$((waited + 30))
    done
    echo "  ⚠ gave up waiting for stack to idle after ${MAX_LOCK_WAIT_SECONDS}s"
    return 1
}

attempt=0
while true; do
    attempt=$((attempt + 1))
    echo ""
    echo "[$(date +%H:%M:%S)] attempt #${attempt} — preparing…"

    wait_for_stack_idle || { sleep "$SLEEP"; continue; }

    # Capture BOTH stdout and stderr so we can see why enqueue failed.
    CREATE_OUT=$(oci resource-manager job create-apply-job \
        --stack-id "$STACK_ID" \
        --execution-plan-strategy AUTO_APPROVED \
        --display-name "auto-retry-${attempt}" 2>&1)
    CREATE_RC=$?

    if [ $CREATE_RC -ne 0 ]; then
        echo "  ⚠ enqueue failed (exit=$CREATE_RC):"
        echo "$CREATE_OUT" | head -15 | sed 's/^/      /'
        # If Oracle rate-limited us, wait much longer before retrying.
        # Resource Manager's create_job API has a tenant-wide throttle
        # that a 3-minute retry loop easily trips.
        if echo "$CREATE_OUT" | grep -qE '"status": 429|"code": "TooManyRequests"'; then
            echo "  ⛔ 429 Too Many Requests — backing off for ${THROTTLE_SLEEP}s…"
            sleep "$THROTTLE_SLEEP"
        else
            echo "  retry in ${SLEEP}s…"
            sleep "$SLEEP"
        fi
        continue
    fi

    # Extract the job id from the JSON response.
    JOB_ID=$(echo "$CREATE_OUT" | python3 -c \
        "import json,sys;print(json.load(sys.stdin)['data']['id'])" 2>/dev/null || true)
    if [ -z "${JOB_ID}" ]; then
        echo "  ⚠ could not parse job id from create response:"
        echo "$CREATE_OUT" | head -15 | sed 's/^/      /'
        sleep "$SLEEP"
        continue
    fi
    echo "  job id: ${JOB_ID}"

    # Poll lifecycle until terminal state or timeout.
    waited=0
    STATE=""
    while [ $waited -lt $MAX_JOB_WAIT_SECONDS ]; do
        STATE=$(oci resource-manager job get --job-id "$JOB_ID" \
            --query 'data."lifecycle-state"' --raw-output 2>/dev/null || echo "?")
        case "$STATE" in
            SUCCEEDED|FAILED|CANCELED) break ;;
        esac
        sleep 10
        waited=$((waited + 10))
    done

    echo "  result: ${STATE}"

    if [ "$STATE" = "SUCCEEDED" ]; then
        echo ""
        echo "════════════════════════════════════════════════════════════"
        echo "  🎉 SUCCESS on attempt #${attempt}"
        echo "  Instance has been created. Check Compute → Instances."
        echo "════════════════════════════════════════════════════════════"
        exit 0
    fi

    # Inspect job logs to decide whether to keep looping.
    LOGS=$(oci resource-manager job get-job-logs-content \
        --job-id "$JOB_ID" 2>/dev/null || true)

    if echo "$LOGS" | grep -qiE "out of host capacity|InternalError"; then
        echo "  ⏳ out of capacity. Retry in ${SLEEP}s…"
        sleep "$SLEEP"
        continue
    fi

    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "  ❌ Job failed for a non-capacity reason."
    echo "  Stopping — fix the underlying issue then re-run."
    echo "  Last logs:"
    echo "────────────────────────────────────────────────────────────"
    echo "$LOGS" | tail -30
    echo "════════════════════════════════════════════════════════════"
    exit 1
done
