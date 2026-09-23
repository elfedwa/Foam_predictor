from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
MODEL_ROOT = ROOT / "models"
ATLAS_ROOT = ROOT / "atlas"
CHAMPION_MANIFEST = MODEL_ROOT / "PINNED_CHAMPION.json"
SUMMARY_PATH = MODEL_ROOT / "level5_v2_branch_holdout_summary.csv"
PREDICTIONS_PATH = MODEL_ROOT / "level5_v2_all_predictions.csv"

TARGET_NAMES = [
    "network_regime",
    "bubble_count",
    "bubble_radius_px",
    "wall_fraction",
    "wall_thickness_px",
]

BRANCH_SPECS = {
    "New CoCO-Betaine": {
        "MnO": {
            "descriptor": 0.3300,
            "kind": "descriptor",
            "model": MODEL_ROOT / "coco_mno.npz",
        },
        "FeOOH nanorods": {
            "descriptor": 0.3715,
            "kind": "descriptor",
            "model": MODEL_ROOT / "coco_feooh.npz",
        },
        "MgOH": {
            "descriptor": 0.2880,
            "kind": "descriptor",
            "model": MODEL_ROOT / "coco_mgoh.npz",
        },
        "HBN": {
            "descriptor": 0.3180,
            "kind": "descriptor",
            "model": MODEL_ROOT / "coco_hbn.npz",
        },
    },
    "Bioterg": {
        "SiO2": {
            "descriptor": 0.2940,
            "kind": "descriptor",
            "model": MODEL_ROOT / "bioterg_sio2.npz",
        },
        "MnO2": {
            "descriptor": 0.316667,
            "kind": "descriptor",
            "model": MODEL_ROOT / "bioterg_mno2.npz",
        },
        "FeOOH nanorods": {
            "descriptor": 0.3715,
            "kind": "exact_parent",
            "model": MODEL_ROOT / "bioterg_feooh.npz",
        },
    },
}


def finite_float(value, fallback=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return result if math.isfinite(result) else float(fallback)


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    return value


def polynomial_basis(raw, mean, sd):
    z = (raw - mean) / sd
    parts = [np.ones((len(z), 1)), z, z**2]
    for left in range(z.shape[1]):
        for right in range(left + 1, z.shape[1]):
            parts.append((z[:, left] * z[:, right])[:, None])
    return np.hstack(parts)


def mode_basis(raw, mean, sd):
    z = np.clip(np.nan_to_num((raw - mean) / sd, nan=0.0, posinf=6.0, neginf=-6.0), -6.0, 6.0)
    parts = [np.ones((len(z), 1)), z, z**2]
    for left in range(z.shape[1]):
        for right in range(left + 1, z.shape[1]):
            parts.append((z[:, left] * z[:, right])[:, None])
    return np.hstack(parts)


def boundary_regime(config, time_s, concentration):
    logc = math.log(max(concentration, 1e-8))
    kind = config["kind"]
    if kind == "boundary_linear":
        coefficients = np.asarray(config["coefficients"], dtype=float)
        log_threshold = coefficients[0] + coefficients[1] * logc
    elif kind == "boundary_quadratic":
        coefficients = np.asarray(config["coefficients"], dtype=float)
        log_threshold = coefficients[0] + coefficients[1] * logc + coefficients[2] * logc**2
    else:
        log_threshold = np.interp(
            logc,
            np.asarray(config["log_concentration"], dtype=float),
            np.asarray(config["log1p_boundary_time"], dtype=float),
        )
    threshold = math.expm1(float(np.clip(log_threshold, 0.0, 10.0)))
    return float(time_s >= threshold), threshold


class BranchRuntime:
    def __init__(self, surfactant, nanoparticle, spec, summary_row, calibration):
        self.surfactant = surfactant
        self.nanoparticle = nanoparticle
        self.branch = f"{surfactant} | {nanoparticle}"
        self.spec = spec
        self.summary = {str(key): json_safe(value) for key, value in summary_row.items()}
        self.calibration = np.asarray(calibration, dtype=float)
        archive = np.load(spec["model"], allow_pickle=True)
        self.input_mean = archive["input_mean"].astype(float)
        self.input_sd = np.maximum(archive["input_sd"].astype(float), 1e-6)
        self.target_mean = archive["target_mean"].astype(float)
        self.target_sd = np.maximum(archive["target_sd"].astype(float), 1e-6)
        self.target_lo = archive["target_lo"].astype(float)
        self.target_hi = archive["target_hi"].astype(float)
        self.coefficients = archive["coefficients"].astype(float)
        self.router = json.loads(str(archive["router"][0]))
        self.atlas_inputs = archive["atlas_inputs"].astype(float)
        self.atlas_morphology = archive["atlas_morphology"].astype(float)
        self.atlas_paths = [str(path) for path in archive["atlas_image_paths"]]
        if "atlas_nanoparticle" in archive.files:
            self.atlas_nanoparticles = [str(value) for value in archive["atlas_nanoparticle"]]
        else:
            self.atlas_nanoparticles = ["FeOOH nanorods"] * len(self.atlas_paths)
        if "atlas_concentration" in archive.files:
            self.atlas_concentrations = archive["atlas_concentration"].astype(float)
        else:
            self.atlas_concentrations = np.exp(self.atlas_inputs[:, 2])
        self.atlas_z = (self.atlas_inputs - self.input_mean) / self.input_sd
        self.atlas_morph_z = (self.atlas_morphology - self.target_mean) / self.target_sd

    def raw_input(self, time_s, hfoam, concentration):
        values = [math.log1p(max(time_s, 0.0)), hfoam, math.log(max(concentration, 1e-8))]
        if self.spec["kind"] == "descriptor":
            values.append(float(self.spec["descriptor"]))
        return np.asarray([values], dtype=float)

    def predict_morphology(self, raw):
        prediction = np.einsum(
            "ij,jk->ik",
            polynomial_basis(raw, self.input_mean, self.input_sd),
            self.coefficients,
        )
        prediction = prediction * self.target_sd + self.target_mean
        prediction = np.clip(prediction, self.target_lo, self.target_hi)
        prediction[:, 0] = np.clip(prediction[:, 0], 0.0, 1.0)
        prediction[:, 1:] = np.maximum(prediction[:, 1:], 0.0)
        prediction[:, 3] = np.clip(prediction[:, 3], 0.0, 1.0)
        return prediction

    def predict_regime(self, raw, time_s, concentration):
        router = self.router
        if self.spec["kind"] == "exact_parent":
            regime, threshold = boundary_regime(router, time_s, concentration)
            return regime, threshold, router["kind"]

        if router["routing"] == "nearest_supported_boundary":
            regime, threshold = boundary_regime(router["classifier"], time_s, concentration)
            return regime, threshold, router["classifier"]["kind"]

        classifier = router["classifier"]
        if classifier["kind"] == "knn":
            query_z = (raw - self.input_mean) / self.input_sd
            weights = np.asarray([1.0, 1.0, 0.35, float(classifier["descriptor_weight"])])
            distances = np.sqrt(np.mean(((query_z[:, None, :] - self.atlas_z[None, :, :]) * weights) ** 2, axis=2))[0]
            nearest = np.argsort(distances)[: min(int(classifier["neighbors"]), len(distances))]
            votes = 1.0 / np.maximum(distances[nearest], 1e-4)
            probability = float(np.sum(votes * self.atlas_morphology[nearest, 0]) / np.sum(votes))
            return float(probability >= 0.5), None, "knn"

        score = float(
            np.einsum(
                "ij,j->i",
                mode_basis(raw, self.input_mean, self.input_sd),
                np.asarray(classifier["coefficients"], dtype=float),
            )[0]
        )
        return float(score >= float(classifier["threshold"])), None, "balanced_ridge"

    def prototype_distances(self, raw, predicted):
        query_z = (raw - self.input_mean) / self.input_sd
        if self.spec["kind"] == "descriptor":
            weights = np.asarray([1.0, 1.0, 0.35, 1.0])
        else:
            weights = np.ones(raw.shape[1], dtype=float)
        input_distance = np.sqrt(np.mean(((query_z[:, None, :] - self.atlas_z[None, :, :]) * weights) ** 2, axis=2))
        query_morph = (predicted - self.target_mean) / self.target_sd
        morphology_distance = np.sqrt(np.mean((query_morph[:, None, :] - self.atlas_morph_z[None, :, :]) ** 2, axis=2))
        mismatch = (predicted[:, 0, None] >= 0.5) != (self.atlas_morphology[None, :, 0] >= 0.5)
        return (0.30 * input_distance + 0.70 * morphology_distance + 2.0 * mismatch)[0]

    def decode_atlas_input(self, index):
        raw = self.atlas_inputs[index]
        return {
            "time_s": float(np.expm1(raw[0])),
            "hfoam": float(raw[1]),
            "concentration_wt": float(np.exp(raw[2])),
        }

    def predict(self, time_s, hfoam, concentration, token_for_path):
        raw = self.raw_input(time_s, hfoam, concentration)
        predicted = self.predict_morphology(raw)
        regime, transition_time, classifier = self.predict_regime(raw, time_s, concentration)
        predicted[:, 0] = regime
        if regime < 0.5:
            predicted[:, 3:5] = 0.0
        distances = self.prototype_distances(raw, predicted)
        order = np.argsort(distances)[:5]
        selected = int(order[0])
        distance = float(distances[selected])
        if len(self.calibration):
            support_confidence = float(np.clip(1.0 - np.mean(self.calibration <= distance), 0.0, 1.0))
        else:
            support_confidence = 0.5
        supported_accuracy = finite_float(self.summary.get("supported_balanced_regime_accuracy"), 0.0)
        combined_trust = float(np.sqrt(max(support_confidence, 0.01) * max(supported_accuracy, 0.01)))
        trust_label = "High" if combined_trust >= 0.75 else "Moderate" if combined_trust >= 0.45 else "Low"
        ensemble = self.atlas_morphology[order]
        uncertainty = np.std(ensemble, axis=0, ddof=1) if len(order) > 1 else np.zeros(5)
        alternatives = []
        for index in order:
            decoded = self.decode_atlas_input(int(index))
            alternatives.append(
                {
                    "image_url": f"/api/frame?id={token_for_path(self.atlas_paths[int(index)])}",
                    "nanoparticle": self.atlas_nanoparticles[int(index)],
                    "concentration_wt": float(self.atlas_concentrations[int(index)]),
                    "time_s": decoded["time_s"],
                    "hfoam": decoded["hfoam"],
                    "distance": float(distances[int(index)]),
                }
            )
        morphology = {
            name: {"value": float(predicted[0, idx]), "support_sd": float(uncertainty[idx])}
            for idx, name in enumerate(TARGET_NAMES)
        }
        return {
            "branch": self.branch,
            "input": {
                "surfactant_type": self.surfactant,
                "nanoparticle_type": self.nanoparticle,
                "concentration_wt": concentration,
                "time_s": time_s,
                "hfoam": hfoam,
            },
            "reconstruction": {
                "image_url": alternatives[0]["image_url"],
                "alternatives": alternatives,
                "morphology": morphology,
                "regime": "wall network" if regime >= 0.5 else "bright droplets",
                "classifier": classifier,
                "transition_time_s": transition_time,
                "prototype_distance": distance,
                "support_confidence": support_confidence,
                "combined_trust": combined_trust,
                "trust_label": trust_label,
            },
            "validation": {
                "selected_expert": self.summary.get("selected_expert"),
                "SSIM": finite_float(self.summary.get("SSIM")),
                "image_MAE": finite_float(self.summary.get("image_MAE")),
                "balanced_regime_accuracy": finite_float(self.summary.get("balanced_regime_accuracy")),
                "supported_balanced_regime_accuracy": supported_accuracy,
                "n_test_frames": int(finite_float(self.summary.get("n_test_frames"))),
            },
        }


class ReconstructionModels:
    def __init__(self):
        self.summary = pd.read_csv(SUMMARY_PATH)
        self.predictions = pd.read_csv(PREDICTIONS_PATH)
        self.manifest = json.loads(CHAMPION_MANIFEST.read_text())
        self.path_tokens = {}
        self.token_paths = {}
        self.runtimes = {}
        for surfactant, nanoparticles in BRANCH_SPECS.items():
            for nanoparticle, spec in nanoparticles.items():
                branch = f"{surfactant} | {nanoparticle}"
                summary_row = self.summary[self.summary["heldout_branch"] == branch].iloc[0].to_dict()
                calibration = self.predictions.loc[
                    self.predictions["heldout_branch"] == branch, "prototype_distance"
                ].to_numpy(float)
                runtime = BranchRuntime(surfactant, nanoparticle, spec, summary_row, calibration)
                self.runtimes[(surfactant, nanoparticle)] = runtime
                for path in runtime.atlas_paths:
                    self.token_for_path(path)

    def token_for_path(self, path):
        if path in self.path_tokens:
            return self.path_tokens[path]
        token = hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]
        existing = self.token_paths.get(token)
        if existing is not None and existing != path:
            raise RuntimeError("Frame token collision")
        self.path_tokens[path] = token
        self.token_paths[token] = path
        return token

    def branch_ranges(self, surfactant, nanoparticle):
        branch = f"{surfactant} | {nanoparticle}"
        table = self.predictions[self.predictions["heldout_branch"] == branch]
        preferred = table[np.isclose(table["concentration_wt"], 0.0125)]
        default_pool = preferred if not preferred.empty else table
        default_time = float(np.clip(300.0, table["time_s"].min(), table["time_s"].max()))
        nearest_rows = default_pool.loc[
            default_pool.groupby("repeat_label")["time_s"].apply(
                lambda values: (values - default_time).abs().idxmin()
            )
        ]
        default_hfoam = float(nearest_rows["measured_hfoam"].median())
        return {
            "concentration_wt": {"min": 0.00625, "max": 0.025, "step": 0.000001},
            "time_s": {
                "min": float(table["time_s"].min()),
                "max": float(table["time_s"].max()),
                "step": 1.0,
            },
            "hfoam": {
                "min": float(table["measured_hfoam"].min()),
                "max": float(table["measured_hfoam"].max()),
                "step": 0.1,
            },
            "defaults": {
                "concentration_wt": 0.0125,
                "time_s": default_time,
                "hfoam": default_hfoam,
            },
        }

    def meta(self):
        ranges = {
            surfactant: {
                nanoparticle: self.branch_ranges(surfactant, nanoparticle)
                for nanoparticle in nanoparticles
            }
            for surfactant, nanoparticles in BRANCH_SPECS.items()
        }
        return {
            "surfactants": list(BRANCH_SPECS),
            "nanoparticles_by_surfactant": {
                surfactant: list(nanoparticles) for surfactant, nanoparticles in BRANCH_SPECS.items()
            },
            "ranges": ranges,
            "defaults": {
                "surfactant_type": "New CoCO-Betaine",
                "nanoparticle_type": "MnO",
            },
            "champion": self.manifest,
        }

    def predict(self, payload):
        surfactant = str(payload.get("surfactant_type") or "New CoCO-Betaine")
        available = BRANCH_SPECS.get(surfactant)
        if not available:
            raise ValueError("Unsupported surfactant")
        nanoparticle = str(payload.get("nanoparticle_type") or next(iter(available)))
        if nanoparticle not in available:
            raise ValueError("Unsupported surfactant-nanoparticle combination")
        ranges = self.branch_ranges(surfactant, nanoparticle)
        concentration = finite_float(payload.get("concentration_wt"), ranges["defaults"]["concentration_wt"])
        time_s = finite_float(payload.get("time_s"), ranges["defaults"]["time_s"])
        hfoam = finite_float(payload.get("hfoam"), ranges["defaults"]["hfoam"])
        concentration = float(np.clip(concentration, ranges["concentration_wt"]["min"], ranges["concentration_wt"]["max"]))
        time_s = float(np.clip(time_s, ranges["time_s"]["min"], ranges["time_s"]["max"]))
        hfoam = float(np.clip(hfoam, ranges["hfoam"]["min"], ranges["hfoam"]["max"]))
        return self.runtimes[(surfactant, nanoparticle)].predict(
            time_s,
            hfoam,
            concentration,
            self.token_for_path,
        )

    def frame_path(self, token):
        bundled = ATLAS_ROOT / f"{token}.webp"
        if bundled.is_file():
            return bundled
        path = self.token_paths.get(token)
        if path is None:
            return None
        candidate = Path(path)
        if candidate.is_file():
            return candidate
        external_root = os.environ.get("BUBBLE_ATLAS_ROOT")
        if external_root and "Clean Data for Dr. Fadwa" in candidate.parts:
            marker = candidate.parts.index("Clean Data for Dr. Fadwa")
            remapped = Path(external_root).expanduser() / Path(*candidate.parts[marker + 1 :])
            if remapped.is_file():
                return remapped
        return None


MODELS = ReconstructionModels()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(json_safe(data), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/meta":
            self.send_json(MODELS.meta())
            return
        if parsed.path == "/api/frame":
            token = parse_qs(parsed.query).get("id", [""])[0]
            path = MODELS.frame_path(token)
            if path is None:
                self.send_error(404)
                return
            self.send_file(path, mimetypes.guess_type(path.name)[0] or "image/png")
            return
        static = {
            "/": (ROOT / "index.html", "text/html; charset=utf-8"),
            "/index.html": (ROOT / "index.html", "text/html; charset=utf-8"),
            "/styles.css": (ROOT / "styles.css", "text/css; charset=utf-8"),
            "/app.js": (ROOT / "app.js", "application/javascript; charset=utf-8"),
        }
        if parsed.path in static:
            self.send_file(*static[parsed.path])
            return
        self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/predict":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self.send_json(MODELS.predict(payload))
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def log_message(self, format_string, *args):
        if self.path.startswith("/api/frame"):
            return
        super().log_message(format_string, *args)


def main():
    host = "127.0.0.1"
    port = int(os.environ.get("BUBBLE_RECON_PORT", "8788"))
    print(f"Bubble image reconstruction GUI: http://{host}:{port}/", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
