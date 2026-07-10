# Scopio Lead Finder — Chrome extension

Find local business leads from your browser toolbar — **free, no API key**. It runs a
search in your **Scopio account** (OpenStreetMap + Geoapify) and shows scored results,
already saved to your dashboard. Manifest V3, consent-first, no background tracking.

## What it does
- ✅ **Consent gate** on first run; settings stored only in `chrome.storage.local`.
- ✅ **Free discovery** — type an address + radius → it runs a Scopio search (no key, no cost).
- ✅ **Lead-quality score** per business (online presence) and results **saved to your account**,
  where AI enrichment + outreach take over.
- ❌ No Google Maps scraping (against Google's ToS).

## Load it (developer mode)
1. Open **chrome://extensions**, toggle **Developer mode** (top-right).
2. **Load unpacked** → select this `extension/` folder.
3. Click the extension icon → **Allow** on the consent screen.
4. **Open settings** → enter your Scopio **server URL** (e.g. `http://localhost:8000`) +
   your login → **Connect** (Chrome asks permission to reach that server → Allow).
5. Back in the popup: type an area (e.g. *"Bandra, Mumbai"*), set a radius → **Find leads (free)** →
   scored results appear and are saved to your Scopio account → **Open Scopio dashboard** to
   enrich & reach out.

## Notes
- **No API key or billing needed** — it uses your Scopio account's free OSM + Geoapify discovery.
- The Scopio server must be running and reachable from your browser.
- Icons are omitted (Chrome uses a default). Add an `icons` entry to `manifest.json` for branding.
