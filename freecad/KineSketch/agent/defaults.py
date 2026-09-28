# SPDX-License-Identifier: LGPL-2.1-or-later

"""Connection defaults and migration of the retired default pair."""

from collections.abc import Mapping

DEFAULT_ENDPOINT = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "modelscope.cn/unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"


def connection_defaults(
    saved_endpoint: str | None, saved_model: str | None,
    environment: Mapping[str, str],
) -> tuple[str, str]:
    endpoint = environment.get(
        "KINESKETCH_AGENT_ENDPOINT", environment.get("KINESKETCH_ENDPOINT", DEFAULT_ENDPOINT),
    )
    model = environment.get(
        "KINESKETCH_AGENT_ID", environment.get("KINESKETCH_MODEL", DEFAULT_MODEL),
    )
    if (saved_endpoint in (None, "http://127.0.0.1:18789/v1")
            and saved_model in (None, "openclaw/default")):
        return endpoint, model
    return (saved_endpoint if saved_endpoint is not None else endpoint,
            saved_model if saved_model is not None else model)
