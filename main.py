"""
CLI entry point -- runs the pipeline with a plain OpenCV display window.
For the interactive web dashboard instead, run dashboard_app.py.

Examples:
    python main.py --source 0                          # webcam
    python main.py --source data/sample_videos/clip.mp4 # video file
    python main.py --source rtsp://camera_ip/stream --camera-id CAM_02
"""
import argparse
import cv2

from modules.pipeline import SurveillancePipeline


def parse_args():
    parser = argparse.ArgumentParser(description="Risk-Aware Video Surveillance System")
    parser.add_argument("--source", default="0",
                         help="Webcam index (e.g. 0), video file path, or RTSP/HTTP stream URL")
    parser.add_argument("--camera-id", default="CAM_01", help="Identifier included in alert payloads")
    return parser.parse_args()


def main():
    args = parse_args()
    source = int(args.source) if args.source.isdigit() else args.source

    pipeline = SurveillancePipeline(source=source, camera_id=args.camera_id)

    for display_frame in pipeline.frames():
        cv2.imshow("Risk-Aware Surveillance", display_frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
