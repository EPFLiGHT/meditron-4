#!/usr/bin/env python3

import argparse
import json
import re
import sys
from collections import Counter
from datasets import load_dataset

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Path to input .jsonl file")
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args()

def extract_chosen_answer(text):
    """
    Extracts the chosen answer by looking for 'Answer: X', '**X.**', 
    or just 'X.' at the beginning of the text.
    """
    # 1. Look for explicit "Answer: [A-E]" or similar
    pattern_answer = r"(?i)Answer[^A-E]*(?::)*[^A-E]*(?:boxed)*[^A-E]*([A-E])\b"
    matches = re.findall(pattern_answer, text)
    if matches:
        return matches[-1].upper()
    
    # 2. Look for "**A.**", "**B -", etc. (very common in your logs)
    pattern_bold = r"\*\*\s*([A-E])\s*[\.\-\–]"
    matches = re.findall(pattern_bold, text)
    if matches:
        return matches[0].upper()
    
    # 3. Look for "Option [1-5]" and map it to A-E
    pattern_option = r"(?i)Option\s*([1-5])"
    matches = re.findall(pattern_option, text)
    if matches:
        # Map 1->A, 2->B, 3->C, 4->D, 5->E
        return chr(int(matches[0]) + 64) 

    return "Unknown"

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

def main():
    args = parse_args()

    print(f"--- Loading Dataset: {args.input} ---")
    rows = []
    with open(args.input, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if args.limit is not None and i >= args.limit:
                break
            line = line.strip()
            if line:  # Skip empty lines
                rows.append(json.loads(line))
    
    label_distribution = Counter() # Initialize Counter

    for row in rows:
        sidx, uidx, aidx = get_indices(row.get("conversations", []))
        
        if aidx is not None:
            raw_assistant_text = row["conversations"][aidx].get("value", "")
            
            choice = extract_chosen_answer(raw_assistant_text)
            label_distribution[choice] += 1
            
            row["conversations"][aidx]["value"] = raw_assistant_text
            # if choice == "Unknown":
            #     print(row["conversations"])

        row["conversations"] = [
            turn for turn in row["conversations"] if turn.get("from") != "system"
        ]

    print("\n--- Label Distribution (Assistant Choices) ---")
    total = sum(label_distribution.values())
    for label in sorted(label_distribution.keys()):
        count = label_distribution[label]
        percentage = (count / total) * 100 if total > 0 else 0
        print(f"Option {label}: {count} ({percentage:.2f}%)")
    print(f"Total processed: {total}")



if __name__ == "__main__":
    main()