
import os
from config.settings import settings
from agent_langgraph_simple import load_system_prompt

# MOCK manual para testar comportamento
# Mas o ideal é testar com o valor REAL do settings
print(f"Current setting path: {settings.agent_prompt_path}")

try:
    prompt = load_system_prompt()
    print(f"Prompt loaded! Size: {len(prompt)} chars")
    print("First line:", prompt.split('\n')[0])
    
    if "GROK OPTIMIZED" in prompt:
        print("✅ SUCCESS: Grok prompt loaded!")
    else:
        print("⚠️ WARNING: Default/Other prompt loaded.")
        
except Exception as e:
    print(f"❌ ERROR: {e}")
