"""Thin wrapper around the Claude API that always returns parsed JSON."""
import base64
import json
import re
from pathlib import Path

import time

import anthropic

from . import approval


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
        sys_text = (system + "\n\nReply with ONLY valid JSON, no prose, no "
                    "markdown fences. Escape every double quote inside a "
                    "string as \\\" (or use single quotes in the text).")
        messages = [{"role": "user", "content": content}]
        for attempt in range(3):
            text, stop = self._call(sys_text, messages, kwargs, images)
            if stop == "max_tokens":
                raise RuntimeError(
                    f"Claude's reply was cut off at max_tokens="
                    f"{self.max_tokens}. Raise llm: max_tokens in config.yaml "
                    "(e.g. 20000) and run 'resume'.")
            try:
                return parse_json(text)
            except ValueError as e:
                if attempt == 2:
                    raise
                print(f"  [llm] reply was not valid JSON ({e}); asking Claude "
                      "to fix it ...", flush=True)
                approval.emit("llm_retry", str(e)[:200])
                messages = [{"role": "user", "content": content},
                            {"role": "assistant", "content": text},
                            {"role": "user", "content":
                             f"That reply is not valid JSON: {e}. Return the "
                             "same content again as valid JSON only. Escape "
                             "double quotes inside strings."}]
                kwargs = {}                     # no web search on the repair

    def _call(self, system, messages, kwargs, images):
        t0 = time.time()
        # Streaming: no 10-minute limit, so long replies (big scripts) work
        with self.client.messages.stream(
                model=self.model, max_tokens=self.max_tokens, system=system,
                messages=messages, **kwargs) as stream:
            resp = stream.get_final_message()
        text = "".join(b.text for b in resp.content if b.type == "text")
        u = getattr(resp, "usage", None)
        approval.emit("llm_call", self.model, model=self.model,
                      tokens_in=getattr(u, "input_tokens", 0) or 0,
                      tokens_out=getattr(u, "output_tokens", 0) or 0,
                      seconds=round(time.time() - t0, 1), web=bool(kwargs),
                      images=len(images or []))
        return text, getattr(resp, "stop_reason", None)


def parse_json(text):
    text = re.sub(r"```(?:json)?", "", text).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError(f"No JSON in model reply:\n{text[:500]}")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]")) + 1
    return json.loads(text[start:end])
