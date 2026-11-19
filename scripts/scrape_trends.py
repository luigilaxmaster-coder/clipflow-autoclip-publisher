import re
import json
import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

r = requests.get('https://yttrendz.com/youtube-trends/united-states-of-america', headers=headers)
text = r.text

print("Has table:", '<table' in text)
print("Has tr:", '<tr' in text)
# Find all occurrences of watch?v=
watch_ids = re.findall(r'watch\?v=([a-zA-Z0-9_-]{11})', text)
print("Watch IDs found:", len(watch_ids), set(watch_ids[:10]))

# Search for any text following a thumbnail or video pattern
ytimg = re.findall(r'https?://i\.ytimg\.com/vi/([a-zA-Z0-9_-]{11})', text)
print("ytimg IDs found:", len(ytimg), set(ytimg[:10]))

for m in re.finditer(r'ytimg\.com/vi/([a-zA-Z0-9_-]{11})', text):
    start = max(0, m.start() - 200)
    end = min(len(text), m.end() + 300)
    print("Block:", text[start:end])
    break
