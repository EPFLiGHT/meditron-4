import json
import os

# Your specific model output directory
model_dir = "/capstor/store/cscs/swissai/a127/meditron/models/sft_qwen_gpt_oss_with_retries_reasoning_low_new_synthetic"
state_file = os.path.join(model_dir, "checkpoint-438", "trainer_state.json")
def draw_ascii_plot(steps, losses, width=80, height=20, title="Loss"):
    if not steps: return
    
    min_x, max_x = min(steps), max(steps)
    min_y, max_y = min(losses), max(losses)
    
    # Prevent division by zero if values are perfectly flat
    if max_x == min_x: max_x += 1e-9
    if max_y == min_y: max_y += 1e-9
    
    # Initialize an empty grid
    grid = [[" " for _ in range(width)] for _ in range(height)]
    
    # Map data points to grid coordinates
    for s, l in zip(steps, losses):
        x_idx = int((s - min_x) / (max_x - min_x) * (width - 1))
        y_idx = int((l - min_y) / (max_y - min_y) * (height - 1))
        # Invert Y so highest loss is at the top of the terminal
        grid[height - 1 - y_idx][x_idx] = "•"
        
    # Print the graph with a clean border and axis labels
    print(f"\n=== {title.upper()} ===")
    print(f"  Max: {max_y:.4f}")
    print("   " + "┌" + "─" * width + "┐")
    
    for i, row in enumerate(grid):
        # Add basic Y-axis markers
        if i == 0:
            label = f"{max_y:.2f}"
        elif i == height - 1:
            label = f"{min_y:.2f}"
        elif i == height // 2:
            label = f"{(max_y + min_y)/2:.2f}"
        else:
            label = "    "
            
        print(f"{label:>5}│{''.join(row)}│")
        
    print("   " + "└" + "─" * width + "┘")
    print(f"  Min: {min_y:.4f} {' '*(width - 32)} Steps: {min_x} ➔ {max_x}\n")

def main():
    try:
        with open(state_file, 'r') as f:
            data = json.load(f)
            
        steps, train_loss, eval_steps, eval_loss = [], [], [], []
        
        # Parse log_history
        for log in data.get("log_history", []):
            if "loss" in log and "step" in log:
                steps.append(log["step"])
                train_loss.append(log["loss"])
            if "eval_loss" in log and "step" in log:
                eval_steps.append(log["step"])
                eval_loss.append(log["eval_loss"])
                
        if steps:
            draw_ascii_plot(steps, train_loss, title="Training Loss")
        else:
            print("❌ No training loss data found in JSON.")
            
        if eval_steps:
            draw_ascii_plot(eval_steps, eval_loss, height=10, title="Evaluation Loss")
            
    except FileNotFoundError:
        print(f"❌ Error: Could not find {state_file}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()