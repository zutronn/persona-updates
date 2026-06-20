#!/usr/bin/env python3
"""
persona_updater.py
------------------
Weekly GitHub Actions script that:
1. Uses Claude with web_search tool to find latest persona content
2. Extracts concrete belief/view/action updates
3. Appends structured deltas to each persona's persona-updates.md
4. Writes detailed logs to logs/{PersonaName}/update-log.md
5. Updates logs/run-history.json

Uses Claude's native web_search tool instead of Serper
(Serper blocks GitHub Actions IPs on free tier).
"""

import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from anthropic import Anthropic

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).parent.parent
PERSONAS_DIR = ROOT / "personas"
LOGS_DIR     = ROOT / "logs"
RUN_HISTORY  = LOGS_DIR / "run-history.json"

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL             = "claude-sonnet-4-5"

# ── Persona Registry ──────────────────────────────────────────────────────────

PERSONAS = {
    "ElonMusk": {
        "full_name": "Elon Musk",
        "search_topics": [
            "Elon Musk AI and xAI latest news",
            "Elon Musk Bitcoin crypto statement",
            "Elon Musk investment tech announcement",
        ],
    },
    # Add more personas here:
    # "WarrenBuffett": {
    #     "full_name": "Warren Buffett",
    #     "search_topics": [
    #         "Warren Buffett latest investment view",
    #         "Berkshire Hathaway recent news",
    #     ],
    # },
}

# ── Utilities ─────────────────────────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc)

def date_str(dt=None):
    return (dt or now_utc()).strftime("%Y-%m-%d")

def datetime_str(dt=None):
    return (dt or now_utc()).strftime("%Y-%m-%d %H:%M UTC")

def get_date_context():
    dt = now_utc()
    return {"year": str(dt.year), "month": dt.strftime("%B")}

# ── Claude web_search extraction ──────────────────────────────────────────────

def extract_updates(full_name: str, search_topics: list, date_ctx: dict) -> dict:
    """
    Ask Claude to search for latest content and extract updates.
    Uses Claude's native web_search tool — no external search API needed.
    """
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    month_year = f"{date_ctx['month']} {date_ctx['year']}"
    topics_str = "\n".join(f"- {t}" for t in search_topics)

    prompt = f"""You are maintaining a live knowledge update file for: {full_name}.

Today is {date_ctx['month']} {date_ctx['year']}.

Please search for recent news and statements from {full_name} on these topics:
{topics_str}

For each search, look for content from the past 4 weeks specifically.

After searching, extract ONLY concrete verifiable updates:
- New opinions or public statements directly from {full_name}
- Investment actions or announcements
- Product/company news they personally drove
- Clearly stated new views on AI, crypto, markets, or technology

SKIP vague speculation, repetition of old positions, or unverified rumours.

For each genuine update found, output EXACTLY this format:

### [Topic] — [Date if known]
- **View/Action:** [1-2 sentence factual summary]
- **Source:** [Publication or platform name only]
- **Implication:** [1 sentence: how this updates persona reasoning on this topic]

After all update blocks output this JSON (required):
```json
{{
  "has_updates": true,
  "topics_found": ["AI", "crypto"],
  "sources_read": ["Reuters", "Bloomberg"],
  "update_count": 2
}}
```

If NO meaningful updates found, output:
NO_UPDATES
```json
{{
  "has_updates": false,
  "topics_found": [],
  "sources_read": [],
  "update_count": 0
}}
```
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # Collect all text blocks from response
    text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
    text = "\n".join(text_parts).strip()

    # Parse JSON metadata
    meta = {"has_updates": False, "topics_found": [], "sources_read": [], "update_count": 0}
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            meta = json.loads(match.group(1))
        except Exception:
            pass

    update_text = re.sub(r"```json.*?```", "", text, flags=re.DOTALL).strip()

    if update_text == "NO_UPDATES" or not meta.get("has_updates"):
        return {"has_updates": False, "updates": "", **meta}

    return {"has_updates": True, "updates": update_text, **meta}

# ── File Writers ──────────────────────────────────────────────────────────────

def append_persona_updates(persona_folder: Path, run_date: str, update_text: str):
    updates_file = persona_folder / "persona-updates.md"
    block = f"\n## Run: {run_date}\n\n{update_text}\n"
    with open(updates_file, "a", encoding="utf-8") as f:
        f.write(block)


def write_update_log(persona_key, run_date, run_time, result, topics_searched, full_name):
    log_dir  = LOGS_DIR / persona_key
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "update-log.md"

    if not log_file.exists():
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"# Update Log — {full_name}\n")
            f.write("> Auto-maintained by GitHub Actions. One entry per weekly run.\n\n---\n")

    status  = "✅ Updates found" if result["has_updates"] else "⬜ No new updates"
    topics  = ", ".join(result.get("topics_found", [])) or "none"
    sources = ", ".join(result.get("sources_read", [])) or "none"
    topics_md = "\n".join(f"- {t}" for t in topics_searched)
    updates_md = result["updates"] if result["has_updates"] else "_No meaningful new content found this run._"

    entry = f"""
## {run_date} — {status}

| Field | Value |
|---|---|
| Run time | {run_time} |
| Topics searched | {len(topics_searched)} |
| Updates extracted | {result.get("update_count", 0)} |
| Topics covered | {topics} |
| Sources cited | {sources} |

### Topics Searched
{topics_md}

### What Was Updated
{updates_md}

---
"""
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)


def update_run_history(run_date, run_time, persona_results):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    history = []
    if RUN_HISTORY.exists():
        try:
            history = json.loads(RUN_HISTORY.read_text(encoding="utf-8"))
        except Exception:
            history = []

    history.append({
        "run_date": run_date,
        "run_time": run_time,
        "personas": {
            k: {
                "has_updates":  v["has_updates"],
                "update_count": v.get("update_count", 0),
                "topics_found": v.get("topics_found", []),
                "sources_read": v.get("sources_read", []),
            }
            for k, v in persona_results.items()
        },
    })
    history = history[-52:]
    RUN_HISTORY.write_text(json.dumps(history, indent=2), encoding="utf-8")

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    start    = now_utc()
    run_date = date_str(start)
    run_time = datetime_str(start)
    date_ctx = get_date_context()

    env_filter = os.environ.get("PERSONAS_TO_UPDATE", "all").strip().lower()
    active = (
        PERSONAS if env_filter == "all"
        else {k: v for k, v in PERSONAS.items() if k.lower() in [x.strip() for x in env_filter.split(",")]}
    )

    print(f"\n{'='*60}")
    print(f"Persona Updater — {run_time}")
    print(f"Personas: {list(active.keys())}")
    print(f"{'='*60}")

    persona_results = {}

    for persona_key, cfg in active.items():
        full_name      = cfg["full_name"]
        persona_folder = PERSONAS_DIR / persona_key

        if not persona_folder.exists():
            print(f"\n[SKIP] Folder not found: {persona_folder}")
            continue

        print(f"\n[{full_name}]")
        print(f"  → Searching with Claude web_search...")

        result = extract_updates(full_name, cfg["search_topics"], date_ctx)

        status = f"✅ {result.get('update_count', 0)} updates" if result["has_updates"] else "⬜ no new updates"
        print(f"  → {status}")

        if result["has_updates"]:
            append_persona_updates(persona_folder, run_date, result["updates"])
            print(f"  → persona-updates.md updated")

        write_update_log(persona_key, run_date, run_time, result, cfg["search_topics"], full_name)
        print(f"  → log written to logs/{persona_key}/update-log.md")

        persona_results[persona_key] = result

    update_run_history(run_date, run_time, persona_results)

    print(f"\n{'='*60}")
    print(f"Done — {datetime_str()}")
    total = sum(v.get("update_count", 0) for v in persona_results.values())
    print(f"Total updates: {total}")
    for k, v in persona_results.items():
        s = f"✅ {v.get('update_count',0)} updates" if v["has_updates"] else "⬜ no changes"
        print(f"  {k}: {s}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run()
