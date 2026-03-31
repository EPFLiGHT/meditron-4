#!/usr/bin/env python3

import argparse
import json
import random
import re
import torch
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
    "You are a distinguished medical educator and board-certified physician "
    "tasked with creating high-quality, clinically accurate content for a medical exam. "
    "Your task is to generate a new, unique, and evidence-based medical question "
    "along with its correct answer. The content must reflect realistic clinical scenarios, "
    "standard-of-care protocols, and current medical consensus. Avoid controversial, "
    "outdated, or unproven treatments. "
    "You will be provided with 5 examples. Use them strictly to understand the desired "
    "format, diagnostic difficulty, and clinical depth. DO NOT copy them. "
    "Generate a completely new, scientifically rigorous question that would be unconditionally "
    "approved by a medical review board."
)

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Path to input .jsonl dataset")
    p.add_argument("--output", required=True, help="Path to output .jsonl file for synthetic data")
    p.add_argument("--model", required=True, help="HF model path or name")
    p.add_argument("--num-synthetic", type=int, default=200000, help="Number of synthetic questions to generate")
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.8, help="Slightly higher temperature for diversity")
    p.add_argument("--tp", type=int, default=0, help="Tensor parallel size (0 for auto-detect)")
    p.add_argument("--utilization", type=float, default=0.90, help="VRAM utilization")
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

    print(f"--- Loading Source Dataset: {args.input} ---")
    ds = load_dataset("json", data_files=args.input, split="train")
    system_message = SYSTEM_MESSAGE.format(reasoning=args.reasoning)

    valid_qas = []
    for row in ds:
        conversations = row.get("conversations", [])
        uidx, aidx = get_indices(conversations)
        if uidx is not None and aidx is not None:
            valid_qas.append({
                "question": conversations[uidx].get("value", "").strip(),
                "answer": conversations[aidx].get("value", "").strip()
            })
            
    if len(valid_qas) < 5:
        raise ValueError("Source dataset must contain at least 5 valid QA pairs to sample from.")

    print(f"Extracted {len(valid_qas)} valid QA pairs for few-shot sampling.")
    print(f"--- Generating {args.num_synthetic} synthetic prompts ---")

    prompt_token_ids = []
    
    for _ in range(args.num_synthetic):
        samples = random.sample(valid_qas, 5)
        
        user_content = "Here are 5 example questions and answers:\n\n"
        for i, s in enumerate(samples):
            user_content += f"--- Example {i+1} ---\n<question>{s['question']}\n<answer>{s['answer']}\n\n"
        
            user_content += "Now, acting as an expert medical educator, generate a brand new, unique, and clinically accurate medical question and its answer in the exact same format. Ensure the answer is supported by current medical consensus."
        messages = [
            {"role": "system", "content": system_message},
            {"role": "developer", "content": DEV_MESSAGE},
            {"role": "user", "content": user_content}
        ]
        
        token_ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
        prompt_token_ids.append(token_ids)

    print(f"--- Starting vLLM Generation ---")
    
    # Optional: vLLM can accept the list of token lists directly in newer versions, 
    # but wrapping in dicts is standard for the `prompts` arg structure.
    formatted_prompts = [{"prompt_token_ids": ids} for ids in prompt_token_ids]
    outputs = llm.generate(prompts=formatted_prompts, sampling_params=sampling_params)

    print(f"--- Processing and Saving Results to: {args.output} ---")
    success_count = 0
    
    with open(args.output, "w", encoding="utf-8") as f:
        for out in outputs:
            generated_text = out.outputs[0].text.strip()
            
            # 1. Isolate the final message (skip the <|channel|>analysis trace)
            final_text = generated_text
            if "<|channel|>final<|message|>" in final_text:
                final_text = final_text.split("<|channel|>final<|message|>")[-1]
            
            # 2. Extract Question and Answer blocks using regex
            # Looks for content inside <question>...</question> or before <answer> / --- Answer ---
            q_match = re.search(r"<question>(.*?)(?:</question>|<answer>|---\s*Answer\s*---)", final_text, re.DOTALL | re.IGNORECASE)
            
            # Looks for everything after <answer> or --- Answer ---
            a_match = re.search(r"(?:<answer>|---\s*Answer\s*---)(.*)", final_text, re.DOTALL | re.IGNORECASE)
            
            if q_match and a_match:
                extracted_q = q_match.group(1).strip()
                extracted_a = a_match.group(1).strip()
                
                # Cleanup: remove a trailing </answer> if the model hallucinated one at the very end
                extracted_a = re.sub(r"</answer>$", "", extracted_a, flags=re.IGNORECASE).strip()
                
                # Make sure both aren't empty
                if extracted_q and extracted_a:
                    synthetic_row = {
                        "conversations": [
                            {"from": "human", "value": extracted_q},
                            {"from": "assistant", "value": extracted_a}
                        ]
                    }
                    f.write(json.dumps(synthetic_row, ensure_ascii=False) + "\n")
                    success_count += 1

    print(f"✅ Extraction complete! Saved {success_count}/{args.num_synthetic} valid synthetic conversations.")

if __name__ == "__main__":
    main()