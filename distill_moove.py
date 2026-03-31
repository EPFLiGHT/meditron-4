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
    "Current date: 2026-03-24\n"
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
    p.add_argument("--ratio", type=float, default=1, help="Number of synthetic questions to generate")
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.7, help="Slightly higher temperature for diversity")
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

def build_prompt(tokenizer, system_msg, examples, is_labeled):
    user_content = "Here are example questions and answers to model your format on:\n\n"
    for i, s in enumerate(examples):
        user_content += f"--- Example {i+1} ---\n<question>{s['question']}\n<answer>{s['answer']}\n\n"
    
    if is_labeled:
        user_content += "Now, acting as an expert medical educator, generate a brand new, unique, and clinically accurate MULTIPLE-CHOICE medical question (with defined options and a clear correct label) and its answer. Ensure the answer is supported by current medical consensus and matches the formatting tags above."
    else:
        user_content += "Now, acting as an expert medical educator, generate a brand new, unique, and clinically accurate OPEN-ENDED medical question (without explicit multiple choice options) and its detailed answer. Ensure the answer is supported by current medical consensus and matches the formatting tags above."

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "developer", "content": DEV_MESSAGE},
        {"role": "user", "content": user_content}
    ]
    return tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)

def main():
    args = parse_args()
    ratio = args.ratio
    
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

    labeled_qas = []
    unlabeled_qas = []

    for row in ds:
        conversations = row.get("conversations", [])
        uidx, aidx = get_indices(conversations)
        if uidx is not None and aidx is not None:
            qa_pair = {
                "question": conversations[uidx].get("value", "").strip(),
                "answer": conversations[aidx].get("value", "").strip()
            }
            # Route into appropriate bucket
            if row.get("label_letter"):
                labeled_qas.append(qa_pair)
            else:
                unlabeled_qas.append(qa_pair)
            
    num_labeled = len(labeled_qas)
    num_unlabeled = len(unlabeled_qas)
    
    if num_labeled == 0:
        raise ValueError("Source dataset must contain at least 1 labeled QA pair (requires 'label_letter').")
    if len(unlabeled_qas) == 0:
        raise ValueError("Source dataset must contain at least 1 unlabeled QA pair to sample from.")

    print(f"Extracted {num_labeled} labeled and {num_unlabeled} unlabeled QA pairs.")
    print(f"--- Generating {ratio * (num_labeled + num_unlabeled)} synthetic prompts ({ratio * num_labeled} labeled, {ratio * num_unlabeled} unlabeled) ---")

    prompt_configs = [] # Store tuple of (token_ids, is_labeled) to track output type

    for _ in range(int(num_labeled*args.ratio)):
        k = min(5, len(labeled_qas))
        samples = random.sample(labeled_qas, k)
        token_ids = build_prompt(tokenizer, system_message, samples, is_labeled=True)
        prompt_configs.append((token_ids, True))

    for _ in range(int(num_unlabeled*args.ratio)):
        k = min(5, len(unlabeled_qas))
        samples = random.sample(unlabeled_qas, k)
        token_ids = build_prompt(tokenizer, system_message, samples, is_labeled=False)
        prompt_configs.append((token_ids, False))

    print(f"--- Starting vLLM Generation ---")
    
    formatted_prompts = [{"prompt_token_ids": config[0]} for config in prompt_configs]
    outputs = llm.generate(prompts=formatted_prompts, sampling_params=sampling_params)

    print(f"--- Processing and Saving Results to: {args.output} ---")
    success_count = 0
    
    with open(args.output, "w", encoding="utf-8") as f:
        for out, (_, is_labeled) in zip(outputs, prompt_configs):
            generated_text = out.outputs[0].text.strip()
            
            # Isolate the final message (skip the <|channel|>analysis trace)
            final_text = generated_text
            if "<|channel|>final<|message|>" in final_text:
                final_text = final_text.split("<|channel|>final<|message|>")[-1]
            
            # Extract Question and Answer blocks using regex
            q_match = re.search(r"<question>(.*?)(?:</question>|<answer>|---\s*Answer\s*---)", final_text, re.DOTALL | re.IGNORECASE)
            a_match = re.search(r"(?:<answer>|---\s*Answer\s*---)(.*)", final_text, re.DOTALL | re.IGNORECASE)
            
            if q_match and a_match:
                extracted_q = q_match.group(1).strip()
                extracted_a = a_match.group(1).strip()
                
                # Cleanup hallucinated closing tags
                extracted_a = re.sub(r"</answer>$", "", extracted_a, flags=re.IGNORECASE).strip()
                
                if extracted_q and extracted_a:
                    synthetic_row = {
                        "is_labeled": is_labeled, # Keeps a record of what type of question this was meant to be
                        "conversations": [
                            {"from": "human", "value": extracted_q},
                            {"from": "assistant", "value": extracted_a}
                        ]
                    }
                    f.write(json.dumps(synthetic_row, ensure_ascii=False) + "\n")
                    success_count += 1

    print(f"✅ Extraction complete! Saved {success_count}/{ratio * (num_labeled + num_unlabeled)} valid synthetic conversations.")

if __name__ == "__main__":
    main()