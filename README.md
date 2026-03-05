# CyberFeed v2.0

A personal intelligence feed that aggregates cybersecurity, tech, finance, world news, and Canadian news into a single chronological stream with optional AI-powered summarization.

## What's New in v2.0

- **Mixed source timeline** — Articles from all feeds sorted chronologically, not grouped by source
- **Clean content** — HTML tags stripped, content truncated to readable previews
- **Category filtering** — Filter by Cybersecurity, Technology, Finance, Crypto, World News, or Canada
- **Region filtering** — Filter by Global or North America
- **Live search** — Type to filter articles in real-time (press `/` to focus, `Esc` to clear)
- **AI summarization** — Optional Claude API integration generates 2-sentence summaries per article
- **Parallel fetching** — All feeds fetched simultaneously (much faster than sequential)
- **Smart caching** — 15-minute cache prevents re-fetching on every page load
- **Deduplication** — Duplicate articles from overlapping feeds are automatically removed
- **Expanded sources** — 30+ feeds covering cyber, tech, finance, world news, and Canadian news
- **Force refresh** — Button to manually clear cache and re-fetch all feeds
- **Summary caching** — AI summaries saved to disk so you don't re-summarize articles you've already seen

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run without AI (works perfectly fine)
python app.py

# Run with AI summarization
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

## AI Summarization Setup

The feed works fully without AI. To enable AI summaries:

1. Get a Claude API key from https://console.anthropic.com/
2. Set it as an environment variable before running:
   - **Linux/Mac:** `export ANTHROPIC_API_KEY="sk-ant-..."`
   - **Windows CMD:** `set ANTHROPIC_API_KEY=sk-ant-...`
   - **Windows PowerShell:** `$env:ANTHROPIC_API_KEY="sk-ant-..."`
3. Run `python app.py` — you'll see "AI Summarization: ENABLED" in the console
4. Summaries appear with a green "AI" badge on each article

**Cost:** Uses Claude Haiku 4.5 (cheapest model). Summarizing 20 articles costs roughly $0.001–0.003. At daily use, expect well under $1/month. Summaries are cached to disk so you never pay to re-summarize the same article.

## Adding or Removing Feeds

Edit the `feeds` list in `app.py`. Each feed needs:

```python
{"url": "https://example.com/rss", "category": "Cybersecurity", "region": "Global"}
```

**Categories:** Cybersecurity, Technology, Finance, Crypto, World News, Canada (or add your own — the UI adapts automatically)

**Regions:** Global, North America, Europe (or add your own)

## Keyboard Shortcuts

- `/` — Focus search bar
- `Esc` — Clear search and unfocus

## File Structure

```
cyberfeed_v2/
├── app.py              # Flask backend, feed fetching, AI summarization
├── requirements.txt    # Python dependencies
├── summary_cache.json  # Auto-generated AI summary cache (don't edit)
├── static/
│   └── style.css       # Dark terminal-aesthetic stylesheet
└── templates/
    └── index.html      # Frontend template
```

## Configuration

In `app.py`, you can adjust:

- `CACHE_DURATION` — How long (seconds) before feeds are re-fetched (default: 900 = 15 min)
- `MAX_ARTICLES_PER_FEED` — Cap per feed to prevent one source from dominating (default: 15)
- `max_batch` in `summarize_articles()` — How many articles to summarize per page load (default: 20)
