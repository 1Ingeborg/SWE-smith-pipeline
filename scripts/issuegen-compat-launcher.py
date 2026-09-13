#!/usr/bin/env python3
"""Run the unmodified issue generation CLI with custom-model cost metadata."""

import os
import runpy

import litellm


model = os.environ.get("SWESMITH_ISSUEGEN_MODEL")
if model and model not in litellm.model_cost:
    litellm.model_cost[model] = {
        "max_tokens": 65_536,
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 65_536,
        "input_cost_per_token": 0.0,
        "output_cost_per_token": 0.0,
        "litellm_provider": model.split("/", 1)[0] if "/" in model else "openai",
        "mode": "chat",
    }

runpy.run_module("swesmith.issue_gen.generate", run_name="__main__")
