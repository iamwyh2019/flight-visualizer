# Deploying the flight map

The app is two parts:

- **Fetcher** (FastAPI backend) — a heavy *local build tool* that downloads multi-GB
  adsb.lol archives and writes the per-flight cache. **Never deploy this publicly** —
  it would let anyone trigger ~200 GB of downloads.
- **Visualizer** (static `frontend/`) — the actual website: a dark map of your flown
  tracks with hover, replay, filters, and airport diagrams.

So the public deploy is **just the static visualizer**. With no backend present, the
frontend auto-detects it (`HEAD /api/flights` 404), **hides the Fetch tab, and shows
only the Visualizer** — the fetcher is disabled by simply not being there.

## 1. Build the static site (locally)

```bash
# (you already do this) run the fetcher locally to populate data/cache/flights/
python -m uvicorn backend.app:app --port 8011   # click "Fetch all flights", let it run

# dump the cache to a static file the visualizer can load with no backend
python scripts/export_static.py                  # writes frontend/data/flights.json
```

`frontend/` is now a self-contained static site (~6 MB). It loads Leaflet from a CDN;
everything else (borders, runways, airport diagrams, airline logos, your flights) is
local. Re-run `export_static.py` whenever you fetch new flights, then redeploy.

## 2. Serve `frontend/` on your server

Pick one — they all just serve the `frontend/` directory as static files.

**Caddy (easiest, automatic HTTPS):** `/etc/caddy/Caddyfile`
```
flights.example.com {
    root * /var/www/flight-map/frontend
    file_server
    try_files {path} /index.html
}
```
```bash
sudo cp -r frontend /var/www/flight-map/      # or rsync from your laptop
sudo systemctl reload caddy
```

**nginx:** `/etc/nginx/sites-available/flight-map`
```nginx
server {
    listen 80;
    server_name flights.example.com;
    root /var/www/flight-map/frontend;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/flight-map /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# HTTPS: sudo certbot --nginx -d flights.example.com
```

**No web server / quick test:** `cd frontend && python -m http.server 8080`

**No server at all:** `frontend/` works on GitHub Pages / Netlify / Cloudflare Pages —
just publish that folder (commit `frontend/data/flights.json` for those, since it's
gitignored by default).

## 3. Updating

```bash
# locally: fetch new flights, re-export, then copy frontend/ up
python scripts/export_static.py
rsync -av --delete frontend/ user@server:/var/www/flight-map/frontend/
```

## (Optional) Full app on a private server

If you want the fetcher available too (e.g. on a private/VPN-only host), run the
backend with `uvicorn backend.app:app --host 127.0.0.1 --port 8011` behind your
reverse proxy. **Do not expose `/api/fetch-latest` publicly** — gate it behind auth
or a private network, or strip those routes. For a public site, prefer the static
deploy above.
