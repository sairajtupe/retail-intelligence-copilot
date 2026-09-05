TRACK_ID=PS03

# Retail Intelligence Copilot

A dark-theme AI dashboard for retail managers. It shows live KPIs (revenue,
units, margin, inventory, stock-out risks), an attention shelf, sales trends and
category share, and a Copilot chat that answers natural-language questions about
your data using `gemini-3.5-flash-lite`.

## How to Run

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:8000 in your browser.

Set the Gemini API key to enable the Copilot chat:

```bash
# Windows (PowerShell)
$env:GEMINI_API_KEY="your-key-here"
# macOS / Linux
export GEMINI_API_KEY="your-key-here"
```

Without a key the dashboard still works; the Copilot returns a clear
"insufficient data" fallback message.

Live demo video: [INSERT_VIDEO_LINK_HERE]