#!/bin/bash
# Oracle VM hunter v2 — direct instance launch with shape rotation.
#
# Unlike oracle_retry_apply.sh which goes through the Terraform stack,
# this script calls `oci compute instance launch` directly and cycles
# through several shape configs per attempt. The insight: A1.Flex
# capacity is reported per-shape-config, not per-shape. A tiny 1-OCPU
# / 1-GB slot is often free even when 4/24 slots are all booked. Get
# *any* instance, resize later.
#
# Designed to run in Oracle Cloud Shell (already authenticated):
#
#   curl -fsSL https://raw.githubusercontent.com/Codetivate/stock-profit-bot/main/scripts/oracle_retry_launch.sh -o ~/launch.sh && \
#     chmod +x ~/launch.sh && \
#     nohup ~/launch.sh > ~/launch.log 2>&1 & disown
#
# Env var overrides (all auto-detected if unset):
#   COMPARTMENT_OCID   — defaults to tenancy root compartment
#   SUBNET_OCID        — defaults to first public subnet in flowcon compartment
#   IMAGE_OCID         — defaults to latest Ubuntu 24.04 aarch64 in region
#   SSH_PUB_KEY        — defaults to ~/.ssh/id_rsa.pub, auto-generated if missing
#   AVAILABILITY_DOMAIN — defaults to first AD in region
#   INSTANCE_NAME      — defaults to stock-profit-bot
#   INNER_SLEEP        — seconds between shape attempts within a cycle (default 10)
#   OUTER_SLEEP        — seconds between full cycles (default 180)
#   THROTTLE_SLEEP     — seconds to back off on HTTP 429 (default 900)

set -u

INSTANCE_NAME="${INSTANCE_NAME:-stock-profit-bot}"
INNER_SLEEP="${INNER_SLEEP:-10}"
OUTER_SLEEP="${OUTER_SLEEP:-180}"
THROTTLE_SLEEP="${THROTTLE_SLEEP:-900}"

# Shape ladder: try biggest first (in case capacity opened), fall back to
# progressively smaller configs. A1.Flex requires ocpus ≥ 1 and
# memoryInGBs between ocpus*1 and ocpus*64, so 1/1 is the absolute floor.
SHAPE_LADDER=(
    "4 24"   # Always-Free max
    "2 12"   # Comfortable for our bot
    "1 6"    # Default A1.Flex config
    "1 2"    # Minimum useful
    "1 1"    # Floor — always has a chance
)

echo "════════════════════════════════════════════════════════════"
echo "  Oracle A1.Flex hunter v2 (direct launch)"
echo "════════════════════════════════════════════════════════════"
echo "  start : $(date)"

# ── Auto-detection ─────────────────────────────────────────────
# Compartment: use tenancy root unless overridden.
if [ -z "${COMPARTMENT_OCID:-}" ]; then
    COMPARTMENT_OCID=$(oci iam compartment list \
        --compartment-id-in-subtree false \
        --query 'data[0]."compartment-id"' --raw-output 2>/dev/null)
fi
echo "  compartment : ${COMPARTMENT_OCID:0:40}…"

# Availability domain
if [ -z "${AVAILABILITY_DOMAIN:-}" ]; then
    AVAILABILITY_DOMAIN=$(oci iam availability-domain list \
        --compartment-id "$COMPARTMENT_OCID" \
        --query 'data[0].name' --raw-output 2>/dev/null)
fi
echo "  AD          : ${AVAILABILITY_DOMAIN}"

# Subnet: first "public" subnet in compartment
if [ -z "${SUBNET_OCID:-}" ]; then
    SUBNET_OCID=$(oci network subnet list \
        --compartment-id "$COMPARTMENT_OCID" \
        --all \
        --query "data[?contains(\"display-name\", 'ublic')] | [0].id" \
        --raw-output 2>/dev/null)
fi
echo "  subnet      : ${SUBNET_OCID:0:40}…"

# Image: latest Canonical Ubuntu 24.04 for A1.Flex
if [ -z "${IMAGE_OCID:-}" ]; then
    IMAGE_OCID=$(oci compute image list \
        --compartment-id "$COMPARTMENT_OCID" \
        --operating-system "Canonical Ubuntu" \
        --operating-system-version "24.04" \
        --shape "VM.Standard.A1.Flex" \
        --sort-by TIMECREATED --sort-order DESC \
        --limit 1 \
        --query 'data[0].id' --raw-output 2>/dev/null)
fi
echo "  image       : ${IMAGE_OCID:0:40}…"

# SSH key: generate if missing
if [ -z "${SSH_PUB_KEY:-}" ]; then
    if [ ! -f "$HOME/.ssh/id_rsa.pub" ]; then
        echo "  ssh         : generating ~/.ssh/id_rsa (rsa 4096) …"
        ssh-keygen -t rsa -b 4096 -N "" -f "$HOME/.ssh/id_rsa" -q
    fi
    SSH_PUB_KEY=$(cat "$HOME/.ssh/id_rsa.pub")
fi
echo "  ssh key     : ${SSH_PUB_KEY:0:50}…"

if [ -z "$COMPARTMENT_OCID" ] || [ -z "$AVAILABILITY_DOMAIN" ] \
   || [ -z "$SUBNET_OCID" ] || [ -z "$IMAGE_OCID" ]; then
    echo "❌ missing required OCID(s). Set them via env vars and retry."
    exit 1
fi

echo "════════════════════════════════════════════════════════════"
echo ""

cycle=0
while true; do
    cycle=$((cycle + 1))
    echo ""
    echo "══ cycle #${cycle}  $(date +%H:%M:%S) ══"

    success=0
    for config in "${SHAPE_LADDER[@]}"; do
        OCPUS=$(echo "$config" | awk '{print $1}')
        MEM=$(echo "$config" | awk '{print $2}')

        echo "  → trying ${OCPUS} OCPU / ${MEM} GB …"

        OUT=$(oci compute instance launch \
            --availability-domain "$AVAILABILITY_DOMAIN" \
            --compartment-id "$COMPARTMENT_OCID" \
            --shape "VM.Standard.A1.Flex" \
            --shape-config "{\"ocpus\":${OCPUS},\"memoryInGBs\":${MEM}}" \
            --image-id "$IMAGE_OCID" \
            --subnet-id "$SUBNET_OCID" \
            --metadata "{\"ssh_authorized_keys\":\"${SSH_PUB_KEY}\"}" \
            --display-name "$INSTANCE_NAME" \
            --assign-public-ip true \
            2>&1)
        RC=$?

        if [ $RC -eq 0 ]; then
            IID=$(echo "$OUT" | python3 -c \
                "import json,sys;print(json.load(sys.stdin)['data']['id'])" 2>/dev/null)
            echo ""
            echo "════════════════════════════════════════════════════════════"
            echo "  🎉 SUCCESS with ${OCPUS} OCPU / ${MEM} GB on cycle #${cycle}"
            echo "  instance: ${IID}"
            echo "════════════════════════════════════════════════════════════"
            echo ""
            echo "Next steps:"
            echo "  1. Get public IP:"
            echo "       oci compute instance list-vnics --instance-id '${IID}' \\"
            echo "         --query 'data[0].\"public-ip\"' --raw-output"
            echo "  2. Wait ~30s for cloud-init, then SSH from this Cloud Shell:"
            echo "       ssh -i ~/.ssh/id_rsa ubuntu@<PUBLIC_IP>"
            echo "  3. Run bootstrap:"
            echo "       export TELEGRAM_BOT_TOKEN=..."
            echo "       export TELEGRAM_CHAT_ID=..."
            echo "       curl -fsSL https://raw.githubusercontent.com/Codetivate/stock-profit-bot/main/scripts/oracle_bootstrap.sh | bash"
            echo ""
            echo "To resize up after bootstrap:"
            echo "   oci compute instance update --instance-id '${IID}' \\"
            echo "     --shape-config '{\"ocpus\":2,\"memoryInGBs\":12}'"
            exit 0
        fi

        if echo "$OUT" | grep -qE '"status": *429|TooManyRequests'; then
            echo "     ⛔ 429 rate limit — back off ${THROTTLE_SLEEP}s"
            sleep "$THROTTLE_SLEEP"
            success=0
            break
        fi

        if echo "$OUT" | grep -qiE "out of host capacity|InternalError"; then
            echo "     ⏳ no capacity at this size — next shape"
            sleep "$INNER_SLEEP"
            continue
        fi

        # Anything else is a real error — log it and stop so user can fix.
        echo "     ❌ unexpected error:"
        echo "$OUT" | head -20 | sed 's/^/         /'
        echo ""
        echo "Stopping. Fix the underlying issue and re-run."
        exit 1
    done

    echo "  all sizes tried — waiting ${OUTER_SLEEP}s for next cycle"
    sleep "$OUTER_SLEEP"
done
