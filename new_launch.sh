#!/bin/bash
#SBATCH --job-name=vllm-task
#SBATCH --output=logs/R-%x.%j.err
#SBATCH --error=logs/R-%x.%j.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --time=1:29:59
#SBATCH --partition=debug
#SBATCH --environment ../.edf/inference.toml
#SBATCH -A a127

set -e

PY_SCRIPT="$1"

if [ -z "$PY_SCRIPT" ]; then
    echo "Error: No python script specified."
    echo "Usage: sbatch $0 <script.py> [args...]"
    exit 1
fi

if [ -z "$SLURM_JOB_ID" ]; then
    echo "login node"

    mkdir -p logs

    SUBMIT_OUT=$(sbatch "$0" "$@")
    JOB_ID=$(echo "$SUBMIT_OUT" | awk '{print $4}')
    echo "Submitted job $JOB_ID running $PY_SCRIPT with args: $@"

    LOG_FILE="logs/R-vllm-task.${JOB_ID}.err"

    while [ ! -f "$LOG_FILE" ]; do sleep 1; done
    echo "Log file: $LOG_FILE"
    tail -n 0 -F "$LOG_FILE" &
    TAIL_PID=$!

    while squeue -h -j "$JOB_ID" | grep -q .; do sleep 5; done

    kill "$TAIL_PID" 2>/dev/null || true
    wait "$TAIL_PID" 2>/dev/null || true
    exit 0
fi

echo "compute node"
cd meditron-4

shift

echo "Executing: python $PY_SCRIPT $@"
python "$PY_SCRIPT" "$@"