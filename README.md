# persona-updates

Auto-updating persona knowledge base for AI mentor/coach skills.

A GitHub Actions cron job runs **every Sunday at 08:00 UTC** to:
1. Search for the latest content from each persona (interviews, X posts, announcements)
2. Use Claude API to extract concrete belief/view updates
3. Append structured deltas to each persona's `persona-updates.md`
4. Write detailed logs of what was searched, found, and written

---

## Project Structure

```
persona-updates/
├── .github/
│   └── workflows/
│       └── persona-updater.yml       ← Weekly cron scheduler
├── personas/
│   └── ElonMusk/
│       ├── SKILL.md                  ← Base persona card (stable)
│       └── persona-updates.md        ← Auto-updated weekly
├── logs/
│   ├── ElonMusk/
│   │   └── update-log.md            ← Human-readable weekly log
│   └── run-history.json             ← Full machine-readable run history
└── scripts/
    └── persona_updater.py           ← The updater script
```

---

## Adding a New Persona

1. Create folder: `personas/YourPersonaName/`
2. Add `SKILL.md` (base persona card) and `persona-updates.md` (from template)
3. Add entry to `PERSONAS` dict in `scripts/persona_updater.py`
4. Create `logs/YourPersonaName/update-log.md` with header
5. Push — next cron run picks it up automatically

---

## Secrets Required (Settings → Secrets → Actions)

| Secret | Source |
|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `SERPER_API_KEY` | https://serper.dev (free: 2,500 searches/month) |

---

## Using with Claude

In any SKILL.md, Claude fetches the live update file:
```
web_fetch: https://raw.githubusercontent.com/zutronn/persona-updates/main/personas/ElonMusk/persona-updates.md
```

Entries dated within 90 days override the base SKILL.md persona card.

---

## Cost

| Service | Monthly cost |
|---|---|
| GitHub Actions | Free |
| Serper API | Free (2,500 queries/month) |
| Anthropic API | ~$0.05–0.10/month |

**Total: essentially free.**
