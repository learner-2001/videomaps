# videomaps — "Brazil: The Country That Feeds the World" (60s, 1080x1920)

Vertical map-explainer rendered from **real data** (no hallucinated geography):

| stage | file | runs on |
|---|---|---|
| asset pull (admin-0 shapes, flags, fonts, World Bank facts) | `render/fetch_assets.py` | GitHub runner (4 cores) or sandbox |
| frame-loop benchmark (MapLibre GL + headless Chromium) | `render/bench.py` | GitHub runner |
| timeline built from voice-over first | `render/timeline.py` | sandbox |
| frames -> mp4 | `render/render_all.py` | GitHub runner |

Triggers: push to a `render/**` branch (the fine-grained PAT cannot call the Actions API, so push is the trigger).
Results land on branch `render-out` under `results/`, plus a workflow artifact.
Data licences: geoBoundaries (CC-BY 4.0), Natural Earth (public domain), flagcdn (MIT), Google Fonts (OFL),
Esri World Imagery tiles (attribution required), Wikimedia Commons (CC).
