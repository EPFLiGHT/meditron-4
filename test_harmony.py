import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

def test_harmony_generation():
    model_id = "openai/gpt-oss-20b"
    
    print(f"Loading tokenizer and model for {model_id}...")
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    
    # Load model (device_map="auto" will automatically place it on your GPU)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        device_map="auto",
        torch_dtype=torch.bfloat16, 
        trust_remote_code=True
    )

    # Define the Harmony-compliant conversation
    messages = [
        {
            "role": "system", 
            # Setting reasoning to low for faster debugging
            "content": "Reasoning: low\n# Valid channels: analysis, commentary, final. Channel must be included for every message."
        },
        {
            "role": "developer", 
            "content": "# Instructions\nYou are a concise and helpful assistant."
        },
        {
            "role": "user", 
            "content": "Write a short haiku about debugging code."
        }
    ]

    print("\n--- Applying Template and Tokenizing ---")
    
    # tokenize=True this time, and return as PyTorch tensors ("pt")
    input_ids = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt"
    ).to(model.device)

    print("\n--- Generating Response ---")
    
    # Generate the output
    with torch.no_grad():
        output_ids = model.generate(
            input_ids,
            max_new_tokens=2048,
            temperature=0.7,
            # It's crucial to let the model hit its natural Harmony stop tokens
            eos_token_id=tokenizer.eos_token_id 
        )

    # Slice the output to ignore the prompt we fed into it, giving us ONLY the new tokens
    generated_ids = output_ids[0][input_ids.shape[1]:]
    
    # CRITICAL: Set skip_special_tokens=False. 
    # If this is True, Hugging Face strips out the Harmony channel tags!
    raw_generation = tokenizer.decode(generated_ids, skip_special_tokens=False)
    
    print("\n✅ RAW HARMONY GENERATION OUTPUT:\n")
    print(raw_generation)

if __name__ == "__main__":
    test_harmony_generation()