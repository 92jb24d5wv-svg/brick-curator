# Brick Curator Pro

A mobile-first Streamlit web app for LEGO BrickLink sellers. Enter a LEGO set number and your purchase cost, then get a part-out value, demand score, buy/pass recommendation, high-demand lots, slow/dead lots, analysis history, and Excel export.

## Features

- iPhone-friendly layout
- BrickLink OAuth API support
- Set part-out inventory lookup
- Last 6 months sold data
- Current listing/stock data
- Demand scoring by lot
- Buy/pass recommendation score
- Top lots to list first
- Analysis history CSV
- Excel report download

## Files

Upload these to GitHub:

- `app.py`
- `requirements.txt`
- `.streamlit/config.toml`
- `.streamlit/secrets.toml.example`
- `.gitignore`
- `README.md`

Do **not** upload your real `.streamlit/secrets.toml` file.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Cloud

1. Create a GitHub repository, for example `brick-curator-pro`.
2. Upload the files from this folder.
3. Go to Streamlit Community Cloud.
4. Choose your GitHub repo.
5. Set the main file to `app.py`.
6. In Streamlit Cloud, open **App settings > Secrets** and add:

```toml
BL_CONSUMER_KEY="your_key"
BL_CONSUMER_SECRET="your_secret"
BL_TOKEN_VALUE="your_token"
BL_TOKEN_SECRET="your_token_secret"
```

7. Deploy.
8. Open the Streamlit app URL in Safari on your iPhone.
9. Tap **Share > Add to Home Screen**.

## Getting BrickLink API keys

In your BrickLink account, request/create API credentials. You need:

- Consumer Key
- Consumer Secret
- Token Value
- Token Secret

Keep these private. Do not post them in GitHub.

## Notes

BrickLink API calls can take a while because the app checks both sold data and current stock data for every lot in the set. Large sets may take longer.

## Fast Mode with Custom Settings

This version includes Fast Mode to reduce BrickLink API calls and speed up iPhone use.

When Fast Mode is enabled, you can customize:

- Maximum lots to check
- Minimum quantity per lot
- Whether to include parts, minifigs, instructions/books, and box/set lots
- Priority order: minifigs first, highest quantity first, or item number order

Fast Mode results are faster, but the part-out value and buy/pass score are based only on the lots analyzed. Turn Fast Mode off for a full-set analysis.
