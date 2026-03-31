#!/usr/bin/env python3

import argparse
import json
import re
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Path to input .jsonl file")
    p.add_argument("--output", required=True, help="Path to output .jsonl file")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no_reasoning", action="store_true")
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

def clean_assistant_message(text, no_reasoning):
    """
    Extracts the reasoning and final message from the raw assistant text.
    Returns: (cleaned_text, is_valid_boolean)
    """
    analysis_match = re.search(r'<\|channel\|>analysis<\|message\|>(.*?)(?=<\|end\|>|<\|start\|>|<\|channel\|>|$)', text, flags=re.DOTALL)
    
    final_match = re.search(r'<\|channel\|>final<\|message\|>(.*?)(?=<\|end\|>|$)', text, flags=re.DOTALL)
    
    if analysis_match and final_match:
        reasoning = analysis_match.group(1).strip()
        final_ans = final_match.group(1).strip()
        if no_reasoning:
            cleaned = final_ans
        else:
            cleaned = f"<think>\n{reasoning}\n</think>\n\n{final_ans}"
        return cleaned, True
    
    return text, False

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
    
    missing_channels_count = 0

    for row in rows:
        if not row.get("exact_match"):
            row["exact_match"] = False
        if not row.get("try_count"):
            row["try_count"] = 0

        sidx, uidx, aidx = get_indices(row.get("conversations", []))
        
        if aidx is not None:
            raw_assistant_text = row["conversations"][aidx].get("value", "")
            cleaned_text, is_valid = clean_assistant_message(raw_assistant_text, args.no_reasoning)
            
            if not is_valid:
                missing_channels_count += 1
                print(row)
                
            row["conversations"][aidx]["value"] = cleaned_text

        row["conversations"] = [
            turn for turn in row["conversations"] if turn.get("from") != "system"
        ]

    if missing_channels_count > 0:
        print(f"\n❌ Error: Found {missing_channels_count} assistant message(s) missing 'analysis' or 'final' channels.")

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