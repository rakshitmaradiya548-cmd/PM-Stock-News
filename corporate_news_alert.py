"""
Corporate Actions + News Alert
Runs every 15 minutes during market hours. Two parts:

1. CORPORATE ACTIONS: Checks NSE's official announcements feed for ANY
   significant corporate action from Nifty 500 companies - not just results,
   but also dividends, buybacks, bonus issues, stock splits, mergers,
   acquisitions, board meeting outcomes, etc.

2. GENERAL NEWS: Checks a few major financial news RSS feeds (free, no API
   key needed) for headlines mentioning Nifty 500 companies. This is
   best-effort - it catches what these specific feeds publish, not every
   possible news source, and matches by company name in the headline.

Both parts remember what they've already sent (using seen files committed
back to the repo) so you don't get repeat alerts.
"""

import os
import json
import requests
import xml.etree.ElementTree as ET

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

NSE_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"
NSE_LIST_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

SEEN_ANNOUNCEMENTS_FILE = "seen_announcements.json"
SEEN_NEWS_FILE = "seen_news.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}

# Keywords that suggest a corporate action is actually significant enough to
# alert on - filters out routine/administrative filings that don't move price
SIGNIFICANT_KEYWORDS = [
    "result", "dividend", "buyback", "bonus", "split", "merger", "acquisition",
    "stake", "rights issue", "board meeting", "delisting", "fund raising",
    "preferential issue", "amalgamation", "demerger", "open offer",
]

# Free financial news RSS feeds - no API key required
NEWS_RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
]


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code != 200:
            print(f"Telegram send failed: {response.text}")
    except Exception as e:
        print(f"Telegram error: {e}")


def get_nifty500_data():
    """Returns a dict mapping {symbol: company_name} for all Nifty 500 stocks."""
    try:
        import pandas as pd
        response = requests.get(NSE_LIST_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()
        with open("nifty500_raw.csv", "wb") as f:
            f.write(response.content)
        df = pd.read_csv("nifty500_raw.csv")
        return dict(zip(df["Symbol"], df["Company Name"]))
    except Exception as e:
        print(f"Could not load Nifty 500 list: {e}")
        return {}


def load_seen(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(filepath, seen_set):
    with open(filepath, "w") as f:
        json.dump(list(seen_set), f)


# --- PART 1: Corporate Actions ---
def check_corporate_actions(nifty500_symbols):
    seen = load_seen(SEEN_ANNOUNCEMENTS_FILE)
    new_alerts = 0

    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.get("https://www.nseindia.com", timeout=10)
        response = session.get(NSE_ANNOUNCEMENTS_URL, timeout=15)
        response.raise_for_status()
        announcements = response.json()
    except Exception as e:
        print(f"Failed to fetch NSE announcements: {e}")
        return 0

    for item in announcements:
        symbol = item.get("symbol", "")
        subject = (item.get("subject", "") or item.get("desc", "")).lower()
        announcement_id = f"{symbol}_{item.get('an_dt', '')}_{subject}"

        if symbol not in nifty500_symbols:
            continue
        if not any(keyword in subject for keyword in SIGNIFICANT_KEYWORDS):
            continue
        if announcement_id in seen:
            continue

        message = (f"📢 CORPORATE ACTION: {symbol}\n\n"
                   f"{item.get('subject', item.get('desc', ''))}\n\n"
                   f"Time: {item.get('an_dt', 'N/A')}")
        send_telegram_message(message)
        seen.add(announcement_id)
        new_alerts += 1
        print(f"Sent corporate action alert for {symbol}")

    save_seen(SEEN_ANNOUNCEMENTS_FILE, seen)
    return new_alerts


# --- PART 2: General News ---
def check_general_news(nifty500_data):
    seen = load_seen(SEEN_NEWS_FILE)
    new_alerts = 0

    for feed_url in NEWS_RSS_FEEDS:
        try:
            response = requests.get(feed_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as e:
            print(f"Failed to fetch/parse feed {feed_url}: {e}")
            continue

        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            if title_el is None:
                continue
            title = title_el.text or ""
            link = link_el.text if link_el is not None else ""

            for symbol, company_name in nifty500_data.items():
                if not isinstance(company_name, str):
                    continue
                # Match if the company's first significant word appears in the headline
                first_word = company_name.split()[0]
                if len(first_word) > 3 and first_word.lower() in title.lower():
                    news_id = title  # dedupe by headline text itself
                    if news_id in seen:
                        continue
                    message = f"📰 NEWS: {symbol} ({company_name})\n\n{title}\n\n{link}"
                    send_telegram_message(message)
                    seen.add(news_id)
                    new_alerts += 1
                    print(f"Sent news alert for {symbol}: {title}")
                    break  # avoid matching the same headline to multiple companies

    save_seen(SEEN_NEWS_FILE, seen)
    return new_alerts


if __name__ == "__main__":
    nifty500_data = get_nifty500_data()
    nifty500_symbols = set(nifty500_data.keys())

    if not nifty500_symbols:
        print("Could not load Nifty 500 list. Exiting.")
        exit()

    action_alerts = check_corporate_actions(nifty500_symbols)
    news_alerts = check_general_news(nifty500_data)

    print(f"\nDone. {action_alerts} corporate action alert(s), {news_alerts} news alert(s) sent.")
