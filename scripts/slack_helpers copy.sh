#!/usr/bin/env bash
# Shared Slack helpers for training/eval scripts.
# Expects the caller to define START_TS, START_HUMAN, RUN_NAME, SLURM_JOB_ID/SLACK_JOB_ID, and optionally FAILED_CMD.

if [ -n "${SLACK_HELPERS_LOADED:-}" ]; then
  return 0
fi
SLACK_HELPERS_LOADED=1

: "${SLACK_INSECURE:=1}"

format_duration() {
    local s="$1"
    printf "%02d:%02d:%02d" $((s/3600)) $(((s%3600)/60)) $((s%60))
}

_slack_find_errors_summary() {
    local report_file="$1"
    local reports_dir find_errors_path output summary

    if [ -n "${FIND_ERRORS_SCRIPT:-}" ] && [ -x "$FIND_ERRORS_SCRIPT" ]; then
        find_errors_path="$FIND_ERRORS_SCRIPT"
    elif [ -n "${PROJECT_ROOT:-}" ] && [ -x "$PROJECT_ROOT/find_errors.sh" ]; then
        find_errors_path="$PROJECT_ROOT/find_errors.sh"
    else
        find_errors_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/find_errors.sh"
        if [ ! -x "$find_errors_path" ]; then
            return 0
        fi
    fi

    reports_dir="$(dirname "$report_file")"
    if [ ! -d "$reports_dir" ]; then
        return 0
    fi

    output="$("$find_errors_path" "$reports_dir" 2>/dev/null || true)"
    summary="$(printf '%s\n' "$output" | awk -v target="$report_file" '
        $0 == "== " target " ==" {found=1; next}
        found && $0 == "" {exit}
        found {print; exit}
    ')"

    if [ -n "$summary" ]; then
        printf '%s' "$summary"
    fi
}

_slack_extract_eval_results() {
    local report="$1"
    local job_id="$2"
    local results_dir="${PROJECT_ROOT}/eval_results"
    local output=""

    # 1. Extract the results table from the SLURM log
    local table
    table="$(awk '
        /^\|[[:space:]]*Tasks[[:space:]]*\|/ {in_table=1; print; next}
        in_table && /^\|/ {print; next}
        in_table {exit}
    ' "$report" 2>/dev/null)"

    if [ -n "$table" ]; then
        output="${table}\n"
    fi

    # 2. Find the specific eval_results folder for this job ID to count invalids
    if [ -n "$job_id" ] && [ -d "$results_dir" ]; then
        local eval_dir
        eval_dir="$(find "$results_dir" -type d -name "*_${job_id}" | sort | head -n 1)"
        
        if [ -n "$eval_dir" ]; then
            local invalid_total=0 line_total=0
            
            # Count invalids (prefer ripgrep if available, fallback to grep)
            if command -v rg >/dev/null 2>&1; then
                invalid_total=$(find "$eval_dir" -type f -name "*.jsonl" -print0 | xargs -0 -r rg -o -i "\\[invalid\\]" 2>/dev/null | wc -l | tr -d ' ')
            else
                invalid_total=$(find "$eval_dir" -type f -name "*.jsonl" -print0 | xargs -0 -r grep -o -i "\\[invalid\\]" 2>/dev/null | wc -l | tr -d ' ')
            fi
            
            # Count total lines
            line_total=$(find "$eval_dir" -type f -name "*.jsonl" -print0 | xargs -0 -r cat 2>/dev/null | wc -l | tr -d ' ')
            
            if [ "$line_total" -gt 0 ]; then
                local percent
                percent=$(awk -v i="$invalid_total" -v t="$line_total" 'BEGIN {printf "%.2f", (i / t) * 100}')
                output="${output}\nInvalids: ${invalid_total}/${line_total} (${percent}%)"
            fi
        fi
    fi

    # Echo back to the caller
    echo -e "$output"
}

_slack_build_payload() {
    local text="$1"
    local payload=""

    local PY_BIN=""
    if command -v python3 >/dev/null 2>&1; then
        PY_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PY_BIN="python"
    fi

    if [ -n "$PY_BIN" ]; then
        payload="$("$PY_BIN" - <<'PY' "$text"
import json, sys
msg = sys.argv[1] if len(sys.argv) > 1 else ""
print(json.dumps({"text": msg}))
PY
)"
    else
        local escaped_msg
        escaped_msg="$(printf '%s' "$text" | sed 's/\"/\\\\\"/g')"
        payload="{\"text\": \"${escaped_msg}\"}"
    fi

    printf '%s' "$payload"
}

slack_notify() {
    # First arg: exit code. Additional args ignored.
    local rc="$1"
    local phase="${2:-${SLACK_PHASE:-}}"
    local end_ts end_human elapsed status text payload job_id report_file reports_dir error_summary send_slack

    send_slack=0
    if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
        send_slack=1
    fi

    end_ts="$(date +%s)"
    end_human="$(date -Is)"
    elapsed="$((end_ts - ${START_TS:-end_ts}))"
    job_id="${SLACK_JOB_ID:-${SLURM_JOB_ID:-?}}"

    if [ "$rc" -eq 0 ]; then
        status="COMPLETED"
    else
        status="FAILED"
    fi

    local phase_label=""
    if [ -n "$phase" ]; then
        phase_label="[$phase] "
    fi

    text="${phase_label}[$status] ${RUN_NAME:-job} (job ${job_id}) on $(hostname)
Config: ${RUN_NAME:-unknown}
Nodes:  ${NODE_COUNT:-${SLURM_NNODES:-?}}
Start:  ${START_HUMAN:-unknown}
End:    ${end_human}
Elapsed: $(format_duration "$elapsed")
Exit code: ${rc}"

    if [ "$rc" -ne 0 ] && [ -n "${FAILED_CMD:-}" ]; then
        text="${text}
Failed at: ${FAILED_CMD}"
    fi

    reports_dir="${SLACK_REPORTS_DIR:-${PROJECT_ROOT:-.}/reports}"
    if [ -n "${SLACK_REPORT_FILE:-}" ]; then
        report_file="$SLACK_REPORT_FILE"
    elif [ -n "${RUN_NAME:-}" ]; then
        report_file="${reports_dir}/R-${RUN_NAME}.${job_id}.err"
    elif [ -n "${SLURM_JOB_NAME:-}" ]; then
        report_file="${reports_dir}/R-${SLURM_JOB_NAME}.${job_id}.err"
    else
        report_file=""
    fi

if [ -n "$report_file" ] && [ -f "$report_file" ]; then
        if [ "$rc" -eq 0 ]; then
            # If successful, extract the evaluation table and stats
            local eval_results
            eval_results="$(_slack_extract_eval_results "$report_file" "$job_id")"
            if [ -n "$eval_results" ]; then
                text="${text}

*Evaluation Results:*
\`\`\`
${eval_results}
\`\`\`"
            fi
        else
            # If failed, extract the error summary
            local error_summary
            error_summary="$(_slack_find_errors_summary "$report_file")"
            if [ -n "$error_summary" ]; then
                text="${text}

*Detected Error:*
\`\`\`
${error_summary}
\`\`\`"
            fi
        fi
    fi

    if [ -n "${SLACK_TAG:-}" ]; then
        text="${SLACK_TAG} ${text}"
    fi

    echo "== SLACK MESSAGE =="
    printf '%s\n' "$text"

    if [ "$send_slack" -ne 1 ]; then
        return 0
    fi

    payload="$(_slack_build_payload "$text")"

    curl_ssl_flag=()
    if [ "${SLACK_INSECURE:-0}" = "1" ]; then
        curl_ssl_flag+=(--insecure)
    fi

    { set +x; } 2>/dev/null
    curl -sS "${curl_ssl_flag[@]}" -X POST -H 'Content-type: application/json' --data "$payload" "$SLACK_WEBHOOK_URL" >/dev/null || true
    { set -x; } 2>/dev/null
}
