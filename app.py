from __future__ import annotations

import json
import math
import os
import pickle
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
CODEX_ROOT = Path("/Users/elfedwa/AIMAT Dropbox/Fedwa El/Mac (2)/Documents/Codex")

MODEL_PATHS = {
    "ravg_hd": ROOT / "models/ravg_t_hd_v6_predictor.pkl",
    "ravg_no_hd": ROOT / "models/ravg_t_no_hd_v10_predictor.pkl",
    "bc_hd": ROOT / "models/bct_hd_v10b_predictor.pkl",
    "bc_no_hd": ROOT / "models/bct_light_no_hd_predictor.pkl",
}

SURF_EXTRA = [
    "chemistry_descriptor_number",
    "formulation_context_descriptor",
    "medium_salinity_ppm",
    "ionic_code",
    "active_mid_pct",
    "pH_mid",
    "density_g_ml",
    "internal_salt_pct",
    "amphoteric_flag",
    "anionic_flag",
]
NANO_EXTRA = [
    "atom_count",
    "mean_atomic_Z",
    "max_atomic_Z",
    "oxygen_count",
    "hydrogen_count",
    "oxygen_fraction_nonH",
    "hetero_fraction_nonH",
    "hydroxide_flag",
    "carbon_flag",
    "nitride_flag",
    "family_code",
    "morphology_code",
]


def _load_pickle(path: Path) -> dict:
    with path.open("rb") as f:
        return pickle.load(f)


def _finite_float(value, fallback=0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(fallback)
    if not math.isfinite(out):
        return float(fallback)
    return out


def _col_first(df: pd.DataFrame, col: str, fallback=0.0) -> float:
    if col not in df.columns:
        return float(fallback)
    return _finite_float(pd.to_numeric(df[col], errors="coerce").dropna().iloc[0], fallback) if pd.to_numeric(df[col], errors="coerce").notna().any() else float(fallback)


def _metrics_map(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {}
    if {"metric", "value"}.issubset(df.columns):
        return {str(r["metric"]): _finite_float(r["value"]) for _, r in df.iterrows()}
    return {}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        out = float(value)
        return out if math.isfinite(out) else None
    if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
        return None
    return value


def _crossing_time(times: np.ndarray, values: np.ndarray, threshold: float, direction: str = "down") -> float | None:
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(times) < 2:
        return None
    if direction == "down":
        hit = np.where(values <= threshold)[0]
    else:
        hit = np.where(values >= threshold)[0]
    if len(hit) == 0:
        return None
    idx = int(hit[0])
    if idx == 0:
        return float(times[0])
    x0, x1 = float(times[idx - 1]), float(times[idx])
    y0, y1 = float(values[idx - 1]), float(values[idx])
    if abs(y1 - y0) < 1e-12:
        return x1
    frac = (threshold - y0) / (y1 - y0)
    return float(x0 + np.clip(frac, 0, 1) * (x1 - x0))


def _half_life_summary(times: np.ndarray, values: np.ndarray, uncertainty: np.ndarray) -> dict:
    values = np.asarray(values, dtype=float)
    uncertainty = np.asarray(uncertainty, dtype=float)
    initial = float(np.median(values[: max(3, min(10, len(values)))]))
    threshold = 0.5 * initial
    lower_curve = np.clip(values - uncertainty, 0, None)
    upper_curve = np.clip(values + uncertainty, 0, None)
    central = _crossing_time(times, values, threshold, "down")
    early = _crossing_time(times, lower_curve, threshold, "down")
    late = _crossing_time(times, upper_curve, threshold, "down")
    if central is None:
        return {
            "value_s": None,
            "lower_s": early,
            "upper_s": late,
            "threshold": threshold,
            "status": "not_reached",
            "label": f">{float(times[-1]):.0f} s",
        }
    low_candidates = [x for x in [early, central] if x is not None]
    high_candidates = [x for x in [late, central] if x is not None]
    lower = min(low_candidates) if low_candidates else central
    upper = max(high_candidates) if high_candidates else central
    return {
        "value_s": central,
        "lower_s": lower,
        "upper_s": upper,
        "threshold": threshold,
        "status": "estimated",
        "label": f"{central:.1f} s",
    }


def _relative_uncertainty_pct(values: np.ndarray, uncertainty: np.ndarray) -> np.ndarray:
    denom = np.maximum(np.abs(np.asarray(values, dtype=float)), 1e-6)
    pct = (np.asarray(uncertainty, dtype=float) / denom) * 100.0
    return np.clip(pct, 0, 999.0)


def _half_life_uncertainty_pct(half_life: dict) -> float | None:
    value = half_life.get("value_s")
    lower = half_life.get("lower_s")
    upper = half_life.get("upper_s")
    if value is None or lower is None or upper is None or value <= 0:
        return None
    return max(abs(value - lower), abs(upper - value)) / value * 100.0


def _build_supports_from_reference(model: dict, target_col: str) -> list[dict]:
    pred = model["reference_predictions"].copy()
    if target_col not in pred.columns:
        target_col = "BC_mean"
    supports = []
    for (branch, conc), g in pred.groupby(["branch", "concentration_wt"], sort=True):
        g = g.sort_values("time_s")
        features = {
            "surf_desc": _col_first(g, "surfactant_descriptor_number_fixed"),
            "nano_desc": _col_first(g, "nano_descriptor_number_fixed"),
            "logC": float(np.log10(float(conc))),
            "hfoam": float(pd.to_numeric(g.get("HD_hfoam_mean", pd.Series([0])), errors="coerce").median()),
            "hliquid": float(pd.to_numeric(g.get("HD_hliquid_mean", pd.Series([0])), errors="coerce").median()),
        }
        for col in SURF_EXTRA + NANO_EXTRA:
            features[col] = _col_first(g, col)
        prediction = pd.to_numeric(g[target_col], errors="coerce").ffill().bfill().to_numpy(float)
        supports.append(
            {
                "branch": str(branch),
                "surfactant_type": str(g["surfactant_type"].iloc[0]),
                "nanoparticle_type": str(g["nanoparticle_type"].iloc[0]),
                "concentration_wt": float(conc),
                "feature_values": features,
                "time_s": g["time_s"].to_numpy(float),
                "prediction": prediction,
                "fit_error": np.abs(pd.to_numeric(g.get("BC_mean", g[target_col]), errors="coerce").to_numpy(float) - pd.to_numeric(g[target_col], errors="coerce").to_numpy(float)),
            }
        )
    return supports


class FoamModels:
    def __init__(self) -> None:
        self.models = {name: _load_pickle(path) for name, path in MODEL_PATHS.items()}
        self.ravg_supports = {
            "hd": self.models["ravg_hd"]["support_curves"],
            "no_hd": self.models["ravg_no_hd"]["support_curves"],
        }
        self.bc_supports = {
            "hd": _build_supports_from_reference(self.models["bc_hd"], self.models["bc_hd"]["metadata"].get("prediction_column", "prediction_BC")),
            "no_hd": _build_supports_from_reference(self.models["bc_no_hd"], self.models["bc_no_hd"]["metadata"].get("prediction_column", "prediction_BC")),
        }
        self.lookup = self._make_lookup()

    def _prediction_tables(self) -> list[pd.DataFrame]:
        return [
            self.models["ravg_hd"]["known_curve_predictions"],
            self.models["ravg_no_hd"]["known_curve_predictions"],
            self.models["bc_hd"]["reference_predictions"],
            self.models["bc_no_hd"]["reference_predictions"],
        ]

    def _make_lookup(self) -> dict:
        rows = pd.concat(self._prediction_tables(), ignore_index=True, sort=False)
        surf = rows.drop_duplicates("surfactant_type").set_index("surfactant_type")
        nano = rows.drop_duplicates("nanoparticle_type").set_index("nanoparticle_type")
        concentrations = sorted(float(x) for x in rows["concentration_wt"].dropna().unique())
        time_s = pd.to_numeric(rows["time_s"], errors="coerce").dropna()
        hfoam = pd.to_numeric(rows.get("HD_hfoam_mean", pd.Series([150])), errors="coerce").dropna()
        hliquid = pd.to_numeric(rows.get("HD_hliquid_mean", pd.Series([20])), errors="coerce").dropna()
        return {
            "surfactants": sorted(str(x) for x in rows["surfactant_type"].dropna().unique()),
            "nanoparticles": sorted(str(x) for x in rows["nanoparticle_type"].dropna().unique()),
            "concentrations": concentrations,
            "time_min": float(time_s.min()),
            "time_max": float(time_s.max()),
            "surf": surf,
            "nano": nano,
            "hfoam_median": float(hfoam.median()),
            "hliquid_median": float(hliquid.median()),
            "ranges": {
                "concentration_wt": {"min": float(min(concentrations)), "max": float(max(concentrations)), "step": 0.000001},
                "time_s": {"min": round(float(time_s.min())), "max": round(float(time_s.max())), "step": 1},
                "hfoam": {"min": round(float(hfoam.min()), 2), "max": round(float(hfoam.max()), 2), "step": 0.01},
                "hliquid": {"min": round(float(hliquid.min()), 2), "max": round(float(hliquid.max()), 2), "step": 0.01},
            },
        }

    def query_features(self, surf: str, nano: str, concentration: float, hfoam: float | None, hliquid: float | None) -> dict:
        srow = self.lookup["surf"].loc[surf] if surf in self.lookup["surf"].index else pd.Series(dtype=float)
        nrow = self.lookup["nano"].loc[nano] if nano in self.lookup["nano"].index else pd.Series(dtype=float)
        features = {
            "surf_desc": _finite_float(srow.get("surfactant_descriptor_number_fixed", srow.get("surfactant_descriptor_number", 0.0))),
            "nano_desc": _finite_float(nrow.get("nano_descriptor_number_fixed", nrow.get("nano_descriptor_number", 0.0))),
            "logC": float(np.log10(max(float(concentration), 1e-9))),
            "hfoam": _finite_float(hfoam, self.lookup["hfoam_median"]),
            "hliquid": _finite_float(hliquid, self.lookup["hliquid_median"]),
        }
        for col in SURF_EXTRA:
            features[col] = _finite_float(srow.get(col, 0.0))
        for col in NANO_EXTRA:
            features[col] = _finite_float(nrow.get(col, 0.0))
        return features

    def feature_columns(self, target: str, mode: str, surf: str) -> list[str]:
        if target == "ravg":
            model = self.models["ravg_hd" if mode == "hd" else "ravg_no_hd"]
            if mode == "no_hd" and surf == "New CoCO-Betaine":
                return list(model.get("coco_feature_columns") or model["feature_columns"])
            if mode == "no_hd":
                return list(model.get("light_feature_columns") or ["surf_desc", "nano_desc", "logC"])
            return list(model["feature_columns"])
        if mode == "hd":
            return ["surf_desc", "nano_desc", "logC", "hfoam", "hliquid"]
        return ["surf_desc", "nano_desc", "logC"]

    def predict_support_curve(self, supports: list[dict], features: dict, columns: list[str], times: np.ndarray, target_key: str) -> dict:
        mat = np.array([[float(s["feature_values"].get(c, 0.0)) for c in columns] for s in supports], dtype=float)
        x = np.array([float(features.get(c, 0.0)) for c in columns], dtype=float)
        mu = np.nanmean(mat, axis=0)
        sd = np.nanstd(mat, axis=0)
        sd[sd == 0] = 1.0
        d = np.sqrt((((mat - mu) / sd - (x - mu) / sd) ** 2).sum(axis=1))
        k = min(6, len(supports))
        idx = np.argsort(d)[:k]
        weights = 1.0 / np.maximum(d[idx], 1e-6)
        weights = weights / weights.sum()
        curves = []
        errs = []
        names = []
        for i, w in zip(idx, weights):
            support = supports[int(i)]
            y = support.get(target_key, support.get("prediction"))
            curves.append(np.interp(times, support["time_s"], y))
            err = support.get("Ravg_abs_fit_error", support.get("fit_error", np.zeros_like(y)))
            errs.append(np.interp(times, support["time_s"], np.asarray(err, dtype=float)))
            names.append({"branch": support["branch"], "concentration_wt": support["concentration_wt"], "weight": float(w), "distance": float(d[int(i)])})
        curve_stack = np.vstack(curves)
        pred = np.sum(curve_stack * weights[:, None], axis=0)
        spread = np.sqrt(np.sum(weights[:, None] * (curve_stack - pred) ** 2, axis=0))
        err_stack = np.vstack(errs)
        uncertainty = np.sum(err_stack * weights[:, None], axis=0) + 0.35 * spread
        nearest = float(d[int(idx[0])])
        p75 = float(np.quantile(d, 0.75)) or 1.0
        median_unc = float(np.median(uncertainty))
        scale = 80.0 if target_key == "Ravg_pred_known" else 12.0
        trust = float(np.clip(math.exp(-nearest / max(p75, 1e-6)) * math.exp(-median_unc / scale), 0.02, 0.98))
        return {
            "values": np.clip(pred, 0, None),
            "uncertainty": np.clip(uncertainty, 0, None),
            "trust": trust,
            "nearest_distance": nearest,
            "supports": names,
        }

    def predict(self, payload: dict) -> dict:
        mode = "hd" if payload.get("mode") == "hd" else "no_hd"
        surf = str(payload.get("surfactant_type") or self.lookup["surfactants"][0])
        nano = str(payload.get("nanoparticle_type") or self.lookup["nanoparticles"][0])
        concentration = _finite_float(payload.get("concentration_wt"), self.lookup["concentrations"][0])
        hfoam = payload.get("hfoam")
        hliquid = payload.get("hliquid")
        t_min = _finite_float(payload.get("time_min_s"), 70)
        t_max = _finite_float(payload.get("time_max_s"), 450)
        t_min, t_max = min(t_min, t_max), max(t_min, t_max)
        if abs(t_max - t_min) < 1e-9:
            t_max = t_min + 1.0
        view_window = {"time_min_s": float(t_min), "time_max_s": float(t_max)}
        times = np.linspace(self.lookup["time_min"], self.lookup["time_max"], 120)
        features = self.query_features(surf, nano, concentration, hfoam, hliquid)

        ravg = self.predict_support_curve(
            self.ravg_supports[mode],
            features,
            self.feature_columns("ravg", mode, surf),
            times,
            "Ravg_pred_known",
        )
        bc = self.predict_support_curve(
            self.bc_supports[mode],
            features,
            self.feature_columns("bc", mode, surf),
            times,
            "prediction",
        )
        return self._format_prediction(mode, surf, nano, concentration, times, bc, ravg, view_window)

    def _format_prediction(self, mode: str, surf: str, nano: str, concentration: float, times: np.ndarray, bc: dict, ravg: dict, view_window: dict) -> dict:
        bc_vals = bc["values"]
        r_vals = ravg["values"]
        bc_initial = float(np.median(bc_vals[:10]))
        bc_final = float(np.median(bc_vals[-10:]))
        r_initial = float(np.median(r_vals[:10]))
        r_final = float(np.median(r_vals[-10:]))
        bc_low = np.clip(bc_vals - bc["uncertainty"], 0, None)
        bc_high = np.clip(bc_vals + bc["uncertainty"], 0, None)
        ravg_low = np.clip(r_vals - ravg["uncertainty"], 0, None)
        ravg_high = np.clip(r_vals + ravg["uncertainty"], 0, None)
        bc_uncertainty_pct = _relative_uncertainty_pct(bc_vals, bc["uncertainty"])
        ravg_uncertainty_pct = _relative_uncertainty_pct(r_vals, ravg["uncertainty"])
        half_life = _half_life_summary(times, bc_vals, bc["uncertainty"])
        half_life_uncertainty_pct = _half_life_uncertainty_pct(half_life)
        bc_retention = bc_final / max(bc_initial, 1e-6)
        ravg_growth = (r_final - r_initial) / max(r_initial, 1e-6)
        combined_trust = float(np.sqrt(bc["trust"] * ravg["trust"]))
        window_start = float(times[0])
        window_end = float(times[-1])
        window_span = max(window_end - window_start, 1e-6)
        half_value = half_life["value_s"]
        half_lower = half_life["lower_s"]
        half_upper = half_life["upper_s"]
        if half_value is None:
            label = "Half-life not reached"
            tone = "stable"
        elif half_lower is not None and half_lower <= window_end <= (half_upper if half_upper is not None else half_value):
            label = "Half-life near curve end"
            tone = "watch"
        elif half_value <= window_start + 0.35 * window_span:
            label = "Fast destabilization"
            tone = "risk"
        else:
            label = "Half-life reached"
            tone = "risk"
        if half_value is None:
            visual = "Bubble count does not reach half of its initial value across the predicted time curve."
        elif ravg_growth > 0.25:
            visual = "Bubble count reaches half-life across the predicted curve; Ravg growth supports coalescence or coarsening."
        else:
            visual = "Bubble count reaches half-life across the predicted curve; inspect this time region for destabilization onset."
        mode_note = "HD model uses foam and liquid height context." if mode == "hd" else "No-HD model excludes HD foam/liquid features and relies on corrected material descriptors."
        return {
            "input": {"mode": mode, "surfactant_type": surf, "nanoparticle_type": nano, "concentration_wt": concentration},
            "view_window_s": {
                "time_min_s": round(float(view_window["time_min_s"]), 3),
                "time_max_s": round(float(view_window["time_max_s"]), 3),
            },
            "times_s": [round(float(x), 3) for x in times],
            "bc": {
                "values": [round(float(x), 5) for x in bc_vals],
                "uncertainty": [round(float(x), 5) for x in bc["uncertainty"]],
                "uncertainty_pct": [round(float(x), 4) for x in bc_uncertainty_pct],
                "lower": [round(float(x), 5) for x in bc_low],
                "upper": [round(float(x), 5) for x in bc_high],
                "trust": round(float(bc["trust"]), 4),
                "initial": round(bc_initial, 4),
                "final": round(bc_final, 4),
                "retention": round(float(bc_retention), 4),
                "median_uncertainty": round(float(np.median(bc["uncertainty"])), 4),
                "median_uncertainty_pct": round(float(np.median(bc_uncertainty_pct)), 4),
                "half_life": {
                    "value_s": None if half_life["value_s"] is None else round(float(half_life["value_s"]), 4),
                    "lower_s": None if half_life["lower_s"] is None else round(float(half_life["lower_s"]), 4),
                    "upper_s": None if half_life["upper_s"] is None else round(float(half_life["upper_s"]), 4),
                    "uncertainty_pct": None if half_life_uncertainty_pct is None else round(float(half_life_uncertainty_pct), 4),
                    "threshold": round(float(half_life["threshold"]), 4),
                    "status": half_life["status"],
                    "label": half_life["label"],
                },
                "supports": bc["supports"],
            },
            "ravg": {
                "values": [round(float(x), 5) for x in r_vals],
                "uncertainty": [round(float(x), 5) for x in ravg["uncertainty"]],
                "uncertainty_pct": [round(float(x), 4) for x in ravg_uncertainty_pct],
                "lower": [round(float(x), 5) for x in ravg_low],
                "upper": [round(float(x), 5) for x in ravg_high],
                "trust": round(float(ravg["trust"]), 4),
                "initial": round(r_initial, 4),
                "final": round(r_final, 4),
                "growth_fraction": round(float(ravg_growth), 4),
                "median_uncertainty": round(float(np.median(ravg["uncertainty"])), 4),
                "median_uncertainty_pct": round(float(np.median(ravg_uncertainty_pct)), 4),
                "supports": ravg["supports"],
            },
            "stability": {
                "label": label,
                "tone": tone,
                "combined_trust": round(combined_trust, 4),
                "visual_explanation": visual,
                "mode_note": mode_note,
                "science_cues": [
                    "Stable foam keeps many bubbles separated for longer.",
                    "Drainage thins bubble films; coalescence merges bubbles.",
                    "A plausible time prediction changes step by step, not by sudden jumps.",
                ],
            },
        }

    def meta(self) -> dict:
        def metrics(name: str) -> dict:
            return _metrics_map(self.models[name].get("metrics_summary", pd.DataFrame()))

        bc_hd = self.models["bc_hd"]["performance_summary"]
        bc_no_hd = self.models["bc_no_hd"]["performance_summary"]
        return {
            "surfactants": self.lookup["surfactants"],
            "nanoparticles": self.lookup["nanoparticles"],
            "concentrations": self.lookup["concentrations"],
            "ranges": self.lookup["ranges"],
            "defaults": {
                "surfactant_type": "New CoCO-Betaine" if "New CoCO-Betaine" in self.lookup["surfactants"] else self.lookup["surfactants"][0],
                "nanoparticle_type": "CeO1" if "CeO1" in self.lookup["nanoparticles"] else self.lookup["nanoparticles"][0],
                "concentration_wt": 0.0125,
                "hfoam": round(self.lookup["hfoam_median"], 3),
                "hliquid": round(self.lookup["hliquid_median"], 3),
                "time_min_s": 70,
                "time_max_s": 450,
            },
            "models": {
                "ravg_hd": {"version": self.models["ravg_hd"]["version"], "metrics": metrics("ravg_hd")},
                "ravg_no_hd": {"version": self.models["ravg_no_hd"]["version"], "metrics": metrics("ravg_no_hd")},
                "bc_hd": {"version": self.models["bc_hd"]["version"], "known": bc_hd[bc_hd["summary_level"].eq("all")].iloc[0].to_dict()},
                "bc_no_hd": {"version": self.models["bc_no_hd"]["version"], "known": bc_no_hd[bc_no_hd["summary_level"].eq("all")].iloc[0].to_dict()},
            },
            "theory": {
                "what_foam_is": "Gas bubbles separated by thin liquid films.",
                "failure_modes": ["drainage", "coalescence", "gas diffusion/coarsening", "wall breakage", "collapse patches"],
                "prediction_rule": "Good time-series predictions should change gradually and preserve physical bubble behavior.",
            },
        }


MODELS = FoamModels()


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data: dict, status=200) -> None:
        body = json.dumps(_json_safe(data), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/meta":
            self._send_json(MODELS.meta())
            return
        if parsed.path in {"/", "/index.html"}:
            self._send_file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._send_file(ROOT / "styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._send_file(ROOT / "app.js", "application/javascript; charset=utf-8")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/predict":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self._send_json(MODELS.predict(payload))
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)


def main() -> None:
    host = "127.0.0.1"
    port = int(os.environ.get("FOAM_GUI_PORT", "8786"))
    print(f"Foam property GUI: http://{host}:{port}/")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
