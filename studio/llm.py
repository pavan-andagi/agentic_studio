"""Thin wrapper around the Claude API that always returns parsed JSON."""
import base64
import json
import re
from pathlib import Path

import anthropic


class LLM:
    def __init__(self, model, max_tokens=12000, web_search_max_uses=8):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model
        self.max_tokens = max_tokens
        self.web_search_max_uses = web_search_max_uses

    def ask_json(self, system, prompt, web_search=False, images=None):
        """Send a prompt (optionally with images / web search), return JSON."""
        content = []
        for img in images or []:
            data = base64.b64encode(Path(img).read_bytes()).decode()
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": data}})
        content.append({"type": "text", "text": prompt})

        kwargs = {}
        if web_search:
            kwargs["tools"] = [{"type": "web_search_20250305",
                                "name": "web_search",
                                "max_uses": self.web_search_max_uses}]
        resp = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            system=system + "\n\nReply with ONLY valid JSON, no prose, "
                            "no markdown fences.",
            messages=[{"role": "user", "content": content}], **kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return parse_json(text)


def parse_json(text):
    text = re.sub(r"```(?:json)?", "", text).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError(f"No JSON in model reply:\n{text[:500]}")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]")) + 1
    return json.loads(text[start:end])
