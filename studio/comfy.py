"""ComfyUI API client.

Workflows are exported with "Export (API)". Nodes whose title starts with
"@" are filled in by the agents, e.g. @PROMPT, @SEED, @IMAGE, @LAST_IMAGE,
@WIDTH, @HEIGHT, @DURATION, @FPS, @VIDEO, @FILENAME (see workflows/README.md).
"""
import copy
import json
import time
import uuid
from pathlib import Path

import requests

from . import approval

# For each tag, the input names tried in order on the tagged node.
FIELDS = {
    "@PROMPT": ["text", "prompt", "positive", "value"],
    "@NEGATIVE": ["text", "prompt", "negative", "value"],
    "@SEED": ["seed", "noise_seed", "value"],
    "@IMAGE": ["image"],
    "@LAST_IMAGE": ["image"],
    "@VIDEO": ["video"],
    "@WIDTH": ["width", "value"],
    "@HEIGHT": ["height", "value"],
    "@DURATION": ["duration", "length", "value"],
    "@FPS": ["frame_rate", "fps", "value"],
    "@FILENAME": ["filename_prefix"],
}


class Comfy:
    def __init__(self, url, poll_seconds=5, timeout_minutes=60, mappings=None):
        self.mappings = mappings or {}   # workflow path -> {"@TAG": "node.field"}
        self.url = url.rstrip("/")
        self.poll = poll_seconds
        self.timeout = timeout_minutes * 60
        self.client_id = str(uuid.uuid4())

    # ---------- basics ----------
    def alive(self):
        try:
            return requests.get(self.url, timeout=10).status_code == 200
        except requests.RequestException:
            return False

    def upload(self, path):
        """Upload an image or video into ComfyUI's input folder."""
        path = Path(path)
        with open(path, "rb") as f:
            r = requests.post(f"{self.url}/upload/image",
                              files={"image": (path.name, f)},
                              data={"overwrite": "true"}, timeout=300)
        r.raise_for_status()
        info = r.json()
        sub = info.get("subfolder") or ""
        return f"{sub}/{info['name']}" if sub else info["name"]

    # ---------- run a workflow ----------
    def run(self, workflow_path, values, out_dir, label="job"):
        """Fill @tags with `values`, queue, wait, download outputs."""
        wf = json.loads(Path(workflow_path).read_text())
        wf = self._fill(wf, values, workflow_path)
        r = requests.post(f"{self.url}/prompt",
                          json={"prompt": wf, "client_id": self.client_id},
                          timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI rejected {label}: {r.text[:800]}")
        pid = r.json()["prompt_id"]
        print(f"  queued {label} ({pid[:8]}) ...", flush=True)
        approval.emit("comfy_queued", label, job=pid[:8])

        start = time.time()
        while True:
            h = requests.get(f"{self.url}/history/{pid}", timeout=30).json()
            if pid in h:
                entry = h[pid]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"{label} failed: "
                                       f"{json.dumps(status)[:800]}")
                if status.get("completed", True):
                    files = self._download(entry.get("outputs", {}), out_dir)
                    approval.emit("comfy_done", label, job=pid[:8],
                                  seconds=round(time.time() - start, 1),
                                  files=len(files))
                    return files
            if time.time() - start > self.timeout:
                raise TimeoutError(f"{label} timed out")
            time.sleep(self.poll)

    def _fill(self, wf, values, name):
        wf = copy.deepcopy(wf)
        found = set()
        mapping = self.mappings.get(str(name), {})
        for tag, targets in mapping.items():          # from config.yaml
            if tag not in values:
                continue
            for target in ([targets] if isinstance(targets, str) else targets):
                node_id, field = target.rsplit(".", 1)
                if node_id in wf:
                    wf[node_id].setdefault("inputs", {})[field] = values[tag]
                    found.add(tag)
        for node in wf.values():
            title = node.get("_meta", {}).get("title", "")
            tags = [t for t in title.split() if t.startswith("@")]
            inputs = node.get("inputs", {})
            for tag in tags:            # e.g. "@WIDTH @HEIGHT"
                if tag not in values or tag in mapping:
                    continue
                for field in FIELDS.get(tag, [tag[1:].lower()]):
                    # skip inputs wired to another node (lists)
                    if field in inputs and not isinstance(inputs[field], list):
                        inputs[field] = values[tag]
                        found.add(tag)
                        break
        missing = [t for t in values if t not in found]
        if missing:
            print(f"  note: {Path(name).name} has no node titled "
                  f"{missing} (left at workflow defaults)")
        return wf

    def _download(self, outputs, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for node_out in outputs.values():
            for key, items in node_out.items():
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict) or "filename" not in it:
                        continue
                    if it.get("type") == "temp":
                        continue  # previews
                    r = requests.get(f"{self.url}/view", params={
                        "filename": it["filename"],
                        "subfolder": it.get("subfolder", ""),
                        "type": it.get("type", "output")}, timeout=600)
                    r.raise_for_status()
                    dest = out_dir / it["filename"]
                    dest.write_bytes(r.content)
                    files.append(dest)
        return files
