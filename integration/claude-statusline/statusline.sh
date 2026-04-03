#!/bin/bash
input=$(cat)

cwd=$(echo "$input" | jq -r '.workspace.current_dir')
user=$(whoami)
host=$(hostname -s)
DURATION_MS=$(echo "$input" | jq -r '.cost.total_duration_ms // 0')

prompt_symbol="@"
if [ "$EUID" -eq 0 ]; then
    prompt_color="94"
    info_color="31"
else
    prompt_color="32"
    info_color="34"
fi

# Format duration
MINS=$((DURATION_MS / 60000))
SECS=$(((DURATION_MS % 60000) / 1000))

# Line 1: identity + directory + duration
printf "\033[1;${info_color}m(%s%s%s\033[0;${prompt_color}m)-[\033[0;1m%s\033[0;${prompt_color}m]" \
    "$user" "$prompt_symbol" "$host" "$cwd"
printf " | \033[0;${info_color}m%sm %ss\033[0m\n" "$MINS" "$SECS"

# Line 2: model + context bar + rate limits (only when NOT behind CCR)
IS_CCR=false
if [[ "$ANTHROPIC_BASE_URL" =~ ^https?://(127\.0\.0\.1|localhost)(:|/) ]]; then
    IS_CCR=true
fi

if [ "$IS_CCR" = false ]; then
    MODEL=$(echo "$input" | jq -r '.model.display_name')
    PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
    CTX_SIZE=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')

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

    # Rate limits (only if present)
    FIVE_H=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
    WEEK=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')

    LIMITS=""
    [ -n "$FIVE_H" ] && LIMITS="5h: $(printf '%.0f' "$FIVE_H")%"
    [ -n "$WEEK" ] && LIMITS="${LIMITS:+$LIMITS }7d: $(printf '%.0f' "$WEEK")%"
    [ -n "$LIMITS" ] && LINE2="${LINE2} | ${LIMITS}"

    echo -e "${LINE2}${RESET}"
fi
