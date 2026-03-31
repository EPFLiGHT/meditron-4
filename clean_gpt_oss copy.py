#!/usr/bin/env python3

import argparse
import json
import re
from datasets import load_dataset

SYSTEM_MESSAGE = (
    "You are ChatGPT, a large language model trained by OpenAI.\n"
    "Knowledge cutoff: 2024-06\n"
    "Current date: 2026-03-17\n"
    "Reasoning: {reasoning}\n\n"
    "# Valid channels: analysis, commentary, final. Channel must be included for every message."
)

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Path to input .jsonl file")
    p.add_argument("--output", required=True, help="Path to output .jsonl file")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--reasoning", type=str, default="low")
    return p.parse_args()

def get_indices(conversations):
    system_idx, user_idx, assistant_idx = None, None, None
    for i, turn in enumerate(conversations):
        role = turn.get("from")
        if system_idx is None and role == "system":
            system_idx = i
        if user_idx is None and role in ["human", "user"]:
            user_idx = i
        elif user_idx is not None and role == "assistant":
            assistant_idx = i
            break
    return system_idx, user_idx, assistant_idx

def clean_assistant_message(text):
    """
    Extracts the reasoning and final message from the raw assistant text
    and formats them with <think> tags for standard reasoning fine-tuning.
    """
    analysis_match = re.search(r'<\|channel\|>analysis<\|message\|>(.*?)(?=<\|end\|>|<\|start\|>|<\|channel\|>|$)', text, flags=re.DOTALL)
    
    final_match = re.search(r'<\|channel\|>final<\|message\|>(.*?)(?=<\|end\|>|$)', text, flags=re.DOTALL)
    
    if analysis_match and final_match:
        reasoning = analysis_match.group(1).strip()
        final_ans = final_match.group(1).strip()
        return f"<think>\n{reasoning}\n</think>\n\n{final_ans}"
    
    # Fallback: simple string replacement if the exact regex doesn't catch it
    cleaned = text.replace("<|channel|>analysis<|message|>", "<think>\n")
    cleaned = cleaned.replace("<|end|><|start|>assistant<|channel|>final<|message|>", "\n</think>\n\n")
    cleaned = cleaned.replace("<|channel|>final<|message|>", "\n</think>\n\n")
    # Clean up any lingering end tokens
    cleaned = cleaned.replace("<|end|>", "").strip()
    return cleaned

def main():
    args = parse_args()

    print(f"--- Loading Dataset: {args.input} ---")
    ds = load_dataset("json", data_files=args.input, split="train")
    if args.limit:
        ds = ds.select(range(min(args.limit, len(ds))))

    rows = [dict(x) for x in ds]
    system_message = SYSTEM_MESSAGE.format(reasoning=args.reasoning)
    
    for row in rows:
        if not row.get("exact_match"):
            row["exact_match"] = False
        if not row.get("try_count"):
            row["try_count"] = 0

        sidx, uidx, aidx = get_indices(row.get("conversations", []))
        
        # 1. Update the System Message
        if sidx is not None:
            row["conversations"][sidx]["value"] = system_message
            print("System:\n", row["conversations"][sidx]["value"])
            print("-" * 40)
            
        # 2. Update and Clean the Assistant Message
        if aidx is not None:
            raw_assistant_text = row["conversations"][aidx].get("value", "")
            cleaned_text = clean_assistant_message(raw_assistant_text)
            row["conversations"][aidx]["value"] = cleaned_text
            print("Assistant:\n", row["conversations"][aidx]["value"])
            print("=" * 60)

    total_labeled = sum(1 for r in rows if r.get("label_letter"))
    total_correct = sum(1 for r in rows if r.get("exact_match") is True)
    
    if total_labeled > 0:
        print(f"\n✅ Final Accuracy: {(total_correct/total_labeled)*100:.2f}% ({total_correct}/{total_labeled})")

    print(f"--- Saving Cleaned Dataset: {args.output} ---")
    with open(args.output, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()