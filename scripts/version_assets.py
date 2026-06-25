"""Cache-bust the static site's local assets by stamping a content hash onto
their URLs in frontend/index.html.

Browsers cache map.js / style.css / etc. aggressively, so after you scp a new
build the old files keep getting served. This rewrites each local <script>/<link>
reference to `file.js?v=<hash>`, where <hash> is an md5 of that file's contents.
Because the hash changes only when the file changes, browsers refetch exactly the
files that changed and keep the rest cached.

Run it before you copy frontend/ up to the server:

    python scripts/version_assets.py
    scp -r frontend/ user@host:/var/www/flight-map/

CDN assets (https://...) are left untouched — they carry their own version.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
HTML = FRONTEND / "index.html"

# href="style.css" / src="map.js", optionally already carrying a ?v=... we replace.
# Skips absolute URLs (http:, https:, //).
REF = re.compile(
    r'(?P<attr>href|src)="(?P<path>(?!https?:|//)[^"?]+\.(?:js|css))(?:\?v=[^"]*)?"'
)


def main() -> None:
    text = HTML.read_text(encoding="utf-8")
    changed: list[str] = []

    def stamp(m: re.Match) -> str:
        path = m.group("path")
        asset = FRONTEND / path
        if not asset.is_file():
            return m.group(0)  # referenced file missing — leave it alone
        h = hashlib.md5(asset.read_bytes()).hexdigest()[:8]
        changed.append(f"{path}?v={h}")
        return f'{m.group("attr")}="{path}?v={h}"'

    new = REF.sub(stamp, text)
    if new != text:
        HTML.write_text(new, encoding="utf-8")
    for c in changed:
        print(f"  {c}")
    print(f"Stamped {len(changed)} asset(s) in {HTML.relative_to(ROOT)}"
          + ("" if new != text else " (no change)"))


if __name__ == "__main__":
    main()
