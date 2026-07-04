# Oraculum landing page

The public landing page for Oraculum, hosted on GitHub Pages.

- **`index.html`** — a single, self-contained page (all CSS/JS inlined, only Google
  Fonts loaded externally). It is a pixel-faithful, framework-free rebuild of the
  `Oraculum - Landing v2` design prototype from
  `docs/oraculum-cloud-design-project/`.
- Fully responsive (desktop / tablet / mobile), with scroll-reveal entrance
  animations, hover states, four live interactive demos (the Interrogation, the
  Verdict Playground, the Signal Oracle, the Judge Calibrator) that faithfully
  reproduce `verdict_engine.py`'s decision tree and the Cohen's κ math, plus
  idle auto-play.

## Deploy

Published by `.github/workflows/pages.yml` — every push to `main` that touches
`landing/` uploads this directory as the GitHub Pages site. Enable it once under
**Settings → Pages → Build and deployment → Source: GitHub Actions**.

## Local preview

```bash
python3 -m http.server 4321 --directory landing
# open http://localhost:4321/
```
