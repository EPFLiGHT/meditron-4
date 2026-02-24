#!/bin/bash
#SBATCH --job-name meditron-eval
#SBATCH --output eval_reports/R-%x.%j.err
#SBATCH --error eval_reports/R-%x.%j.err
#SBATCH --nodes 1
#SBATCH --ntasks-per-node 1
#SBATCH --gres gpu:4
#SBATCH --cpus-per-task 64
#SBATCH --partition=normal
#SBATCH --time=0:29:59
#SBATCH --environment ../.edf/new_axolotl.toml
#SBATCH -A a127

ulimit -c 0 # prevents core dumps


# Prefer the submit directory (available on workers) so we can find helpers after sbatch copies the script to /var/spool.
SCRIPT_DIR="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
source "$SCRIPT_DIR/scripts/slack_helpers.sh"

# =========================================================
# PHASE 1: SUBMISSION LOGIC (Runs on Login Node)
# =========================================================

if [ -z "$SLURM_JOB_ID" ]; then
    RUN_NAME="meditron-eval-logits"
    MODEL_PATH="$1"
    DEBUG_FLAG=0
    MODEL_PARALLELISM=0
    if [ "${2:-}" = "--debug" ]; then
        DEBUG_FLAG=1
    elif [ "${2:-}" = "--model_parallelism" ]; then
        MODEL_PARALLELISM=1
    fi
    if [ "${3:-}" = "--debug" ]; then
        DEBUG_FLAG=1
    elif [ "${3:-}" = "--model_parallelism" ]; then
        MODEL_PARALLELISM=1
    fi
    SCRIPT_PATH="$0"

    # Load env (PROJECT_ROOT, USER_STORAGE, etc.)
    if [ -f .env ]; then
        set -o allexport
        source .env
        set +o allexport
    fi

    echo "This script is self-submitting..."
    echo "🏷️  Run Name:  $RUN_NAME"
    echo "📁  Model Path: $MODEL_PATH"

    # Avoid exporting a job-scoped TMPDIR before SLURM_JOB_ID exists.
    export TMPDIR="${SLURM_TMPDIR:-/tmp}"

    if [ -z "$MODEL_PATH" ]; then
        echo "Usage: $0 /path/to/model [--debug] [--model_parallelism]"
        exit 1
    fi

    SBATCH_ARGS=("$RUN_NAME" "$MODEL_PATH")
    if [ "$DEBUG_FLAG" -eq 1 ]; then
        SBATCH_ARGS+=("--debug")
    fi
    if [ "$MODEL_PARALLELISM" -eq 1 ]; then
        SBATCH_ARGS+=("--model_parallelism")
    fi
    SUBMISSION_OUTPUT=$(sbatch -J "$RUN_NAME" "$SCRIPT_PATH" "${SBATCH_ARGS[@]}")
    JOB_ID=$(echo "$SUBMISSION_OUTPUT" | awk '{print $4}')

    echo "🚀 Submitted Job: $JOB_ID"

    LOG_FILE="$PROJECT_ROOT/eval_reports/R-${RUN_NAME}.${JOB_ID}.err"
    echo "Waiting for log file: $LOG_FILE"

    while [ ! -f "$LOG_FILE" ]; do
        sleep 1
    done

    echo "✅ Log found! Tailing (Ctrl+C to stop watching, job will continue)..."
    echo "-------------------------------------------------------------------"
    tail -n 0 -F "$LOG_FILE" &
    TAIL_PID=$!

    while squeue -j "$JOB_ID" >/dev/null 2>&1; do
        sleep 5
    done

    echo "Job $JOB_ID finished; stopping log tail."
    kill "$TAIL_PID" 2>/dev/null || true
    wait "$TAIL_PID" 2>/dev/null || true

    exit 0
fi



# =========================================================
# PHASE 2: WORKER LOGIC (Runs on Compute Node)
# =========================================================

RUN_NAME="$1"
MODEL_PATH="$2"
DEBUG_FLAG=0
MODEL_PARALLELISM=0
if [ "$MODEL_PATH" = "--debug" ]; then
    DEBUG_FLAG=1
    MODEL_PATH=""
elif [ "$MODEL_PATH" = "--model_parallelism" ]; then
    MODEL_PARALLELISM=1
    MODEL_PATH=""
elif [ "${3:-}" = "--debug" ]; then
    DEBUG_FLAG=1
elif [ "${3:-}" = "--model_parallelism" ]; then
    MODEL_PARALLELISM=1
fi

# Back-compat: allow a single positional arg to mean MODEL_PATH.
if [ -z "$MODEL_PATH" ] && [ -n "$RUN_NAME" ]; then
    MODEL_PATH="$RUN_NAME"
    RUN_NAME=""
fi

if [ -z "$RUN_NAME" ]; then
    RUN_NAME="eval-logits"
fi
if [ -z "$MODEL_PATH" ]; then
    echo "MODEL_PATH is empty; pass it as the first argument."
    exit 1
fi
if [ ! -e "$MODEL_PATH" ] && [ -n "$STORAGE_ROOT" ] && [ -e "$STORAGE_ROOT$MODEL_PATH" ]; then
    MODEL_PATH="$STORAGE_ROOT$MODEL_PATH"
    echo "Resolved MODEL_PATH to: $MODEL_PATH"
fi

# 1. Project root & env
export PROJECT_ROOT=${SLURM_SUBMIT_DIR:-$(pwd)}
cd "$PROJECT_ROOT"

echo "Project Root detected as: $PROJECT_ROOT"

if [ -f .env ]; then
    set -o allexport
    source .env
    set +o allexport
fi

# Ensure TMPDIR exists to avoid Slurm temp directory errors.
export TMPDIR="${SLURM_TMPDIR:-/tmp}"
mkdir -p "$TMPDIR"

# Default to insecure curl for Slack if the node lacks CA bundle; can override by exporting SLACK_INSECURE=0
export SLACK_INSECURE="${SLACK_INSECURE:-1}"

# Slack runtime bookkeeping (worker side)
START_TS="$(date +%s)"
START_HUMAN="$(date -Is)"
SLACK_JOB_ID="${SLURM_JOB_ID:-?}"
FAILED_CMD=""
export SLACK_PHASE="eval"
export SLACK_REPORTS_DIR="$PROJECT_ROOT/eval_reports"
export SLACK_TAG=":robot_face: [Meditron]"
trap 'FAILED_CMD=$BASH_COMMAND' ERR
trap 'rc=$?; slack_notify "$rc" "eval"; exit "$rc"' EXIT
set -eo pipefail

cd "$PROJECT_ROOT/../lm-evaluation-harness"
pip install -e .
#pip install --upgrade --no-deps "datasets>=2.19.0,<3.0.0"

cd "$PROJECT_ROOT/../lm-evaluation-harness/lm_eval/tasks"

export WORLD_SIZE=$SLURM_NNODES
export MASTER_ADDR=$(hostname)
export MASTER_PORT=6300

export HF_HOME=$USER_STORAGE/hf
export HF_DATASETS_TRUST_REMOTE_CODE=1
export TRITON_CACHE_DIR=$USER_STORAGE/triton
export HF_DATASETS_CACHE=$HF_HOME/datasets
unset TRANSFORMERS_CACHE

export LM_EVAL_INCLUDE_PATH="/users/$USER/lm-evaluation-harness/lm_eval/tasks"

echo "WORLD_SIZE=$WORLD_SIZE"
echo "MASTER_ADDR=$MASTER_ADDR"
echo "MODEL_PATH=$MODEL_PATH"

echo "START TIME: $(date)"
set -x

SAFE_MODEL_TAG="$(basename "$MODEL_PATH" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_.-' '_' | sed 's/^_//;s/_$//')"
RUN_TAG="${RUN_NAME:-eval}"
JOB_TAG="${SLURM_JOB_ID:-nojob}"
OUTPUT_DIR="$PROJECT_ROOT/eval_results/${RUN_TAG}_${SAFE_MODEL_TAG}_${JOB_TAG}"
mkdir -p "$OUTPUT_DIR"

LIMIT_ARGS=()
if [ "$DEBUG_FLAG" -eq 1 ]; then
    LIMIT_ARGS=(--limit 100)
fi

#MODEL_ARGS="pretrained=$MODEL_PATH,dtype=bfloat16,attn_implementation=flash_attention_3,trust_remote_code=True"
MODEL_ARGS="pretrained=$MODEL_PATH,dtype=bfloat16,attn_implementation=flash_attention_2,trust_remote_code=True"

TASKS="multimedqa"

if [ "$MODEL_PARALLELISM" -eq 1 ]; then
python3 -m lm_eval \
  --model hf \
  --model_args "$MODEL_ARGS,parallelize=True" \
  --tasks "$TASKS" \
  --batch_size 16 \
  --verbosity "DEBUG" \
  --log_samples \
  --output_path "$OUTPUT_DIR" \
  --include_path "$LM_EVAL_INCLUDE_PATH" \
  --gen_kwargs max_new_tokens=1024 \
  "${LIMIT_ARGS[@]}" \
  --apply_chat_template tokenizer_default 
else
accelerate launch --num_processes 4 --num_machines 1 --mixed_precision bf16 --dynamo_backend no -m lm_eval \
  --model hf \
  --model_args "$MODEL_ARGS" \
  --tasks "$TASKS" \
  --batch_size 16 \
  --verbosity "DEBUG" \
  --log_samples \
  --output_path "$OUTPUT_DIR" \
  --include_path "$LM_EVAL_INCLUDE_PATH" \
  --gen_kwargs max_new_tokens=1024 \
  "${LIMIT_ARGS[@]}" \
  --apply_chat_template tokenizer_default 
fi

echo "END TIME: $(date)"

#pubmedqa,medmcqa,medqa_4options,pubmedqa_g,medmcqa_g,medqa_g,medxpertqa,medxpertqa_g,mmlu_flan_cot_zeroshot
