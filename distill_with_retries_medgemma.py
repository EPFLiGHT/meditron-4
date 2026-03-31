#!/usr/bin/env python3

import argparse
import json
import torch
import re
from datasets import load_dataset
from vllm import LLM, SamplingParams

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
    p.add_argument("--max-tries", type=int, default=8)
    p.add_argument("--reasoning", type=str, default="low")
    p.add_argument("--resume", action="store_true", help="Resume from existing output file")
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

def evaluate_exact_match(text, ground_truth_label):
    if not ground_truth_label:
        return None

    pattern = r"(?i)Answer[^A-E]*(?::)*[^A-E]*(?:boxed)*[^A-E]*([A-E])\b"
    matches = re.findall(pattern, text)
    if len(matches) > 0:
        return matches[-1].upper() == ground_truth_label.upper()
    
    return False

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

    stop_tokens = [tokenizer.eos_token_id]
    if "<end_of_turn>" in tokenizer.vocab:
        stop_tokens.append(tokenizer.vocab["<end_of_turn>"])
    
    sampling_params = SamplingParams(
        temperature=args.temperature,
        max_tokens=args.max_new_tokens,
        skip_special_tokens=False, 
        stop_token_ids=stop_tokens,
    )

    print(f"--- Loading Dataset: {args.input} ---")
    ds = load_dataset("json", data_files=args.input, split="train")
    if args.limit:
        ds = ds.select(range(min(args.limit, len(ds))))

    rows = [dict(x) for x in ds]
    for row in rows:
        if not row.get("exact_match"):
            row["exact_match"] = False
        if not row.get("try_count"):
            row["try_count"] = 0

    for attempt in range(1, args.max_tries + 1):
        pending_indices = []
        prompt_token_ids = []

        for i, row in enumerate(rows):
            if (attempt == 1 and not args.resume) or (not row.get("exact_match") and row.get("label_letter")): #if 1st attempt and false or if labeled and false
                uidx, aidx = get_indices(row.get("conversations", []))
                if uidx is not None:
                    user_text = row["conversations"][uidx].get("value", "").strip()
                    messages = [
                        {"role": "user", "content": user_text}
                    ]
                    token_ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
                    prompt_token_ids.append(token_ids)
                    pending_indices.append((i, aidx))

        if not pending_indices:
            print(f"--- All items matched or no labels found. Ending early at attempt {attempt} ---")
            break

        print(f"--- Attempt {attempt}/{args.max_tries}: Processing {len(pending_indices)} samples ---")
        
        formatted_prompts = [{"prompt_token_ids": ids} for ids in prompt_token_ids]
        outputs = llm.generate(prompts=formatted_prompts, sampling_params=sampling_params)

        for (row_idx, assistant_idx), out in zip(pending_indices, outputs):
            res_text = out.outputs[0].text.strip()
            label = rows[row_idx].get("label_letter")
            
            is_match = evaluate_exact_match(res_text, label)
            
            rows[row_idx]["conversations"][assistant_idx]["value"] = res_text
            rows[row_idx]["exact_match"] = is_match
            rows[row_idx]["try_count"] = attempt

    total_labeled = sum(1 for r in rows if r.get("label_letter"))
    total_correct = sum(1 for r in rows if r.get("exact_match") is True)
    
    if total_labeled > 0:
        print(f"\n✅ Final Accuracy after {args.max_tries} tries: {(total_correct/total_labeled)*100:.2f}% ({total_correct}/{total_labeled})")

    print(f"--- Saving Results to: {args.output} ---")
    with open(args.output, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()