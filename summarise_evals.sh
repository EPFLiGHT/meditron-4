#!/usr/bin/env bash
set -euo pipefail

SHOW_SAMPLE=0
ONLY_COMPLETED=0
args=()
for arg in "$@"; do
  if [ "$arg" = "--show_sample" ]; then
    SHOW_SAMPLE=1
  elif [ "$arg" = "--completed" ]; then
    ONLY_COMPLETED=1
  else
    args+=("$arg")
  fi
done

REPORTS_DIR="${args[0]:-eval_reports}"
EVAL_RESULTS_DIR="${args[1]:-eval_results}"

if [ ! -d "$REPORTS_DIR" ]; then
  echo "Reports directory not found: $REPORTS_DIR" >&2
  exit 1
fi
if [ ! -d "$EVAL_RESULTS_DIR" ]; then
  echo "Eval results directory not found: $EVAL_RESULTS_DIR" >&2
  exit 1
fi

shopt -s nullglob
mapfile -d '' report_files < <(find "$REPORTS_DIR" -maxdepth 1 -type f -print0)
if [ "${#report_files[@]}" -eq 0 ]; then
  echo "No report files found in $REPORTS_DIR" >&2
  exit 0
fi

sorted_reports=()
while IFS= read -r report; do
  sorted_reports+=("$report")
done < <(
  printf '%s\0' "${report_files[@]}" \
    | xargs -0 stat -c '%Y %n' \
    | sort -n \
    | awk '{$1=""; sub(/^ /,""); print}'
)

if command -v rg >/dev/null 2>&1; then
  line_cmd=(rg -n -m1)
  grep_cmd=(rg -n)
  has_rg=1
else
  line_cmd=(grep -n -m1)
  grep_cmd=(grep -n)
  has_rg=0
fi

count_invalids_recursive() {
  local dir="$1"
  local total=0
  if [ "$has_rg" -eq 1 ]; then
    total=$(
      find "$dir" -type f -name "*.jsonl" -print0 \
        | xargs -0 -r rg -o -i "\\[invalid\\]" 2>/dev/null \
        | wc -l | tr -d ' '
    )
  else
    total=$(
      find "$dir" -type f -name "*.jsonl" -print0 \
        | xargs -0 -r grep -o -i "\\[invalid\\]" 2>/dev/null \
        | wc -l | tr -d ' '
    )
  fi
  echo "$total"
}

count_lines_recursive() {
  local dir="$1"
  local total=0
  total=$(
    find "$dir" -type f -name "*.jsonl" -print0 \
      | xargs -0 -r wc -l 2>/dev/null \
      | awk '{sum += $1} END {print sum + 0}'
  )
  echo "$total"
}

eval_resps_stats() {
  local eval_dir="$1"
  local jsonl_path=""
  jsonl_path="$(find "$eval_dir" -type f -name "*.jsonl" | sort | head -n1)"
  if [ -z "$jsonl_path" ]; then
    echo -e "n/a\tn/a\tn/a"
    return
  fi
  python3 - "$jsonl_path" <<'PY' 2>/dev/null || true
import json
import sys

path = sys.argv[1]
first_resps = "n/a"
first_len = "n/a"
total = 0
count = 0

with open(path, "r", encoding="utf-8") as handle:
    for idx, line in enumerate(handle):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        resps = obj.get("resps")
        if isinstance(resps, list) and resps:
            first = resps[0]
            text = None
            if isinstance(first, list) and first:
                if isinstance(first[0], str):
                    text = first[0]
            elif isinstance(first, str):
                text = first
            if text:
                if first_resps == "n/a":
                    first_resps = str(resps)
                    first_len = str(len(text))
                total += len(text)
                count += 1
        if idx == 0 and first_resps == "n/a":
            break

mean_len = f"{total / count:.1f}" if count else "n/a"
print(f"{first_resps}\t{first_len}\t{mean_len}")
PY
}

print_invalids_for_report() {
  local report="$1"
  local base
  local job_id=""
  base="$(basename "$report")"
  if [[ "$base" =~ \.([0-9]+)\. ]]; then
    job_id="${BASH_REMATCH[1]}"
  fi
  if [ -z "$job_id" ]; then
    echo "Invalids: n/a"
    return
  fi

  mapfile -t matches < <(find "$EVAL_RESULTS_DIR" -type d -name "*_${job_id}" | sort)
  if [ "${#matches[@]}" -eq 0 ]; then
    echo "Invalids: n/a"
    echo "Eval folder: n/a"
    if [ "$SHOW_SAMPLE" -eq 1 ]; then
      echo "First resps: n/a"
    fi
    echo "Resps mean length: n/a"
    return
  fi

  local invalid_total=0
  local line_total=0
  for dir in "${matches[@]}"; do
    invalid_total=$((invalid_total + $(count_invalids_recursive "$dir")))
    line_total=$((line_total + $(count_lines_recursive "$dir")))
  done
  if [ "$line_total" -eq 0 ]; then
    echo "Invalids: 0/0"
    echo "Eval folder: ${matches[*]}"
    if [ "$SHOW_SAMPLE" -eq 1 ]; then
      echo "First resps: n/a"
    fi
    echo "Resps mean length: n/a"
    return
  fi
  local percent
  percent=$(awk -v a="$invalid_total" -v b="$line_total" 'BEGIN {printf "%.4f", (a / b) * 100}')
  echo "Invalids: ${invalid_total}/${line_total} (${percent}%)"
  echo "Eval folder: ${matches[*]}"
  IFS=$'\t' read -r first_resps first_len mean_len < <(eval_resps_stats "${matches[0]}")
  if [ "$SHOW_SAMPLE" -eq 1 ]; then
    echo "First resps: ${first_resps}"
  fi
  echo "Resps mean length: ${mean_len}"
}

for report in "${sorted_reports[@]}"; do
  exit_code="$(awk -F'Exit code: ' '/Exit code:/{code=$2} END{if(code!="") print code}' "$report" | sed -E 's/[^0-9].*$//')"
  status="UNKNOWN"
  if [ -n "$exit_code" ]; then
    if [ "$exit_code" = "0" ]; then
      status="COMPLETED"
    else
      status="FAILED (exit code $exit_code)"
    fi
  fi
  if [ "$ONLY_COMPLETED" -eq 1 ] && [ "$status" != "COMPLETED" ]; then
    continue
  fi

  echo "== $report =="

  model_line="$("${line_cmd[@]}" "MODEL_PATH=" "$report" || true)"
  model=""
  if [ -n "$model_line" ]; then
    model="${model_line#*MODEL_PATH=}"
  else
    model_line="$("${line_cmd[@]}" "pretrained=" "$report" || true)"
    if [ -n "$model_line" ]; then
      model="$(printf '%s' "$model_line" | sed -E 's/.*pretrained=([^, ]+).*/\1/')"
    fi
  fi

  if [ -n "$model" ]; then
    echo "Model: $model"
  else
    echo "Model: UNKNOWN"
  fi

  echo "Status: $status"

  results_table="$(awk '
    /^\|[[:space:]]*Tasks[[:space:]]*\|/ {in_table=1; print; next}
    in_table && /^\|/ {print; next}
    in_table {exit}
  ' "$report")"

  if [ -n "$results_table" ]; then
    echo "Results:"
    printf '%s\n' "$results_table"
    print_invalids_for_report "$report"
  else
    error_summary=""
    if "${grep_cmd[@]}" "Traceback" "$report" >/dev/null 2>&1; then
      error_summary="$(awk '
        /Traceback/ {tb=1}
        tb && ($0 ~ /(Error|Exception|KeyError|RuntimeError|ValueError|TypeError|AssertionError|ChildFailedError)/) {last=$0}
        END {if (last!="") print last}
      ' "$report")"
    fi

    if [ -z "$error_summary" ]; then
      error_summary="$("${line_cmd[@]}" "ERROR:|Error:|FAILED|exit code: [1-9]" "$report" 2>/dev/null || true)"
    fi

    if [ -n "$error_summary" ]; then
      echo "Error: $error_summary"
    else
      echo "Error: UNKNOWN"
    fi
    print_invalids_for_report "$report"
  fi

  echo
done
