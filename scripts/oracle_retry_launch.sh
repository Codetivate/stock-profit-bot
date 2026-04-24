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
# Tight defaults — compute instance launch has a much higher per-tenant
# rate limit than the Resource Manager create_job endpoint (first-gen
# hunter). 3 s between shape attempts and 45 s between cycles still
# stays well inside any plausible throttle, while tripling the attempts
# per hour versus the old 10 s / 180 s pacing.
INNER_SLEEP="${INNER_SLEEP:-3}"
OUTER_SLEEP="${OUTER_SLEEP:-45}"
# Per-attempt cooldown specifically for TINY_ONLY mode — just hammer
# 1-OCPU-1-GB as fast as it's safe to.
TINY_ONLY_SLEEP="${TINY_ONLY_SLEEP:-15}"
THROTTLE_SLEEP="${THROTTLE_SLEEP:-900}"

# TINY_ONLY=1 skips the shape ladder and pounds just 1/1 every
# TINY_ONLY_SLEEP seconds. Use when Singapore reports no-capacity on
# every shape tried — that usually means ARM capacity is family-wide
# exhausted, so bigger shapes are hopeless anyway. Meanwhile the
# smallest slot frees up first when any capacity returns.
TINY_ONLY="${TINY_ONLY:-0}"

# "Golden window" timing attack: capacity releases from Oracle tend
# to cluster around UTC 00:00 / 06:00 / 12:00 / 18:00 / 22:00 as
# other tenants' reservations expire or US/EU workloads wind down.
# Outside those windows we retry normally; inside a ±15 minute band
# we retry every 30 s to race other hunters for the slot.
GOLDEN_HOURS=(0 6 12 18 22)
GOLDEN_INNER_SLEEP=3      # between shape attempts in golden window
GOLDEN_OUTER_SLEEP=30     # between cycles in golden window
GOLDEN_BAND_MINUTES=15    # ± around the hour

in_golden_window() {
    local h=$(date -u +%-H)
    local m=$(date -u +%-M)
    for gh in "${GOLDEN_HOURS[@]}"; do
        local diff=$(( h - gh ))
        # within ± 15 min of a golden hour (handles :45-:59 and :00-:15)
        if [ "$h" = "$gh" ] && [ "$m" -le "$GOLDEN_BAND_MINUTES" ]; then return 0; fi
        local prev=$(( (gh - 1 + 24) % 24 ))
        if [ "$h" = "$prev" ] && [ "$m" -ge $((60 - GOLDEN_BAND_MINUTES)) ]; then return 0; fi
    done
    return 1
}

# Shape ladder: try smallest first (highest probability of capacity
# being available). A1.Flex supports resize in-place, so we "get a foot
# in the door" with 1 OCPU / 1 GB and the user can bump it to 2/12 or
# 4/24 via `oci compute instance update --shape-config …` after.
#
# Empirical availability in Singapore AD-1 (approximate):
#   4 OCPU / 24 GB → ~1% at any given time (everyone wants Always-Free max)
#   2 OCPU / 12 GB → ~5%
#   1 OCPU /  6 GB → ~15%  (Oracle's default A1.Flex)
#   1 OCPU /  2 GB → ~40%
#   1 OCPU /  1 GB → ~80%  (floor — nobody asks for this shape)
#
# Tiny-first flips the old biggest-first order so the hunter lands a VM
# fast rather than wasting a full cycle before falling back to a slot
# that's usually free anyway. Set SHAPE_ORDER=big-first to restore the
# original order if you really want to grab 24 GB in the first try.
SHAPE_ORDER="${SHAPE_ORDER:-tiny-first}"
if [ "$TINY_ONLY" = "1" ]; then
    SHAPE_LADDER=("1 1")
elif [ "$SHAPE_ORDER" = "big-first" ]; then
    SHAPE_LADDER=("4 24" "2 12" "1 6" "1 2" "1 1")
else
    SHAPE_LADDER=("1 1" "1 2" "1 6" "2 12" "4 24")
fi

echo "════════════════════════════════════════════════════════════"
echo "  Oracle A1.Flex hunter v2 (direct launch)"
echo "════════════════════════════════════════════════════════════"
echo "  start : $(date)"

# ── Auto-detection ─────────────────────────────────────────────
# Tenancy OCID — try env, config file, then a CLI call that works
# without a compartment arg.
TENANCY_OCID="${TENANCY_OCID:-${OCI_TENANCY:-${OCI_CLI_TENANCY:-}}}"
if [ -z "$TENANCY_OCID" ] && [ -f "$HOME/.oci/config" ]; then
    TENANCY_OCID=$(grep -E "^tenancy" "$HOME/.oci/config" 2>/dev/null \
        | head -1 | awk -F= '{print $2}' | tr -d '[:space:]')
fi

# Compartment: default to tenancy root (free-tier users usually have
# nothing under root).
if [ -z "${COMPARTMENT_OCID:-}" ]; then
    COMPARTMENT_OCID="${TENANCY_OCID:-}"
fi

# Last resort: query any compartment we can see and use its parent
# (which is tenancy).
if [ -z "$COMPARTMENT_OCID" ]; then
    COMPARTMENT_OCID=$(oci iam compartment list --all \
        --query 'data[0]."compartment-id"' --raw-output 2>/dev/null)
fi
echo "  compartment : ${COMPARTMENT_OCID:0:40}…"

# Availability domain
if [ -z "${AVAILABILITY_DOMAIN:-}" ]; then
    AVAILABILITY_DOMAIN=$(oci iam availability-domain list \
        --compartment-id "$COMPARTMENT_OCID" \
        --query 'data[0].name' --raw-output 2>/dev/null)
    # Fallback: try without compartment (some Cloud Shell setups)
    if [ -z "$AVAILABILITY_DOMAIN" ] || [ "$AVAILABILITY_DOMAIN" = "null" ]; then
        AVAILABILITY_DOMAIN=$(oci iam availability-domain list \
            --query 'data[0].name' --raw-output 2>/dev/null)
    fi
fi
echo "  AD          : ${AVAILABILITY_DOMAIN}"

# Subnet: first "public" subnet we can see
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

missing=()
[ -z "$COMPARTMENT_OCID" ] || [ "$COMPARTMENT_OCID" = "null" ] && missing+=("COMPARTMENT_OCID")
[ -z "$AVAILABILITY_DOMAIN" ] || [ "$AVAILABILITY_DOMAIN" = "null" ] && missing+=("AVAILABILITY_DOMAIN")
[ -z "$SUBNET_OCID" ] || [ "$SUBNET_OCID" = "null" ] && missing+=("SUBNET_OCID")
[ -z "$IMAGE_OCID" ] || [ "$IMAGE_OCID" = "null" ] && missing+=("IMAGE_OCID")

if [ ${#missing[@]} -ne 0 ]; then
    echo ""
    echo "❌ Missing required values: ${missing[*]}"
    echo ""
    echo "Fix one of these ways:"
    echo ""
    echo "  A) Export them explicitly before re-running:"
    echo "       export COMPARTMENT_OCID='ocid1.tenancy.oc1..aaaaaa...'"
    echo "       export AVAILABILITY_DOMAIN='doCA:AP-SINGAPORE-1-AD-1'"
    echo "       export SUBNET_OCID='ocid1.subnet.oc1.ap-singapore-1...'"
    echo "       export IMAGE_OCID='ocid1.image.oc1.ap-singapore-1...'"
    echo ""
    echo "  B) Find them in Oracle Console:"
    echo "     · Tenancy OCID  → Profile menu → Tenancy"
    echo "     · Subnet OCID   → Networking → VCN → stock-profit-bot-vcn"
    echo "                       → public subnet → click 'Show' on OCID"
    echo "     · Image OCID    → click command below in Cloud Shell:"
    echo "         oci compute image list --compartment-id \$COMPARTMENT_OCID \\"
    echo "           --operating-system 'Canonical Ubuntu' \\"
    echo "           --operating-system-version 24.04 \\"
    echo "           --shape VM.Standard.A1.Flex --limit 1 \\"
    echo "           --query 'data[0].id' --raw-output"
    echo "     · AD           →  oci iam availability-domain list \\"
    echo "                         --compartment-id \$COMPARTMENT_OCID \\"
    echo "                         --query 'data[0].name' --raw-output"
    echo ""
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
            echo "  1. Get public IP (wait ~30s for cloud-init first):"
            echo "       oci compute instance list-vnics --instance-id '${IID}' \\"
            echo "         --query 'data[0].\"public-ip\"' --raw-output"
            echo "  2. SSH from this Cloud Shell:"
            echo "       ssh -i ~/.ssh/id_rsa ubuntu@<PUBLIC_IP>"
            echo "  3. Run bootstrap to install bot:"
            echo "       export TELEGRAM_BOT_TOKEN=..."
            echo "       export TELEGRAM_CHAT_ID=..."
            echo "       curl -fsSL https://raw.githubusercontent.com/Codetivate/stock-profit-bot/main/scripts/oracle_bootstrap.sh | bash"
            if [ "$OCPUS" -lt 4 ] || [ "$MEM" -lt 24 ]; then
                echo ""
                echo "  4. Resize up to Always-Free max (optional, recommended):"
                echo "       oci compute instance update --instance-id '${IID}' \\"
                echo "         --shape-config '{\"ocpus\":4,\"memoryInGBs\":24}' --force"
                echo "     Or step up gradually if 4/24 also out of capacity:"
                echo "       → 2/12  ({\"ocpus\":2,\"memoryInGBs\":12})"
                echo "       → 2/8   ({\"ocpus\":2,\"memoryInGBs\":8})"
                echo "     Instance reboots 1-2 min; systemd services auto-restart."
            fi
            exit 0
        fi

        if echo "$OUT" | grep -qE '"status": *429|TooManyRequests'; then
            echo "     ⛔ 429 rate limit — back off ${THROTTLE_SLEEP}s"
            sleep "$THROTTLE_SLEEP"
            success=0
            break
        fi

        if echo "$OUT" | grep -qiE "out of host capacity|InternalError"; then
            if in_golden_window; then
                echo "     ⏳ no capacity — GOLDEN WINDOW fast retry in ${GOLDEN_INNER_SLEEP}s"
                sleep "$GOLDEN_INNER_SLEEP"
            else
                echo "     ⏳ no capacity at this size — next shape"
                sleep "$INNER_SLEEP"
            fi
            continue
        fi

        # Anything else is a real error — log it and stop so user can fix.
        echo "     ❌ unexpected error:"
        echo "$OUT" | head -20 | sed 's/^/         /'
        echo ""
        echo "Stopping. Fix the underlying issue and re-run."
        exit 1
    done

    if [ "$TINY_ONLY" = "1" ]; then
        # In tiny-only mode, tighter cycle — it's just one shape
        sleep "$TINY_ONLY_SLEEP"
    elif in_golden_window; then
        echo "  all sizes tried — GOLDEN WINDOW fast cycle in ${GOLDEN_OUTER_SLEEP}s"
        sleep "$GOLDEN_OUTER_SLEEP"
    else
        echo "  all sizes tried — waiting ${OUTER_SLEEP}s for next cycle"
        sleep "$OUTER_SLEEP"
    fi
done
