# Foam Property Predictor GUI

Local browser GUI for predicting `BC(t)` and `Ravg(t)` together from the saved best HD/no-HD model handoffs.

## Run

```bash
python3 app.py
```

Open:

```text
http://127.0.0.1:8786/
```

## Models Used

- BC(t) HD: `models/bct_hd_v10b_predictor.pkl`
- BC(t) no-HD: `models/bct_light_no_hd_predictor.pkl`
- Ravg(t) HD: `models/ravg_t_hd_v6_predictor.pkl`
- Ravg(t) no-HD: `models/ravg_t_no_hd_v10_predictor.pkl`

## Explanation Layer

The interpretation panel uses the bubble-stability tutorial concepts:
foam films, drainage, coalescence, gas diffusion/coarsening, particle barriers, half-life, and physically plausible time-lapse behavior.
