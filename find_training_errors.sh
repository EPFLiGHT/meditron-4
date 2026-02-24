#!/usr/bin/env bash
set -euo pipefail

REPORTS_DIR="${1:-train_reports}"

if [ ! -d "$REPORTS_DIR" ]; then
  echo "Reports directory not found: $REPORTS_DIR" >&2
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
  search_cmd=(rg -n -m1 -i)
  nan_pattern="\\bnan\\b|loss is nan|nan loss"
else
  search_cmd=(grep -n -m1 -i -E)
  nan_pattern="\\<nan\\>|loss is nan|nan loss"
fi

print_elapsed() {
  local report="$1"
  local elapsed=""
  if command -v rg >/dev/null 2>&1; then
    elapsed=$(
      {
        rg -i "elapsed" "$report" 2>/dev/null \
          | rg -o -e '[0-9]{2}:[0-9]{2}:[0-9]{2}' 2>/dev/null \
          | tail -n1
      } || true
    )
  else
    elapsed=$(
      {
        grep -i "elapsed" "$report" 2>/dev/null \
          | grep -E -o '[0-9]{2}:[0-9]{2}:[0-9]{2}' 2>/dev/null \
          | tail -n1
      } || true
    )
  fi

  if [ -n "$elapsed" ]; then
    echo "Elapsed: $elapsed"
  else
    echo "Elapsed: n/a"
  fi
}

for report in "${sorted_reports[@]}"; do
  echo "== $report =="

  if "${search_cmd[@]}" "cuda out of memory|cudnn_status_alloc_failed|cublas_status_alloc_failed|out of memory" "$report"; then
    :
  elif "${search_cmd[@]}" "$nan_pattern" "$report"; then
    :
  elif "${search_cmd[@]}" "too many open files|errno 24" "$report"; then
    :
  elif "${search_cmd[@]}" "training finished|training completed" "$report"; then
    echo "FINISHED"
  else
    echo "IDK"
  fi

  print_elapsed "$report"
  echo
done
