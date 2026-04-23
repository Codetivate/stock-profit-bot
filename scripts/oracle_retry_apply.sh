#!/bin/bash
# Oracle Resource Manager stack — auto-retry apply until capacity frees up.
#
# Designed to run in Oracle Cloud Shell (terminal in the OCI console):
#   1. Open Oracle Console → top-right "Cloud Shell" icon (>_)
#   2. Paste the two lines below and hit enter:
#        curl -fsSL https://raw.githubusercontent.com/Codetivate/stock-profit-bot/main/scripts/oracle_retry_apply.sh -o ~/retry.sh
#        chmod +x ~/retry.sh && nohup ~/retry.sh <STACK_OCID> > ~/retry.log 2>&1 & disown
#   3. Close the Cloud Shell tab — the script keeps running (nohup + disown).
#   4. Check progress: open Cloud Shell again → `tail -f ~/retry.log`
#      or check the Jobs list of the stack in the console.
#
# Cloud Shell already has `oci` CLI authenticated as your user, so no
# key upload is required.
#
# Usage:
#   ./oracle_retry_apply.sh <STACK_OCID> [retry_seconds]
#
# Exits once a job succeeds or a non-capacity error occurs.

set -u

STACK_ID="${1:?Usage: $0 <STACK_OCID> [retry_seconds]}"
SLEEP="${2:-180}"        # default 3 min between attempts
MAX_JOB_WAIT_SECONDS=600 # wait up to 10 min for each apply job to finish

echo "════════════════════════════════════════════════════════════"
echo "  Oracle auto-retry apply"
echo "    stack : $STACK_ID"
echo "    retry : every ${SLEEP}s when out of capacity"
echo "    start : $(date)"
echo "════════════════════════════════════════════════════════════"

attempt=0
while true; do
    attempt=$((attempt + 1))
    echo ""
    echo "[$(date +%H:%M:%S)] attempt #${attempt} — creating apply job…"

    # Kick off a new APPLY job (auto-approved so no plan review pause)
    JOB_ID=$(oci resource-manager job create-apply-job \
        --stack-id "$STACK_ID" \
        --execution-plan-strategy AUTO_APPROVED \
        --display-name "auto-retry-${attempt}" \
        --query 'data.id' \
        --raw-output 2>/dev/null)

    if [ -z "${JOB_ID}" ]; then
        echo "  ⚠ failed to enqueue job. API error. Retry in ${SLEEP}s…"
        sleep "$SLEEP"
        continue
    fi

    echo "  job id: ${JOB_ID}"

    # Poll lifecycle until terminal state or timeout
    waited=0
    STATE=""
    while [ $waited -lt $MAX_JOB_WAIT_SECONDS ]; do
        STATE=$(oci resource-manager job get --job-id "$JOB_ID" \
            --query 'data."lifecycle-state"' --raw-output 2>/dev/null)
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

    # Pull the job's logs to see whether it's a capacity issue or something
    # worse we shouldn't loop on (bad config, quota breach, auth).
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
