#!/usr/bin/env python3
"""
persona_updater.py
------------------
Weekly GitHub Actions script that:
1. Loads a small "known facts index" (deduplicated, one line per fact, 90-day window)
2. Passes that index to Claude so it skips already-known facts and only
   reports genuinely NEW developments — saves tokens by avoiding repeat output
3. Appends full update blocks to persona-updates.md (the readable file)
4. Appends short index lines to known-facts-index.md (the dedup memory)
5. Auto-prunes the index to entries within the last 90 days, keeping it small
6. Writes detailed logs to logs/{PersonaName}/update-log.md
7. Updates logs/run-history.json

Uses Claude's native web_search tool — no external search API needed.
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from anthropic import Anthropic

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).parent.parent
PERSONAS_DIR = ROOT / "personas"
LOGS_DIR     = ROOT / "logs"
RUN_HISTORY  = LOGS_DIR / "run-history.json"
INDEX_NAME   = "known-facts-index.md"
QUOTES_NAME  = "recent-quotes.md"
SKILL_NAME   = "SKILL.md"
INDEX_RETENTION_DAYS = 90
QUOTES_MAX_COUNT = 12

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
    # Add more personas here — same pattern.
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

# ── Known Facts Index (dedup memory) ──────────────────────────────────────────

def load_index_lines(persona_folder: Path) -> list[str]:
    idx_file = persona_folder / INDEX_NAME
    if not idx_file.exists():
        return []
    lines = []
    for line in idx_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("- ") and re.match(r"-\s*\d{4}-\d{2}-\d{2}", line):
            lines.append(line)
    return lines

def prune_lines(lines: list[str]) -> list[str]:
    cutoff = now_utc() - timedelta(days=INDEX_RETENTION_DAYS)
    kept = []
    for line in lines:
        m = re.match(r"-\s*(\d{4}-\d{2}-\d{2})", line)
        if m:
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if d < cutoff:
                    continue
            except Exception:
                pass
        kept.append(line)
    return kept

def save_index(persona_folder: Path, lines: list[str]):
    idx_file = persona_folder / INDEX_NAME
    header = (
        "# Known Facts Index (internal — used for dedup checks, NOT shown to the persona)\n"
        "> Auto-pruned to last 90 days. One line per distinct fact. Do not edit manually.\n\n"
    )
    idx_file.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")

# ── Recent Quotes (feeds the Live Knowledge block in SKILL.md) ───────────────

def load_quote_lines(persona_folder: Path) -> list[str]:
    f = persona_folder / QUOTES_NAME
    if not f.exists():
        return []
    lines = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("- ") and re.match(r"-\s*\d{4}-\d{2}-\d{2}", line):
            lines.append(line)
    return lines

def cap_lines_by_date(lines: list[str], max_count: int) -> list[str]:
    def extract_date(line):
        m = re.match(r"-\s*(\d{4}-\d{2}-\d{2})", line)
        return m.group(1) if m else "0000-00-00"
    return sorted(lines, key=extract_date, reverse=True)[:max_count]

def format_quote_storage(date: str, quote: str, source: str, context: str) -> str:
    quote   = quote.replace("|", "-").strip()
    source  = source.replace("|", "-").strip()
    context = context.replace("|", "-").strip()
    return f"- {date} | {quote} | {source} | {context}"

def parse_quote_storage(line: str):
    body = line[1:].strip() if line.startswith("-") else line
    parts = [p.strip() for p in body.split("|")]
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]  # date, quote, source, context

def save_quote_lines(persona_folder: Path, lines: list[str]):
    f = persona_folder / QUOTES_NAME
    header = (
        "# Recent Quotes (internal — feeds the Live Knowledge block in SKILL.md)\n"
        f"> Auto-pruned to last {QUOTES_MAX_COUNT} quotes. Do not edit manually.\n\n"
    )
    f.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")

# ── SKILL.md Live Knowledge block injector ────────────────────────────────────

def render_live_block(index_lines: list[str], quote_lines: list[str], run_date: str) -> str:
    if quote_lines:
        quotes_md = "\n".join(
            f'> "{q}" — {s}, {d}' + (f" _({c})_" if c else "")
            for d, q, s, c in (parse_quote_storage(l) for l in quote_lines)
        )
    else:
        quotes_md = "_Nothing quote-worthy tracked recently._"

    facts_md = "\n".join(index_lines) if index_lines else "_No facts tracked yet._"

    return (
        "<!-- AUTO_LIVE_BLOCK_START -->\n"
        f"## 🔴 Live Knowledge (auto-updated weekly — last refresh: {run_date})\n\n"
        "### What He Actually Said Recently\n"
        f"{quotes_md}\n\n"
        "### Known Current Facts (rolling memory)\n"
        f"{facts_md}\n"
        "<!-- AUTO_LIVE_BLOCK_END -->"
    )

def update_skill_md(persona_folder: Path, block_content: str):
    skill_file = persona_folder / SKILL_NAME
    if not skill_file.exists():
        return False
    text = skill_file.read_text(encoding="utf-8")
    if "<!-- AUTO_LIVE_BLOCK_START -->" in text:
        text = re.sub(
            r"<!-- AUTO_LIVE_BLOCK_START -->.*?<!-- AUTO_LIVE_BLOCK_END -->",
            block_content,
            text,
            flags=re.DOTALL,
        )
    else:
        marker = "## Identity"
        if marker in text:
            text = text.replace(marker, block_content + "\n\n" + marker, 1)
        else:
            text = text + "\n\n" + block_content + "\n"
    skill_file.write_text(text, encoding="utf-8")
    return True

# ── Claude web_search extraction ──────────────────────────────────────────────

def extract_updates(full_name: str, search_topics: list, date_ctx: dict, known_facts: list[str]) -> dict:
    """
    Ask Claude to search for latest content, SKIP anything already in known_facts,
    and extract only genuinely new updates. Returns updates + short index lines.
    """
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    topics_str = "\n".join(f"- {t}" for t in search_topics)
    known_str  = "\n".join(known_facts) if known_facts else "(none yet — first run)"

    prompt = f"""You are maintaining a live knowledge update file for: {full_name}.

Today is {date_ctx['month']} {date_ctx['year']}.

ALREADY KNOWN FACTS (do not re-report these — only report something NEW, or a
material update to one of these, like a number changing or a deal closing):
{known_str}

Search for recent news and statements from {full_name} on:
{topics_str}

Focus on the past 1-2 weeks specifically, since older ground is already covered above.

For each genuinely NEW update found (not already in the known facts list above),
output EXACTLY this format:

### [Topic] — [Date if known]
- **View/Action:** [1-2 sentence factual summary]
- **Source:** [Publication or platform name only]
- **Implication:** [1 sentence: how this updates persona reasoning on this topic]

Also pull 1-3 of the most notable things {full_name} ACTUALLY SAID this week —
direct short quotes (under 15 words each, exact words, in quotation marks),
not paraphrases. Pick the ones that best reveal his current thinking or stance.
If he said nothing notable this week, just leave the top_quotes list empty in the
JSON below — do NOT write any sentence about this in your main response text.

After all update blocks, output this JSON (required):
```json
{{
  "has_updates": true,
  "topics_found": ["AI", "crypto"],
  "sources_read": ["Reuters", "Bloomberg"],
  "update_count": 2,
  "index_lines": [
    "- 2026-06-21 | Short topic: one-line fact summary under 15 words",
    "- 2026-06-21 | Another short fact"
  ],
  "top_quotes": [
    {{"quote": "exact short quote under 15 words", "context": "5-8 words on what he was talking about", "source": "Publication", "date": "2026-06-18"}}
  ]
}}
```
Each index_line must start with "- YYYY-MM-DD | " and be ONE short line per new fact —
these are for internal dedup tracking only, keep them terse.

If NOTHING new is found (everything matches known facts), output:
NO_UPDATES
```json
{{
  "has_updates": false,
  "topics_found": [],
  "sources_read": [],
  "update_count": 0,
  "index_lines": [],
  "top_quotes": []
}}
```
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
    text = "\n".join(text_parts).strip()

    meta = {"has_updates": False, "topics_found": [], "sources_read": [], "update_count": 0, "index_lines": [], "top_quotes": []}
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


def write_update_log(persona_key, run_date, run_time, result, topics_searched, full_name, known_count):
    log_dir  = LOGS_DIR / persona_key
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "update-log.md"

    if not log_file.exists():
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"# Update Log — {full_name}\n")
            f.write("> Auto-maintained by GitHub Actions. One entry per weekly run.\n\n---\n")

    status  = "✅ New updates found" if result["has_updates"] else "⬜ Nothing new (all known)"
    topics  = ", ".join(result.get("topics_found", [])) or "none"
    sources = ", ".join(result.get("sources_read", [])) or "none"
    topics_md = "\n".join(f"- {t}" for t in topics_searched)
    updates_md = result["updates"] if result["has_updates"] else "_Everything found already matched the known-facts index — no duplicate written._"

    quotes = result.get("top_quotes", [])
    if quotes:
        quotes_md = "\n".join(
            f'> "{q.get("quote","")}" — {q.get("source","?")}, {q.get("date","?")} _({q.get("context","")})_'
            for q in quotes
        )
    else:
        quotes_md = "_Nothing quote-worthy found this week._"

    entry = f"""
## {run_date} — {status}

### 💬 What He Actually Said This Week
{quotes_md}

| Field | Value |
|---|---|
| Run time | {run_time} |
| Known facts checked against | {known_count} |
| Topics searched | {len(topics_searched)} |
| New updates extracted | {result.get("update_count", 0)} |
| Topics covered | {topics} |
| Sources cited | {sources} |

### Topics Searched
{topics_md}

### What Was New This Run
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

        # Load + prune known facts index (this is the dedup memory)
        existing_lines = load_index_lines(persona_folder)
        existing_lines = prune_lines(existing_lines)
        print(f"  → {len(existing_lines)} known facts loaded (after 90-day prune)")

        print(f"  → Searching with Claude web_search (skipping known facts)...")
        result = extract_updates(full_name, cfg["search_topics"], date_ctx, existing_lines)

        status = f"✅ {result.get('update_count', 0)} NEW updates" if result["has_updates"] else "⬜ nothing new"
        print(f"  → {status}")

        if result["has_updates"]:
            append_persona_updates(persona_folder, run_date, result["updates"])
            print(f"  → persona-updates.md updated")

            new_index_lines = [l.strip() for l in result.get("index_lines", []) if l.strip()]
            combined = prune_lines(existing_lines + new_index_lines)
            save_index(persona_folder, combined)
            print(f"  → known-facts-index.md updated ({len(combined)} facts tracked)")
        else:
            combined = existing_lines  # unchanged, still used below for SKILL.md render

        # Quotes: update regardless of has_updates (a run can find quotes with no "new facts")
        existing_quotes = load_quote_lines(persona_folder)
        new_quote_lines = [
            format_quote_storage(q.get("date", run_date), q.get("quote", ""), q.get("source", "?"), q.get("context", ""))
            for q in result.get("top_quotes", [])
            if q.get("quote", "").strip()
        ]
        combined_quotes = cap_lines_by_date(prune_lines(existing_quotes + new_quote_lines), QUOTES_MAX_COUNT)
        if new_quote_lines:
            save_quote_lines(persona_folder, combined_quotes)
            print(f"  → recent-quotes.md updated ({len(combined_quotes)} quotes tracked)")

        # Always refresh the Live Knowledge block in SKILL.md so a single fetch stays current
        live_block = render_live_block(combined, combined_quotes, run_date)
        if update_skill_md(persona_folder, live_block):
            print(f"  → SKILL.md Live Knowledge block refreshed")

        write_update_log(persona_key, run_date, run_time, result, cfg["search_topics"], full_name, len(existing_lines))
        print(f"  → log written to logs/{persona_key}/update-log.md")

        persona_results[persona_key] = result

    update_run_history(run_date, run_time, persona_results)

    print(f"\n{'='*60}")
    print(f"Done — {datetime_str()}")
    total = sum(v.get("update_count", 0) for v in persona_results.values())
    print(f"Total NEW updates: {total}")
    for k, v in persona_results.items():
        s = f"✅ {v.get('update_count',0)} new" if v["has_updates"] else "⬜ no changes"
        print(f"  {k}: {s}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run()
