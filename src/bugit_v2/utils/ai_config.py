"""
Reads the AI (OpenAI-compatible API) configuration used by the AI Log
Collector (see dut_utils/ai_log_collector.py).

Configuration is provided via environment variables, which are populated from
snap config by snap/hooks/configure + snap/local/scripts/env_wrapper.sh:

    sudo snap set bugit ai-api-key=<key> ai-base-url=<url> ai-model=<model>

For the pipx/source install, the same env vars (AI_API_KEY, AI_BASE_URL,
AI_MODEL) can just be set directly, mirroring how JIRA_SERVER is handled in
jira_submitter.py.
"""

import os
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class AiConfig:
    api_key: str
    base_url: str
    model: str


def get_ai_config() -> AiConfig | None:
    """Return the configured AI settings, or None if any of them is unset.

    The AI Log Collector should be hidden entirely when this returns None.
    """
    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_BASE_URL")
    model = os.getenv("AI_MODEL")

    if not api_key or not base_url or not model:
        return None

    return AiConfig(api_key=api_key, base_url=base_url, model=model)
