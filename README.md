# Foam Property Predictor GUI

Browser GUI for predicting `BC(t)` and `Ravg(t)` together from the saved best HD/no-HD model handoffs.

The app can run two ways:

- local Python server, using the bundled `.pkl` predictors
- static GitHub Pages, using `static_model_data.json` and in-browser prediction

## Run

```bash
python3 app.py
```

Open:

```text
http://127.0.0.1:8786/
```

## Static GitHub Pages

For GitHub Pages, serve the repository root as a static site. The frontend first tries the local `/api` endpoints; if those are unavailable, it automatically loads `static_model_data.json` and performs the same support-curve prediction in browser JavaScript.

## Models Used

- BC(t) HD: `models/bct_hd_v10b_predictor.pkl`
- BC(t) no-HD: `models/bct_light_no_hd_predictor.pkl`
- Ravg(t) HD: `models/ravg_t_hd_v6_predictor.pkl`
- Ravg(t) no-HD: `models/ravg_t_no_hd_v10_predictor.pkl`
- Static browser payload: `static_model_data.json`

## Explanation Layer

The interpretation panel uses the bubble-stability tutorial concepts:
foam films, drainage, coalescence, gas diffusion/coarsening, particle barriers, half-life, and physically plausible time-lapse behavior.
