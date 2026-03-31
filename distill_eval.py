#!/usr/bin/env python3

import argparse
import json
import re
import torch
from collections import defaultdict
from datasets import load_dataset
from vllm import LLM, SamplingParams

SYSTEM_MESSAGE = (
    "You are ChatGPT, a large language model trained by OpenAI.\n"
    "Knowledge cutoff: 2024-06\n"
    "Current date: 2026-03-17\n"
    "Reasoning: {reasoning}\n\n"
    "# Valid channels: analysis, commentary, final. Channel must be included for every message."
)

DEV_MESSAGE = (
    "You are an expert medical board examiner and instructional designer. Your task is to evaluate the quality of a medical question (either multiple-choice or open-ended) intended for a high-quality QA dataset."
)

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Path to input .jsonl file")
    p.add_argument("--output", required=True, help="Path to output .jsonl file")
    p.add_argument("--model", required=True, help="HF model path or name")
    p.add_argument("--max-new-tokens", type=int, default=2048)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--tp", type=int, default=0, help="Tensor parallel size (0 for auto-detect)")
    p.add_argument("--utilization", type=float, default=0.90, help="VRAM utilization")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--reasoning", type=str, default="low")
    return p.parse_args()

def get_indices(conversations):
    user_idx, assistant_idx = None, None
    for i, turn in enumerate(conversations):
        role = turn.get("from")
        if user_idx is None and role in ["human", "user"]:
            user_idx = i
        elif user_idx is not None and role == "assistant":
            assistant_idx = i
            break
    return user_idx, assistant_idx

def extract_evaluation_fields(text):
    """Extracts the evaluation metrics from the LLM's raw text response."""
    # Regex patterns are designed to be flexible with markdown formatting (e.g., **Difficulty**)
    diff_match = re.search(r"(?i)Difficulty level[*\s:]*([1-5])", text)
    rel_match = re.search(r"(?i)Clinical relevance[*\s:]*([1-5])", text)
    rec_match = re.search(r"(?i)Overall Recommendation[*\s:]*(Keep|Revise|Discard)", text)
    feedback_match = re.search(r"(?i)Actionable Feedback[*\s:]*(.*)", text, re.DOTALL)
    
    return {
        "eval_difficulty": int(diff_match.group(1)) if diff_match else None,
        "eval_relevance": int(rel_match.group(1)) if rel_match else None,
        "eval_recommendation": rec_match.group(1).capitalize() if rec_match else None,
        "eval_feedback": feedback_match.group(1).strip() if feedback_match else None
    }

def print_comparisons(rows):
    """Calculates and prints comparisons for Labeled vs Unlabeled, and Try Count vs Evaluation."""
    labeled_stats = {"count": 0, "diff_sum": 0, "rel_sum": 0}
    unlabeled_stats = {"count": 0, "diff_sum": 0, "rel_sum": 0}
    try_stats = defaultdict(lambda: {"count": 0, "diff_sum": 0, "rel_sum": 0, "recs": defaultdict(int)})
    
    for row in rows:
        diff = row.get("eval_difficulty")
        rel = row.get("eval_relevance")
        rec = row.get("eval_recommendation")
        try_count = row.get("try_count", "Unknown")
        has_label = bool(row.get("label_letter"))
        
        # We only aggregate rows where extraction was successful
        if diff is not None and rel is not None:
            # Labeled vs Unlabeled
            if has_label:
                labeled_stats["count"] += 1
                labeled_stats["diff_sum"] += diff
                labeled_stats["rel_sum"] += rel
            else:
                unlabeled_stats["count"] += 1
                unlabeled_stats["diff_sum"] += diff
                unlabeled_stats["rel_sum"] += rel
                
            # Try count
            try_stats[try_count]["count"] += 1
            try_stats[try_count]["diff_sum"] += diff
            try_stats[try_count]["rel_sum"] += rel
            if rec:
                try_stats[try_count]["recs"][rec] += 1

    print("\n" + "="*50)
    print("📊 EVALUATION COMPARISON REPORT")
    print("="*50)
    
    print("\n--- Labeled vs. Unlabeled Questions ---")
    if labeled_stats["count"] > 0:
        print(f"Labeled Questions   (n={labeled_stats['count']}): Avg Difficulty: {labeled_stats['diff_sum']/labeled_stats['count']:.2f} | Avg Relevance: {labeled_stats['rel_sum']/labeled_stats['count']:.2f}")
    else:
        print("Labeled Questions   (n=0): No data")
        
    if unlabeled_stats["count"] > 0:
        print(f"Unlabeled Questions (n={unlabeled_stats['count']}): Avg Difficulty: {unlabeled_stats['diff_sum']/unlabeled_stats['count']:.2f} | Avg Relevance: {unlabeled_stats['rel_sum']/unlabeled_stats['count']:.2f}")
    else:
        print("Unlabeled Questions (n=0): No data")

    print("\n--- Try Count vs. Evaluation ---")
    for tc, stats in sorted(try_stats.items(), key=lambda x: str(x[0])):
        avg_diff = stats["diff_sum"] / stats["count"]
        avg_rel = stats["rel_sum"] / stats["count"]
        recs_str = ", ".join([f"{k}: {v}" for k, v in stats["recs"].items()])
        print(f"Try Count {tc} (n={stats['count']}): Avg Diff: {avg_diff:.2f} | Avg Rel: {avg_rel:.2f} | Recs: {recs_str}")
    print("="*50 + "\n")

def main():
    args = parse_args()
    
    num_gpus = torch.cuda.device_count()
    tp_size = args.tp if args.tp > 0 else num_gpus

    llm = LLM(
        model=args.model,
        tensor_parallel_size=tp_size,
        gpu_memory_utilization=args.utilization,
        trust_remote_code=True
    )
    tokenizer = llm.get_tokenizer()
    
    sampling_params = SamplingParams(
        temperature=args.temperature,
        max_tokens=args.max_new_tokens,
        skip_special_tokens=False, 
        stop_token_ids=[tokenizer.eos_token_id],
    )

    print(f"--- Loading Dataset: {args.input} ---")
    ds = load_dataset("json", data_files=args.input, split="train")
    if args.limit:
        ds = ds.select(range(min(args.limit, len(ds))))
    system_message = SYSTEM_MESSAGE.format(reasoning=args.reasoning)

    pending_indices = []
    prompt_token_ids = []
    rows = [dict(x) for x in ds]

    for i, row in enumerate(ds):
        uidx, aidx = get_indices(row.get("conversations", []))
        if uidx is not None:
            user_text = row["conversations"][uidx].get("value", "").strip()

            user_content = (
                "### Task\nAnalyze the following medical question based on the provided evaluation rubric. "
                "Do not answer the question; evaluate the *design* and *quality* of the question itself.\n\n"
                f"### Question to Evaluate\n{user_text}\n\n"
                "### Response Format\n"
                "- **Difficulty level:** [1-5]\n"
                "- **Clinical relevance:** [1-5]\n"
                "- **Overall Recommendation:** [Keep, Revise, or Discard]\n"
                "- **Actionable Feedback:** [If Revise/Discard, explain exactly what is wrong. If Keep, output None]"
            )
            
            messages = [
                {"role": "system", "content": system_message},
                {"role": "developer", "content": DEV_MESSAGE},
                {"role": "user", "content": user_content}
            ]
            
            token_ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
            prompt_token_ids.append(token_ids)
            # If assistant index doesn't exist, we'll append to the conversation later
            pending_indices.append((i, uidx, aidx))

    print(f"--- Starting vLLM Generation ---")
    formatted_prompts = [{"prompt_token_ids": ids} for ids in prompt_token_ids]
    outputs = llm.generate(prompts=formatted_prompts, sampling_params=sampling_params)

    print(f"--- Processing Results ---")
    
    for (row_idx, uidx, aidx), out in zip(pending_indices, outputs):
        res_text = out.outputs[0].text.strip()
        
        # 1. Update the conversation array with the raw LLM output
        if aidx is not None:
            rows[row_idx]["conversations"][aidx]["value"] = res_text
        else:
            rows[row_idx]["conversations"].append({"from": "assistant", "value": res_text})
        
        # 2. Extract the structured fields and save them at the root level of the row
        extracted_data = extract_evaluation_fields(res_text)
        rows[row_idx].update(extracted_data)

    # 3. Print the analytical comparisons
    print_comparisons(rows)

    # 4. Save to output
    print(f"--- Saving Results to: {args.output} ---")
    with open(args.output, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()