import json
import os

def clean_jsonl(input_path, output_path):
    print(f"Reading from {input_path}...")
    
    cleaned_count = 0
    error_count = 0
    
    with open(input_path, 'r', encoding='utf-8') as f_in, \
         open(output_path, 'w', encoding='utf-8') as f_out:
        
        for i, line in enumerate(f_in):
            line = line.strip()
            if not line:
                continue
                
            try:
                data = json.loads(line)
                
                # 1. Force ID to be a string (Fixes the ArrowInvalid error)
                if "id" in data:
                    data["id"] = str(data["id"])
                
                # 2. Basic validation for 'conversations'
                if "conversations" not in data or not isinstance(data["conversations"], list):
                    print(f"Row {i}: Missing or invalid conversations format. Skipping.")
                    error_count += 1
                    continue
                
                # Write the cleaned line
                f_out.write(json.dumps(data, ensure_ascii=False) + "\n")
                cleaned_count += 1
                
            except json.JSONDecodeError:
                print(f"Row {i}: Invalid JSON syntax. Skipping.")
                error_count += 1

    print(f"--- Finished ---")
    print(f"Successfully cleaned: {cleaned_count} rows")
    print(f"Skipped/Errors: {error_count} rows")
    print(f"Output saved to: {output_path}")

if __name__ == "__main__":
    # Update these paths as needed
    INPUT = "/users/theimer/meditron-4/meditron_4_merged_all_source.condensed_labels.jsonl"
    OUTPUT = "/users/theimer/meditron-4/meditron_4_cleaned.jsonl"
    
    clean_jsonl(INPUT, OUTPUT)