from flask import Flask, render_template, request, jsonify
import feedparser
import requests
import re
import os
import json
import hashlib
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

# Set your Claude API key as an environment variable to enable AI summaries:
#   export ANTHROPIC_API_KEY="sk-ant-..."
# Leave unset to run without AI summarization (feed works fine without it)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_ENABLED = bool(ANTHROPIC_API_KEY)

# Cache settings (seconds) — prevents re-fetching on every page load
CACHE_DURATION = 900  # 15 minutes
_cache = {"articles": [], "timestamp": 0}

# Maximum articles per feed (prevents any single source from dominating)
MAX_ARTICLES_PER_FEED = 15

# ============================================================
# IMPORTANCE SCORING
# ============================================================

# Source authority tiers (higher = more authoritative)
SOURCE_TIERS = {
    # Tier 3 — Premier sources
    "Krebs on Security": 3,
    "BleepingComputer": 3,
    "The Record by Recorded Future": 3,
    "The Record from Recorded Future News": 3,
    "Dark Reading": 3,
    "The Hacker News": 3,
    "Schneier on Security": 3,
    "BBC News": 3,
    "BBC News - World": 3,
    "Al Jazeera English": 3,
    "NYT > World": 3,
    "Financial Times": 3,
    "Ars Technica": 3,
    "WIRED": 3,
    "CNBC": 3,
    
    # Tier 2 — Strong sources
    "CyberScoop": 2,
    "Infosecurity Magazine": 2,
    "PortSwigger": 2,
    "Security Magazine": 2,
    "TechCrunch": 2,
    "The Verge": 2,
    "MarketWatch": 2,
    "Defense News": 2,
    "CBC News": 2,
    "CoinDesk": 2,
    "Cointelegraph": 2,
    "South China Morning Post": 2,
    
    # Tier 1 — Everything else
}

# Keywords that indicate high-importance articles
IMPORTANCE_KEYWORDS = {
    # Critical cybersecurity events (weight 5)
    5: [
        "zero-day", "0-day", "zero day", "critical vulnerability", "actively exploited",
        "ransomware attack", "data breach", "mass exploitation", "supply chain attack",
        "nation-state", "APT", "critical infrastructure", "emergency patch",
        "remote code execution", "RCE",
    ],
    # High importance (weight 3)
    3: [
        "breach", "exploit", "ransomware", "malware campaign", "CVE-",
        "patch tuesday", "sanctions", "indictment", "arrested",
        "market crash", "rate decision", "fed ", "bank of canada",
        "war ", "invasion", "missile", "ceasefire",
        "AI regulation", "executive order",
    ],
    # Moderate importance (weight 1)
    1: [
        "vulnerability", "phishing", "DDoS", "botnet",
        "earnings", "IPO", "acquisition", "merger",
        "election", "summit", "treaty",
    ],
}


def calculate_importance(article):
    """Score an article based on source authority and content keywords."""
    score = 0.0
    
    # Source tier score (0-3)
    source_name = article.get("source", "")
    source_score = SOURCE_TIERS.get(source_name, 1)
    score += source_score * 2  # Weight source authority
    
    # Keyword importance score
    title_lower = article.get("title", "").lower()
    content_lower = article.get("content", "").lower()
    text = title_lower + " " + content_lower
    
    for weight, keywords in IMPORTANCE_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text:
                # Title matches are worth more than content matches
                if keyword.lower() in title_lower:
                    score += weight * 2
                else:
                    score += weight
                break  # Only count one match per weight tier
    
    # Recency boost for "hot" sorting — articles from last 6h get a boost
    try:
        age_hours = (datetime.now(timezone.utc) - article["published_dt"]).total_seconds() / 3600
        if age_hours < 1:
            score += 5
        elif age_hours < 3:
            score += 3
        elif age_hours < 6:
            score += 2
        elif age_hours < 12:
            score += 1
    except Exception:
        pass
    
    return score

# ============================================================
# RSS FEED SOURCES
# ============================================================

feeds = [
    # --- Cybersecurity ---
    {"url": "https://www.bleepingcomputer.com/feed/", "category": "Cybersecurity", "region": "Global"},
    {"url": "https://krebsonsecurity.com/feed/", "category": "Cybersecurity", "region": "North America"},
    {"url": "https://therecord.media/feed/", "category": "Cybersecurity", "region": "Global"},
    {"url": "https://www.cyberscoop.com/feed/", "category": "Cybersecurity", "region": "North America"},
    {"url": "https://www.infosecurity-magazine.com/rss/news/", "category": "Cybersecurity", "region": "Europe"},
    {"url": "https://portswigger.net/daily-swig/rss", "category": "Cybersecurity", "region": "Global"},
    {"url": "https://www.securitymagazine.com/rss", "category": "Cybersecurity", "region": "North America"},
    {"url": "https://feeds.feedburner.com/TheHackersNews", "category": "Cybersecurity", "region": "Global"},
    {"url": "https://www.darkreading.com/rss.xml", "category": "Cybersecurity", "region": "Global"},
    {"url": "https://www.schneier.com/feed/", "category": "Cybersecurity", "region": "North America"},

    # --- Technology ---
    {"url": "https://arstechnica.com/feed/", "category": "Technology", "region": "North America"},
    {"url": "https://www.theverge.com/rss/index.xml", "category": "Technology", "region": "North America"},
    {"url": "https://techcrunch.com/feed/", "category": "Technology", "region": "North America"},
    {"url": "https://www.wired.com/feed/rss", "category": "Technology", "region": "North America"},

    # --- Finance & Economics ---
    {"url": "https://www.investing.com/rss/news_25.rss", "category": "Finance", "region": "Global"},
    {"url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "Finance", "region": "North America"},
    {"url": "https://www.ft.com/?format=rss", "category": "Finance", "region": "Europe"},
    {"url": "https://finance.yahoo.com/news/rssindex", "category": "Finance", "region": "Global"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "category": "Finance", "region": "North America"},

    # --- Crypto ---
    {"url": "https://cointelegraph.com/rss", "category": "Crypto", "region": "Global"},
    {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "category": "Crypto", "region": "Global"},

    # --- World News & Geopolitics ---
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "category": "World News", "region": "Global"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml", "category": "World News", "region": "Global"},
    {"url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "category": "World News", "region": "Global"},
    {"url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "category": "World News", "region": "North America"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "category": "World News", "region": "Global"},

    # --- Regional News ---
    {"url": "https://feeds.bbci.co.uk/news/world/africa/rss.xml", "category": "World News", "region": "Africa"},
    {"url": "https://feeds.bbci.co.uk/news/world/asia/rss.xml", "category": "World News", "region": "Asia"},
    {"url": "https://feeds.bbci.co.uk/news/world/europe/rss.xml", "category": "World News", "region": "Europe"},
    {"url": "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml", "category": "World News", "region": "Central/South America"},
    {"url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "category": "World News", "region": "Asia"},
    {"url": "https://feeds.bbci.co.uk/news/world/australia/rss.xml", "category": "World News", "region": "Australia"},
    {"url": "https://www.scmp.com/rss/91/feed", "category": "World News", "region": "Asia"},
    {"url": "https://japantoday.com/feed", "category": "World News", "region": "Asia"},

    # --- Canada ---
    {"url": "https://www.cbc.ca/webfeed/rss/rss-topstories", "category": "Canada", "region": "North America"},
    {"url": "https://www.cbc.ca/webfeed/rss/rss-technology", "category": "Canada", "region": "North America"},
    {"url": "https://globalnews.ca/feed/", "category": "Canada", "region": "North America"},
]

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def clean_html(raw_html):
    """Strip HTML tags and decode entities from RSS content."""
    if not raw_html:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", raw_html)
    # Decode HTML entities
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Truncate to reasonable preview length
    if len(text) > 350:
        text = text[:347] + "..."
    return text


def parse_date(date_str):
    """Parse various RSS date formats into a timezone-aware datetime object."""
    if not date_str or date_str == "Unknown":
        return datetime.now(timezone.utc)
    
    dt = None
    
    try:
        dt = parsedate_to_datetime(date_str)
    except Exception:
        pass
    
    if dt is None:
        # Try common alternative formats
        formats = [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue
    
    if dt is None:
        return datetime.now(timezone.utc)
    
    # Ensure timezone-aware (some feeds return naive datetimes)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    return dt


def relative_time(dt):
    """Convert a datetime to a human-readable relative time string."""
    now = datetime.now(timezone.utc)
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = now - dt
    except Exception:
        return "recently"
    
    seconds = int(diff.total_seconds())
    if seconds < 0:
        return "just now"
    elif seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins}m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days}d ago"
    else:
        return dt.strftime("%b %d")


def fetch_single_feed(feed_info):
    """Fetch and parse a single RSS feed. Used for parallel fetching."""
    url = feed_info["url"]
    category = feed_info["category"]
    region = feed_info["region"]
    articles = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            feed_data = feedparser.parse(response.content)
            source_name = feed_data.feed.get("title", "Unknown Source")
            
            for entry in feed_data.entries[:MAX_ARTICLES_PER_FEED]:
                title = entry.get("title", "")
                if not title:
                    continue
                
                # Get content — try multiple fields since RSS feeds are inconsistent
                content = ""
                if hasattr(entry, "content") and entry.content:
                    content = entry.content[0].get("value", "")
                if not content:
                    content = entry.get("summary", "")
                if not content:
                    content = entry.get("description", "")
                
                parsed_date = parse_date(entry.get("published", entry.get("updated", "")))
                
                articles.append({
                    "title": clean_html(title),
                    "link": entry.get("link", "#"),
                    "published": entry.get("published", entry.get("updated", "Unknown")),
                    "published_dt": parsed_date,
                    "time_ago": relative_time(parsed_date),
                    "source": source_name,
                    "content": clean_html(content),
                    "category": category,
                    "region": region,
                    "summary": "",  # Populated by AI if enabled
                    "importance": 0,  # Calculated after collection
                })
    except Exception as e:
        print(f"[WARN] Failed to fetch {url}: {e}")
    
    return articles


def get_all_articles():
    """Fetch all feeds in parallel, merge, sort by date, and return."""
    now = time.time()
    
    # Return cached articles if still fresh
    if _cache["articles"] and (now - _cache["timestamp"]) < CACHE_DURATION:
        return _cache["articles"]
    
    all_articles = []
    
    # Fetch feeds in parallel (much faster than sequential)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_single_feed, feed): feed for feed in feeds}
        for future in as_completed(futures):
            try:
                articles = future.result()
                all_articles.extend(articles)
            except Exception as e:
                print(f"[WARN] Feed fetch error: {e}")
    
    # Deduplicate by title similarity (some feeds share articles)
    seen_titles = set()
    unique_articles = []
    for article in all_articles:
        title_key = article["title"].lower().strip()[:80]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_articles.append(article)
    
    # Sort by date — most recent first (this is what mixes the sources)
    unique_articles.sort(key=lambda x: x["published_dt"], reverse=True)
    
    # Calculate importance scores
    for article in unique_articles:
        article["importance"] = calculate_importance(article)
    
    # Update cache
    _cache["articles"] = unique_articles
    _cache["timestamp"] = now
    
    return unique_articles


# ============================================================
# AI SUMMARIZATION (optional — requires ANTHROPIC_API_KEY)
# ============================================================

# File-based summary cache so you don't re-summarize articles you've already seen
SUMMARY_CACHE_FILE = os.path.join(os.path.dirname(__file__), "summary_cache.json")

def load_summary_cache():
    """Load cached summaries from disk."""
    if os.path.exists(SUMMARY_CACHE_FILE):
        try:
            with open(SUMMARY_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_summary_cache(cache):
    """Save summary cache to disk."""
    try:
        with open(SUMMARY_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        print(f"[WARN] Failed to save summary cache: {e}")


def get_article_hash(article):
    """Generate a unique hash for an article based on title and link."""
    key = f"{article['title']}|{article['link']}"
    return hashlib.md5(key.encode()).hexdigest()


def summarize_articles(articles, max_batch=20):
    """
    Summarize articles using Claude API. 
    Only summarizes unsummarized articles, up to max_batch per call.
    Uses disk cache to avoid re-summarizing on refresh.
    """
    if not AI_ENABLED:
        return articles
    
    cache = load_summary_cache()
    to_summarize = []
    
    for article in articles:
        article_hash = get_article_hash(article)
        if article_hash in cache:
            article["summary"] = cache[article_hash]
        elif not article["summary"]:
            to_summarize.append(article)
    
    # Batch summarize new articles (limit to control API costs)
    if to_summarize:
        batch = to_summarize[:max_batch]
        
        # Build the prompt with all articles to summarize in one call
        articles_text = ""
        for i, article in enumerate(batch):
            articles_text += f"\n---\nARTICLE {i+1}:\nTitle: {article['title']}\nSource: {article['source']}\nCategory: {article['category']}\nContent: {article['content']}\n"
        
        prompt = f"""Summarize each article below in exactly 2 sentences. Be specific and factual. 
For cybersecurity articles: highlight the threat, vulnerability, or incident and who is affected.
For finance articles: highlight the key data point or market movement and its implication.
For news articles: highlight the core event and its significance.

If an article's content is too short or vague to summarize meaningfully, write "Insufficient detail — click through to read."

Return ONLY a JSON array of strings, one summary per article, in the same order. No markdown, no extra text.

{articles_text}"""
        
        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            
            if response.status_code == 200:
                data = response.json()
                text = data["content"][0]["text"].strip()
                # Clean potential markdown code fences
                text = re.sub(r"^```json\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                summaries = json.loads(text)
                
                for i, article in enumerate(batch):
                    if i < len(summaries):
                        article["summary"] = summaries[i]
                        cache[get_article_hash(article)] = summaries[i]
                
                save_summary_cache(cache)
            else:
                print(f"[WARN] Claude API error: {response.status_code} — {response.text[:200]}")
        
        except Exception as e:
            print(f"[WARN] AI summarization failed: {e}")
    
    return articles


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    articles = get_all_articles()
    
    # Get available categories and regions for filter buttons
    categories = sorted(set(a["category"] for a in articles))
    regions = sorted(set(a["region"] for a in articles))
    
    # Apply filters if provided
    selected_category = request.args.get("category", "all")
    selected_region = request.args.get("region", "all")
    selected_time = request.args.get("time", "all")
    selected_sort = request.args.get("sort", "new")
    
    filtered = articles
    if selected_category != "all":
        filtered = [a for a in filtered if a["category"] == selected_category]
    if selected_region != "all":
        filtered = [a for a in filtered if a["region"] == selected_region]
    
    # Time filter
    if selected_time != "all":
        now = datetime.now(timezone.utc)
        time_windows = {
            "1h": 3600,
            "6h": 21600,
            "today": None,  # Special: since midnight
            "24h": 86400,
            "48h": 172800,
            "week": 604800,
            "month": 2592000,
        }
        
        if selected_time == "today":
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            filtered = [a for a in filtered if a["published_dt"] >= midnight]
        elif selected_time in time_windows:
            cutoff = now.timestamp() - time_windows[selected_time]
            filtered = [a for a in filtered if a["published_dt"].timestamp() >= cutoff]
    
    # Sort modes
    if selected_sort == "hot":
        # Hot = importance score with recency boost (best stuff from recent hours)
        filtered.sort(key=lambda x: x["importance"], reverse=True)
    elif selected_sort == "top":
        # Top = pure importance score regardless of recency (best stuff period)
        filtered.sort(key=lambda x: x["importance"], reverse=True)
    else:
        # New = chronological (default, already sorted this way from cache)
        filtered.sort(key=lambda x: x["published_dt"], reverse=True)
    
    # Run AI summarization on filtered articles (if enabled)
    if AI_ENABLED:
        filtered = summarize_articles(filtered)
    
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return render_template(
        "index.html",
        articles=filtered,
        categories=categories,
        regions=regions,
        selected_category=selected_category,
        selected_region=selected_region,
        selected_time=selected_time,
        selected_sort=selected_sort,
        last_updated=last_updated,
        total_count=len(articles),
        filtered_count=len(filtered),
        ai_enabled=AI_ENABLED,
    )


@app.route("/refresh")
def refresh():
    """Force refresh the feed cache."""
    _cache["articles"] = []
    _cache["timestamp"] = 0
    return jsonify({"status": "ok", "message": "Cache cleared. Reload the page."})


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  CYBERFEED v2.0")
    print(f"  AI Summarization: {'ENABLED' if AI_ENABLED else 'DISABLED (set ANTHROPIC_API_KEY to enable)'}")
    print(f"  Feeds configured: {len(feeds)}")
    print(f"{'='*50}\n")
    app.run(debug=True)
