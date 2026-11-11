"""Agent 1 - Trend Identifier: finds current trends that fit the channel."""
from ..approval import gate
from .base import StageResult, history

SYSTEM = """You are the Trend Identifier for a faceless AI video channel.
Use web search to find what is trending NOW on Instagram Reels, YouTube
Shorts and YouTube for the given niche and region: formats, topics,
hooks, festivals and upcoming dates, popular audio styles. Prefer
evidence from the last 30 days. Judge each idea's fit for an AI pipeline
(photoreal images + short AI video clips), originality, and risks
(copyright, sensitive topics, platform policies)."""

FORMAT = """Return JSON:
{"trends": [{"title": str, "summary": str, "why_now": str,
  "evidence": [str], "format": "reel|short|long", "hook_idea": str,
  "audience": str, "fit_score": 1-10, "effort": "low|medium|high",
  "risks": [str]}]}
Give 6-8 ideas, best first."""


def run(ctx, feedback=None, items=None):
    p, brief = ctx.project, ctx.project.state["inputs"]["brief"]
    while True:
        prompt = (f"Channel brief:\n{brief}\n\n{history(ctx, 'trend')}\n\n"
                  f"{FORMAT}" + (f"\n\nFeedback to apply: {feedback}"
                                 if feedback else ""))
        print("\n[Trend agent] researching trends (web search) ...")
        p.set_status("trend", "working", "searching the web for trends")
        data = ctx.llm.ask_json(SYSTEM, prompt, web_search=True)
        path = p.write_json("1_trends.json", data)
        n = len(data["trends"])
        lines = "\n".join(
            f"#{i} [{t['fit_score']}/10, {t['effort']}] {t['title']}\n"
            f"     {t['summary']}\n     why now: {t['why_now']}"
            + (f"\n     risks: {'; '.join(t['risks'])}" if t.get("risks")
               else "") for i, t in enumerate(data["trends"], 1))
        while True:
            action, val = gate("Trend Identifier - choose", lines, path,
                               f"Type 1-{n} to choose a trend.")
            if action == "pick" and val.isdigit() and 1 <= int(val) <= n:
                chosen = data["trends"][int(val) - 1]
                p.write_json("1_trend_chosen.json", chosen)
                return StageResult(
                    f"Chosen trend: {chosen['title']}\n{chosen['summary']}",
                    path, {"brief": brief, "chosen": chosen,
                           "alternatives": [t["title"]
                                            for t in data["trends"]]})
            if action == "revise":
                p.log("trend", "human", val)
                feedback = val
                break
            if action == "quit":
                raise SystemExit("Saved. Resume later.")
            print(f"Type a number 1-{n}, or r to revise.")
