# meditron_rewards.py
import re

def accuracy_reward(prompts, completions, label_letter, **kwargs):
    """Reward function that checks if the model's extracted answer matches the ground truth."""
    rewards = []
    # Your specific regex
    pattern = r"(?i)Answer[^A-E]*(?::)*[^A-E]*(?:boxed)*[^A-E]*([A-E])\b"
    
    for completion, truth in zip(completions, label_letter):
        # TRL passes completions as strings or list of message dicts depending on the base model config
        content = completion[0]["content"] if isinstance(completion, list) else completion
        
        matches = re.findall(pattern, content)
        # Check if the last matched letter matches the ground truth
        if matches and matches[-1].upper() == truth.upper():
            rewards.append(1.0) # Full reward for correct answer
        else:
            rewards.append(0.0) # Zero reward for wrong answer or formatting failure
            
    return rewards

def format_reward(prompts, completions, **kwargs):
    """Reward function that enforces <think> tags."""
    rewards = []
    for completion in completions:
        content = completion[0]["content"] if isinstance(completion, list) else completion
        if "<think>" in content and "</think>" in content:
            rewards.append(1.0)
        else:
            rewards.append(0.0)
    return rewards