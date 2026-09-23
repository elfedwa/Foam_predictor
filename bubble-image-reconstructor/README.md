# Bubble Image Reconstructor

Self-contained deployment of the pinned support-aware Level 5 v2 bubble-image reconstruction model.

The application predicts bubble morphology from formulation and height measurements, then selects the closest validated microscopy prototype. The repository includes a compact 960 px WebP atlas containing every prototype referenced by the deployed model. The original 4.84 GB raw image library is not required for normal inference.

## Inputs

- surfactant
- nanoparticle
- concentration in wt.%
- elapsed time in seconds
- measured foam height

## Run

```bash
cd bubble-image-reconstructor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open `http://127.0.0.1:8788/`.

Set a different port with `BUBBLE_RECON_PORT`. If the bundled atlas is removed, an external full-resolution atlas can be supplied with `BUBBLE_ATLAS_ROOT` pointing to the `Clean Data for Dr. Fadwa` directory.

## Included runtime artifacts

- seven compact branch model archives
- Level 5 v2 champion manifest and branch validation summary
- full calibration table used for branch-specific support confidence
- 3,500 hashed deployment frames at 960 px width

The original training videos, raw microscopy frames, intermediate experiments, and model-building notebooks are not included.

## Validation

- weighted SSIM: `0.504`
- image MAE: `22.46`
- balanced regime accuracy: `0.949`
- supported balanced regime accuracy: `0.984`

The complete target composition was excluded in each reported branch-holdout test. No target frame is supplied at prediction time.
