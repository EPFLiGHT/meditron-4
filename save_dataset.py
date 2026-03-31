import os
from datasets import load_dataset

def main():
    # 1. Resolve the path using the environment variable
    # os.path.expandvars will replace $STORAGE_ROOT with its actual value
    output_dir = os.path.expandvars("/capstor/store/cscs/swissai/a127/meditron/datasets/")
    
    # Create the directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # 2. Load the dataset
    ds = load_dataset("epfl-llm/guidelines")
    
    # 3. Save each split (e.g., 'train') as a separate JSONL file
    for split_name, dataset_split in ds.items():
        output_path = os.path.join(output_dir, f"guidelines_epfl_llm.jsonl")
        print(f"Saving '{split_name}' split to {output_path}...")
        
        # to_json with orient="records" and lines=True creates a standard JSONL
        dataset_split.to_json(output_path, orient="records", lines=True)
        
    print("Download and conversion complete!")

if __name__ == "__main__":
    main()