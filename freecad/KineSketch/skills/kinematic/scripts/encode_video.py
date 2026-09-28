"""Encode genuine FreeCAD viewport frames after the GUI process exits."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from freecad.KineSketch.utils.mechanisms.contract import (
    MechanismError, read_json, result, sha256, write_json,
)


def encode_native_video(capture_report_path: str) -> dict:
    try:
        report_path = Path(capture_report_path).expanduser().resolve()
        capture = read_json(report_path)
        if capture.get("source") != "FreeCAD GUI activeView.saveImage" or capture.get("generation_return_code") != 0:
            raise MechanismError("CAPTURE_UNVERIFIED", "Expected a successful FreeCAD viewport capture")
        frames = Path(capture["frames_dir"]).resolve()
        output = Path(capture["target_video"]).resolve()
        if frames.parent != output.parent or report_path.parent != output.parent or not frames.is_dir():
            raise MechanismError("INVALID_CAPTURE", "Frame directory must belong to this motion result")
        count = capture["video_frame_count"]
        fps = capture["fps"]
        if type(count) is not int or not 2 <= count <= 3600 or type(fps) is not int or not 1 <= fps <= 60:
            raise MechanismError("INVALID_CAPTURE", "Frame count or fps is invalid")
        for i in range(count):
            frame = frames / f"frame_{i:04d}.png"
            if not frame.is_file() or frame.stat().st_size == 0:
                raise MechanismError("FRAME_MISSING", f"Missing FreeCAD frame {i}")
        if output.exists():
            raise MechanismError("OUTPUT_EXISTS", f"Video already exists: {output}")
        video_report = output.with_name(output.stem + ".report.json")
        if video_report.exists():
            raise MechanismError("OUTPUT_EXISTS", f"Video report already exists: {video_report}")
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise MechanismError("FFMPEG_UNAVAILABLE", "ffmpeg and ffprobe are required for MP4 export", "blocked")
        temporary_video = output.with_name(output.stem + ".encoding.mp4")
        if temporary_video.exists():
            raise MechanismError("OUTPUT_EXISTS", f"Temporary video already exists: {temporary_video}")
        command = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps),
                   "-i", str(frames / "frame_%04d.png"), "-an", "-c:v", "libx264",
                   "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(temporary_video)]
        encoded = subprocess.run(command, capture_output=True, text=True, check=False)
        if encoded.returncode != 0 or not temporary_video.is_file():
            raise MechanismError("VIDEO_ENCODE_FAILED", f"ffmpeg failed: {encoded.stderr[-1000:]}", "failed")
        metadata = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=codec_name,width,height,nb_frames",
                                   "-show_entries", "format=duration", "-of", "json", str(temporary_video)],
                                  capture_output=True, text=True, check=False)
        if metadata.returncode != 0:
            raise MechanismError("VIDEO_VERIFY_FAILED", f"ffprobe failed: {metadata.stderr[-1000:]}", "failed")
        probed = json.loads(metadata.stdout)
        stream = probed["streams"][0]
        if stream["codec_name"] != "h264" or int(stream["nb_frames"]) != count or [stream["width"], stream["height"]] != capture["resolution"]:
            raise MechanismError("VIDEO_VERIFY_FAILED", "Encoded video differs from the FreeCAD capture", "failed")
        if output.exists():
            raise MechanismError("OUTPUT_EXISTS", f"Video already exists: {output}")
        temporary_video.rename(output)
        details = dict(capture)
        details.update({"status": "success", "video_path": str(output),
                        "video_sha256": sha256(output), "codec": "h264",
                        "verified_frame_count": int(stream["nb_frames"]),
                        "verified_duration_s": float(probed["format"]["duration"])})
        write_json(video_report, details)
        return result("success", artifacts={"video": str(output), "report": str(video_report)}, checks=details)
    except MechanismError as exc:
        return result(exc.status, checks=exc.checks, code=exc.code, message=str(exc))
    except Exception as exc:
        return result("failed", code="VIDEO_EXCEPTION", message=f"{type(exc).__name__}: {exc}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m freecad.KineSketch.skills.kinematic.scripts.encode_video capture.json", file=sys.stderr)
        return 2
    response = encode_native_video(sys.argv[1])
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
