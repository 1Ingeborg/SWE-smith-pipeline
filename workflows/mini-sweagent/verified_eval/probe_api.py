"""Sanity-check the mini LoRA OpenAI-compatible endpoint."""

import json
import sys
import urllib.request

import yaml


config_path = "/data/venvs/mini-sweagent/lib/python3.11/site-packages/minisweagent/config/benchmarks/swebench_backticks.yaml"
system_prompt = yaml.safe_load(open(config_path, encoding="utf-8"))["agent"]["system_template"]
model_name = sys.argv[1] if len(sys.argv) > 1 else "swe-mini-lora"
port = 8000 if model_name == "swe-mini-lora" else 8001


payload = {
    "model": model_name,
    "messages": [
        {
            "role": "system",
            "content": system_prompt,
        },
        {"role": "user", "content": "Inspect the repository files to start solving the issue."},
    ],
    "temperature": 0,
    "max_tokens": 128,
}
request = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer swesmith"},
)
with urllib.request.urlopen(request, timeout=120) as response:
    result = json.load(response)
print(result["choices"][0]["message"])
