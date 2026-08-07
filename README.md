# Risk-Aware Video Surveillance System
### A Two-Level (Rule-Engine → AI-Verification) Risk-Aware Surveillance System with Live/Uploaded-Video Analysis, Evidence Capture, and a 4-Page Operator Dashboard

Full working implementation of the **two-level architecture**:

```
Camera / Uploaded Video
        │
        ▼
  YOLO Detection (Module 3)
        │
        ▼
  Level 1 — Rule Engine (Module 4A, NO AI)
    • Loitering > 30s        • Crowd density high
    • Restricted-zone entry  • Object abandoned
    • Running                • Fight detected
    • Person falls
        │
        ├── risk < 0.60 ──► Dashboard "warning" alert (yellow) + beep, keep monitoring
        │
        └── risk ≥ 0.60 ──► ESCALATE
                                │
                                ▼
                    Level 2 — AI Verification (Module 4B)
                        CNN (MobileNetV3) → LSTM → P_threat
                                │
                                ▼
                    Module 5 — Final Risk Score
                    (level1_weight·rule_score + level2_weight·P_threat)
                                │
                        3-consecutive-window confirm
                                │
                                ▼
                    Module 6 — Alert & Output
                    Red dashboard + siren + push notification
                                │
                                ▼
                    Module 7 — Evidence Manager
                    Saves Evidence/Images/<date>/*.jpg
                          Evidence/Videos/<date>/Incident_NN.mp4
                    + logs/evidence_log.csv (camera, risk, AI confidence, time, behaviour)
```

---

## 1. Project Structure

```
RiskAware_Surveillance_Project/
├── config.yaml                        # All tunable parameters (rules, thresholds, weights, paths)
├── requirements.txt
├── main.py                             # CLI entry point — plain OpenCV window
├── dashboard_app.py                     # 4-page web dashboard (Flask) — RECOMMENDED
│
├── modules/
│   ├── config.py                         # Config loader
│   ├── utils.py                           # Frame buffer, persistence tracker, crowd density
│   ├── module1_video_capture.py           # Module 1 — Video Capture (webcam / file / RTSP-IP camera)
│   ├── module2_preprocessing.py           # Module 2 — Preprocessing
│   ├── module3_detection_tracking.py      # Module 3 — YOLOv8 + ByteTrack (persons + watched objects)
│   ├── module4a_rule_engine.py            # Module 4A — Level-1 Rule Engine (NO AI)
│   ├── module4_behaviour_classification.py# Module 4B — CNN + LSTM AI Verification
│   ├── module5_risk_assessment.py         # Module 5 — Level-1/Level-2 risk scoring + 3-window confirm
│   ├── module6_alert_output.py            # Module 6 — beep (L1) / siren (L2), push notification, CSV log
│   ├── module7_evidence_manager.py        # Module 7 — saves incident images/clips + evidence_log.csv
│   ├── module8_video_upload_analyzer.py   # Module 8 — offline analysis of an uploaded video file
│   └── pipeline.py                        # Orchestrates Modules 1-7, shared by every entry point
│
├── dashboard/                          # 4-page web dashboard front-end
│   ├── templates/
│   │   ├── _nav.html                       # Shared top nav (Live / Upload / History / Analytics)
│   │   ├── index.html                      # Live Camera page
│   │   ├── upload.html                     # Upload Video page
│   │   ├── history.html                    # Alert History + Evidence Viewer page
│   │   └── analytics.html                  # Analytics page (totals, timeline, heat map)
│   └── static/{css,js}/                    # Styling + per-page front-end logic
│
├── training/                           # Synthetic data generator, LSTM trainer, evaluator
├── models/                             # Saved weights land here (best_lstm.pt, etc.)
├── data/
│   ├── sample_videos/                    # Drop your own test clips here
│   └── uploaded_videos/                  # Files uploaded via the dashboard's Upload page land here
├── Evidence/                            # Created at runtime — Images/<date>/, Videos/<date>/
├── logs/                                # event_log.csv (every frame) + evidence_log.csv (Module 7)
├── notifications/mock_server.py          # Mock security-personnel push-notification receiver
└── tests/test_pipeline.py                 # 21 unit tests — rule engine, two-level scoring, etc.
```

## 2. Quick Start

```bash
# 1. Create environment
python -m venv venv
source venv/bin/activate            # (venv\Scripts\activate on Windows)

# 2. Install dependencies
pip install -r requirements.txt

# 3. (First run) YOLOv8n + MobileNetV3 weights auto-download — needs internet once

# 4. Generate a synthetic dataset so you can train/test without waiting on UCF-Crime downloads
python training/generate_synthetic_data.py

# 5. Train Module 4B's LSTM classifier
python training/train_lstm.py

# 6. Evaluate
python training/evaluate.py

# 7a. Run the interactive 4-page web dashboard (recommended for demos)
python dashboard_app.py --source 0
# then open http://127.0.0.1:8000

# 7b. ...or run the plain OpenCV window instead
python main.py --source 0

# 7c. ...or point either entry point at an RTSP/IP camera instead of a webcam
python dashboard_app.py --source rtsp://192.168.1.50:554/stream --camera-id CAM_02

# 8. (Optional) start the mock notification receiver in another terminal to see
#    the JSON payloads Module 6 pushes on Level-2, AI-verified Critical alerts
python notifications/mock_server.py
```

## 3. The 4-Page Dashboard

`dashboard_app.py` runs the full two-level pipeline in a background thread and serves:

- **Live Camera (`/`)** — MJPEG feed with bounding boxes/risk labels, per-person risk bars,
  tier cards, risk-trend chart, a **Level-1 warning console** (rule-triggered, beep-only) and a
  separate **Level-2 critical console** (AI-verified, siren). A source switcher lets you swap
  between webcam index, a video file path, or an `rtsp://` IP-camera URL without restarting the app.
- **Upload Video (`/upload`)** — choose a file, upload it, and it's analyzed offline through the
  exact same Module 1-7 pipeline (Module 8). Shows a progress bar, then a summary + a
  video-time-ordered timeline of every Level-1 warning and Level-2 AI-verified alert found.
- **Alert History (`/history`)** — every Level-2, AI-verified incident Module 7 saved, with date,
  time, camera, behaviour class, risk score, AI confidence, and the triggered rule(s). Click
  **View** to open the **Evidence Viewer** (saved snapshot image + recorded video clip). Operators
  can flag any row **Mark False Positive** to support the false-positive-rate analytic.
- **Analytics (`/analytics`)** — Total Alerts Today, Total AI-Verified Alerts, False-Positive Rate,
  Average Risk, an hour-of-day alert timeline chart, and a Camera × Hour heat map.

No extra setup needed beyond the base `pip install -r requirements.txt` — Flask is already included.

## 4. Level-1 Rule Engine (Module 4A)

Pure, deterministic checks over YOLO+ByteTrack detections — **no neural network** runs here.
Each rule independently contributes a score in `[0, 1]`; the Level-1 risk score is the `MAX` of
every triggered rule (a single strong rule is enough to request AI verification):

| Rule | Trigger condition | Config key |
|---|---|---|
| Loitering | centroid stays within a small radius for > 30s | `rule_engine.loitering` |
| Restricted Zone | centroid enters a configured polygon | `rule_engine.restricted_zone` |
| Running | centroid speed exceeds a px/sec threshold | `rule_engine.running` |
| Crowd Density | too many persons simultaneously in frame | `rule_engine.crowd_density` |
| Abandoned Object | a bag/suitcase/backpack is stationary with no person nearby | `rule_engine.abandoned_object` |
| Fight Detection | two tracks close together, moving rapidly relative to each other | `rule_engine.fight_detection` |
| Fall Detection | bbox aspect ratio flips from "tall" to "wide" quickly | `rule_engine.fall_detection` |

Every rule can be toggled/tuned independently in `config.yaml`.

## 5. Two-Level Risk Scoring (Module 5)

```
LEVEL 1 (rule-based):  risk_score = MAX(triggered rule scores)
    risk_score <  level1_ai_verification_threshold (0.60) → "warning" tier, beep, no AI
    risk_score >= level1_ai_verification_threshold        → escalate to LEVEL 2

LEVEL 2 (AI verification, escalated tracks only):
    final_score = level1_weight · risk_score + level2_weight · P_threat
    "confirmed" once P_threat clears the AI threshold for `confirmation_windows`
    consecutive frames → "critical" tier → siren + evidence save + push notification
```

The original single-formula spec (`R = alpha·P_threat + beta·D_persistence + gamma·C_density`)
is retained as `RiskAssessmentModule.compute()` for backward compatibility / unit tests.

## 6. Evidence Manager (Module 7)

On every confirmed, AI-verified critical alert:

```
Evidence/
├── Images/<YYYY-MM-DD>/<HH-MM-SS>.jpg
└── Videos/<YYYY-MM-DD>/Incident_NN.mp4
```

plus a row in `logs/evidence_log.csv` with camera ID, risk score, AI confidence, timestamp,
behaviour class, and the saved file paths — this is exactly what powers the dashboard's Alert
History, Evidence Viewer, and Analytics pages.

## 7. Video Upload Analyzer (Module 8)

`modules/module8_video_upload_analyzer.py` runs a background thread per upload, feeding the
uploaded file through `SurveillancePipeline(..., realtime=False)` (no real-time throttling — reads
as fast as the file decodes), collecting every Level-1/Level-2 event into a video-time-ordered
timeline, and writing incident evidence through the same Module 7 as the live camera path.

## 8. Config

All rule thresholds, two-level weights, alert sounds, and evidence settings live in `config.yaml`:

```yaml
level1_ai_verification_threshold: 0.60

risk_weights:
  level1_weight: 0.4   # weight on the rule-engine (Level-1) score
  level2_weight: 0.6   # weight on P_threat, the AI verifier's confidence

alerts:
  level1_beep_frequency_hz: 700     # Level-1 warning — short soft beep
  siren_frequency_hz: 1000          # Level-2 critical — full siren

evidence:
  clip_pre_event_seconds: 3
  clip_post_event_seconds: 4
```

## 9. Notes for Your Viva / Demo

- If you don't have a GPU, `config.yaml` → `device: cpu` still works, just slower.
- Audio (Module 6) uses your machine's default speaker via `simpleaudio`; both the Level-1 beep
  and Level-2 siren are synthesised tones in code — no external audio files needed.
- `notifications/mock_server.py` is a tiny Flask server that just prints/logs any JSON payload it
  receives — use it to demonstrate the "push notification to security personnel" feature live.
- The 3-consecutive-window AI confirmation rule means a single noisy frame won't trigger a false
  alarm — P_threat must clear the AI threshold for 3 windows in a row before Module 6 fires the
  siren and Module 7 saves evidence.
- RTSP/IP camera support: `modules/module1_video_capture.py` accepts any OpenCV-readable source
  string (webcam index, file path, `rtsp://`/`http://` stream URL) — use the Live page's source
  switcher, or `--source rtsp://...` on the CLI.
- Because no labelled UCF-Crime subset is bundled (dataset licensing/size), the synthetic
  generator lets you demonstrate a full working, trainable pipeline end-to-end. Swap in real
  extracted features (see `training/dataset.py`) before your final reported results/demo.
