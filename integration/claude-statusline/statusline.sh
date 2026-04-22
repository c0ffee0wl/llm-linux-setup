#!/bin/bash
# Redirect stderr to /dev/null – errors in statusline must never leak to Claude Code UI
exec 2>/dev/null

input=$(cat)

# $USER can be stale under sudo; $HOSTNAME is empty in non-interactive bash.
# /etc/hostname isn't authoritative either (diverges from the live kernel
# hostname after hostnamectl/containers), so fork hostname -s.
user=$(id -un)
host=$(hostname -s)

# Parse + validate in a single jq fork. `-e` exits non-zero on null/false top-level,
# and jq fails outright on invalid JSON — both fall through to the minimal fallback.
# @tsv keeps field boundaries intact even if any string contains tabs/newlines.
tsv_output=$(printf '%s' "$input" | jq -er '[
    .workspace.current_dir // "~",
    .cost.total_duration_ms // 0,
    .model.display_name // "unknown",
    (.context_window.used_percentage // 0 | floor),
    .context_window.context_window_size // 200000,
    .rate_limits.five_hour.used_percentage // "",
    .rate_limits.seven_day.used_percentage // ""
] | @tsv' 2>/dev/null)

if [ -z "$tsv_output" ]; then
    printf "\033[1;32m(%s@%s)\033[0m" "$user" "$host"
    exit 0
fi

IFS=$'\t' read -r cwd DURATION_MS MODEL PCT CTX_SIZE FIVE_H WEEK <<<"$tsv_output"

# Sanitize numerics — defend against any surprise output from jq
[[ "$DURATION_MS" =~ ^[0-9]+$ ]] || DURATION_MS=0

prompt_symbol="@"
if [ "$EUID" -eq 0 ]; then
    prompt_color="94"
    info_color="31"
else
    prompt_color="32"
    info_color="34"
fi

# Line 1: identity + directory + (duration, only once the first turn completes)
printf "\033[1;${info_color}m(%s%s%s\033[0;${prompt_color}m)-[\033[0;1m%s\033[0;${prompt_color}m]" \
    "$user" "$prompt_symbol" "$host" "$cwd"
if [ "$DURATION_MS" -gt 0 ]; then
    printf " | \033[0;${info_color}m%sm %ss" \
        "$((DURATION_MS / 60000))" "$(((DURATION_MS % 60000) / 1000))"
fi
printf "\033[0m\n"

# Line 2: model + context bar + rate limits (only when NOT behind CCR)
IS_CCR=false
if [[ "$ANTHROPIC_BASE_URL" =~ ^https?://(127\.0\.0\.1|localhost)(:|/) ]]; then
    IS_CCR=true
fi

if [ "$IS_CCR" = false ]; then
    [[ "$PCT" =~ ^[0-9]+$ ]] || PCT=0
    [[ "$CTX_SIZE" =~ ^[0-9]+$ ]] || CTX_SIZE=200000

    GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'

    # Color-coded progress bar
    if [ "$PCT" -ge 90 ]; then BAR_COLOR="$RED"
    elif [ "$PCT" -ge 70 ]; then BAR_COLOR="$YELLOW"
    else BAR_COLOR="$GREEN"; fi

    FILLED=$((PCT / 10)); EMPTY=$((10 - FILLED))
    BAR=""
    [ "$FILLED" -gt 0 ] && printf -v FILL "%${FILLED}s" && BAR="${FILL// /█}"
    [ "$EMPTY" -gt 0 ] && printf -v PAD "%${EMPTY}s" && BAR="${BAR}${PAD// /░}"

    # Context window size label
    if [ "$CTX_SIZE" -ge 1000000 ]; then
        CTX_LABEL="1M"
    else
        CTX_LABEL="$((CTX_SIZE / 1000))K"
    fi

    # Line 2 is green by default, only the progress bar changes color
    LINE2="${GREEN}[${MODEL}] ${BAR_COLOR}${BAR}${GREEN} ${PCT}% (${CTX_LABEL})"

    # Rate limits (only if present and numeric)
    LIMITS=""
    [[ "$FIVE_H" =~ ^[0-9.]+$ ]] && LIMITS="5h: $(printf '%.0f' "$FIVE_H")%"
    [[ "$WEEK" =~ ^[0-9.]+$ ]] && LIMITS="${LIMITS:+$LIMITS }7d: $(printf '%.0f' "$WEEK")%"
    [ -n "$LIMITS" ] && LINE2="${LINE2} | ${LIMITS}"

    echo -e "${LINE2}${RESET}"
fi
