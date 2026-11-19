"""Optional structured intent classification using only allowlisted signals."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_MODEL = "gpt-4.1-mini-2025-04-14"
API_URL = "https://api.openai.com/v1/chat/completions"


@dataclass(frozen=True)
class RouteModel:
    enabled: bool = False
    model: str = DEFAULT_MODEL
    key: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls) -> RouteModel:
        return cls(os.getenv("AGENT_LLM") == "1", os.getenv("LLM_MODEL", DEFAULT_MODEL),
                   os.getenv("OPENAI_API_KEY", ""))

    def choose(self, options: list[str], signals: dict[str, list[str]], fallback: str) -> tuple[str, int, str | None]:
        if not self.key:
            return fallback, 0, "API_KEY_MISSING"
        schema = {"type": "object", "properties": {"task": {"type": "string", "enum": options}},
                  "required": ["task"], "additionalProperties": False}
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Classify the supplied intent hints into exactly one permitted task. "
                 "audit = personal credit check; rules = published requirements; courses = search timetable; "
                 "plan = propose timetable; validate = check supplied sections."},
                {"role": "user", "content": json.dumps(signals, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "agent_route", "schema": schema, "strict": True}},
            "max_completion_tokens": 80,
        }
        request = Request(API_URL, data=json.dumps(request_body).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        for attempt in (1, 2):
            try:
                with urlopen(request, timeout=8) as response:
                    body = json.loads(response.read(32_768))
                route = json.loads(body["choices"][0]["message"]["content"])
                if set(route) == {"task"} and route["task"] in options:
                    return route["task"], attempt, None
                return fallback, attempt, "MODEL_ROUTE_INVALID"
            except HTTPError as error:
                if error.code not in {408, 409, 429} and not 500 <= error.code <= 599:
                    return fallback, attempt, f"HTTP_{error.code}"
                failure = f"HTTP_{error.code}"
            except (TimeoutError, URLError):
                failure = "MODEL_NETWORK"
            except (KeyError, IndexError, TypeError, ValueError):
                return fallback, attempt, "MODEL_RESPONSE_INVALID"
        return fallback, 2, failure
