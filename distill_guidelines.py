#!/usr/bin/env python3

import argparse
import json
import re
import torch
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
    "Your task is to generate clinical vignette-style questions along with its correct answer. "
    "based STRICTLY on the provided medical guideline. "
    "Focus on realistic patient presentations (age, symptoms, physical exam findings), "
    "identifying 'red flags', and diagnostic reasoning highlighted in the text. "
    "Do not include outside information or unproven treatments. "
)

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Path to input .jsonl dataset containing guidelines")
    p.add_argument("--output", required=True, help="Path to output .jsonl file for synthetic QA data")
    p.add_argument("--model", required=True, help="HF model path or name")
    p.add_argument("--max-new-tokens", type=int, default=8192, help="High limit to fit 10 vignettes")
    p.add_argument("--temperature", type=float, default=0.6, help="Lower temp to keep it grounded in the text")
    p.add_argument("--tp", type=int, default=0, help="Tensor parallel size (0 for auto-detect)")
    p.add_argument("--utilization", type=float, default=0.90, help="VRAM utilization")
    p.add_argument("--reasoning", type=str, default="low")
    p.add_argument("--limit", type=int, default=None)

    return p.parse_args()

def build_prompt(tokenizer, guideline_text):
    user_content = (
        f"Here is the medical guideline:\n\n"
        f"=== GUIDELINE START ===\n{guideline_text}\n=== GUIDELINE END ===\n\n"
        "Based ONLY on the guideline above, generate exactly 10 unique MULTIPLE-CHOICE clinical vignette questions and their answers. "
        "Each question should present a realistic patient scenario that tests the diagnostic or management principles in the text. "
        "For each vignette, provide 4-5 plausible multiple-choice options (A-E). "
        "Ensure distractors represent common diagnostic pitfalls or 'next best steps' "
        "that are incorrect based strictly on the provided guideline.\n\n"
        "You MUST format EACH of the 10 items exactly as follows, using these specific XML tags:\n\n"
        "<qa>\n"
        "<question>\n"
        "Patient scenario and the specific question here.\n"
        "A) [Option 1]\n"
        "B) [Option 2]\n"
        "C) [Option 3]\n"
        "D) [Option 4]\n"
        "</question>\n"
        "<answer>The correct answer and the rationale explaining why, referencing the guideline.</answer>\n"
        "</qa>"
    )

    messages = [
        {"role": "system", "content": DEV_MESSAGE},
        {"role": "user", "content": user_content}
    ]
    
    # Return raw token IDs for vLLM to process accurately
    return tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)

def main():
    args = parse_args()
    
    num_gpus = torch.cuda.device_count()
    tp_size = args.tp if args.tp > 0 else num_gpus

    print(f"--- Initializing vLLM (Model: {args.model}, TP: {tp_size}) ---")
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

    max_model_len = llm.llm_engine.model_config.max_model_len
    print("model context lenght : !!!!!", max_model_len)

    print(f"--- Loading Source Guidelines from: {args.input} ---")
    prompt_configs = []
    guideline_count = 0
    
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            # Adjust the key "text" if your JSONL uses a different key for the guideline body
            guideline_text = data.get("text", "").strip()
            
            if guideline_text:

                token_count = len(tokenizer.encode(guideline_text))
                if token_count > max_model_len * (3/4): 
                    print(f"Warning: Guideline {guideline_count} is very long ({token_count} tokens). Consider chunking.")
                token_ids = build_prompt(tokenizer, guideline_text)
                prompt_configs.append(token_ids)
                guideline_count += 1

    if args.limit:
        guideline_count = args.limit
        prompt_configs = prompt_configs[:args.limit]

    print(f"Loaded {guideline_count} guidelines. Generating ~{guideline_count * 10} synthetic QAs...")

    formatted_prompts = [{"prompt_token_ids": tids} for tids in prompt_configs]
    outputs = llm.generate(prompts=formatted_prompts, sampling_params=sampling_params)

    print(f"--- Processing and Saving Results to: {args.output} ---")
    total_extracted_qas = 0
    
    with open(args.output, "w", encoding="utf-8") as out_f:
        for i, out in enumerate(outputs):
            generated_text = out.outputs[0].text.strip()
            
            # Find all instances of <qa> blocks in the output
            # re.DOTALL allows the dot (.) to match newlines
            qa_matches = re.findall(
                r"<qa>\s*<question>(.*?)</question>\s*<answer>(.*?)</answer>\s*</qa>", 
                generated_text, 
                re.DOTALL | re.IGNORECASE
            )
            
            if not qa_matches:
                print(f"Warning: No valid <qa> blocks found in output for guideline index {i}.")
                continue
                
            for q_text, a_text in qa_matches:
                q_text = q_text.strip()
                a_text = a_text.strip()
                
                if q_text and a_text:
                    synthetic_row = {
                        "conversations": [
                            {"from": "human", "value": q_text},
                            {"from": "assistant", "value": a_text}
                        ],
                        "parse_failed": False,
                        "raw_output": generated_text
                    }
                    out_f.write(json.dumps(synthetic_row, ensure_ascii=False) + "\n")
                    total_extracted_qas += 1

    print(f"✅ Extraction complete! Saved {total_extracted_qas} valid synthetic QA pairs to {args.output}.")

if __name__ == "__main__":
    main()