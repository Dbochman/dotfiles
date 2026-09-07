#!/usr/bin/env python3
"""Reproducible review and frame extraction for long splat-capture videos."""

from __future__ import annotations

import csv
from fractions import Fraction
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable


VIDEO_SUFFIXES = frozenset((".avi", ".m4v", ".mkv", ".mov", ".mp4"))
ID_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
TIMECODE_RE = re.compile(r"\A(?:(\d+):)?([0-5]?\d):([0-5]?\d(?:\.\d{1,3})?)\Z")
MAX_SOURCES = 16
MAX_SEGMENTS = 100
MAX_OUTPUT_FRAMES = 100_000
MAX_REVIEW_THUMBNAILS = 2_000


class VideoPrepError(RuntimeError):
    """A bounded video-preparation error safe to show to the caller."""


def find_tool(env_name: str, executable: str, preferred: str) -> str:
    configured = os.environ.get(env_name)
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise VideoPrepError(f"configured {executable} executable is unavailable")
    preferred_path = Path(preferred)
    if preferred_path.is_file() and os.access(preferred_path, os.X_OK):
        return str(preferred_path)
    discovered = shutil.which(executable)
    if discovered:
        return discovered
    user_tool = Path.home() / ".local/bin" / executable
    if user_tool.is_file() and os.access(user_tool, os.X_OK):
        return str(user_tool)
    raise VideoPrepError(f"{executable} is unavailable")


def ffmpeg() -> str:
    return find_tool("SPLAT_FFMPEG", "ffmpeg", "/opt/homebrew/bin/ffmpeg")


def ffprobe() -> str:
    return find_tool("SPLAT_FFPROBE", "ffprobe", "/opt/homebrew/bin/ffprobe")


def scenedetect() -> str:
    return find_tool("SPLAT_SCENEDETECT", "scenedetect", str(Path.home() / ".local/bin/scenedetect"))


def run_checked(command: list[str], label: str, *, timeout: int = 24 * 60 * 60) -> None:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VideoPrepError(f"{label} failed") from error
    if completed.returncode != 0:
        raise VideoPrepError(f"{label} failed")


def regular_video(value: str) -> Path:
    candidate = Path(value).expanduser()
    candidate = candidate.resolve()
    if not candidate.is_file() or candidate.suffix.casefold() not in VIDEO_SUFFIXES:
        raise VideoPrepError("source must be a regular supported video file")
    return candidate


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_rate(value: object) -> float | None:
    if not isinstance(value, str) or value in ("", "0/0"):
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def probe_video(source: Path, *, include_hash: bool = False) -> dict[str, object]:
    command = [
        ffprobe(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration,format_name,bit_rate:stream=codec_name,width,height,avg_frame_rate,r_frame_rate,pix_fmt:stream_tags=rotate:stream_side_data=rotation",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VideoPrepError("video probe failed") from error
    if completed.returncode != 0:
        raise VideoPrepError("video probe failed")
    try:
        raw = json.loads(completed.stdout)
        stream = raw["streams"][0]
        format_data = raw["format"]
        duration = float(format_data["duration"])
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise VideoPrepError("video probe returned incomplete metadata") from error
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        raise VideoPrepError("video metadata is invalid")
    frame_rate = parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate"))
    rotation = 0
    tags = stream.get("tags")
    if isinstance(tags, dict):
        try:
            rotation = int(tags.get("rotate", 0))
        except (TypeError, ValueError):
            rotation = 0
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict) and "rotation" in item:
                try:
                    rotation = int(item["rotation"])
                except (TypeError, ValueError):
                    pass
    result: dict[str, object] = {
        "source": str(source),
        "fileName": source.name,
        "sizeBytes": source.stat().st_size,
        "durationSeconds": round(duration, 3),
        "codec": stream.get("codec_name"),
        "format": format_data.get("format_name"),
        "width": width,
        "height": height,
        "rotationDegrees": rotation,
        "frameRate": round(frame_rate, 6) if frame_rate else None,
        "pixelFormat": stream.get("pix_fmt"),
    }
    if include_hash:
        result["sha256"] = hash_file(source)
    return result


def prepare_output(value: str, label: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise VideoPrepError(f"{label} must not be a symbolic link")
    output = candidate.resolve()
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise VideoPrepError(f"{label} must be a new or empty directory")
    else:
        output.mkdir(mode=0o700, parents=True)
    return output


def seconds_to_timecode(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def timecode_to_seconds(value: object, label: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    elif isinstance(value, str):
        match = TIMECODE_RE.fullmatch(value)
        if not match:
            raise VideoPrepError(f"{label} must be seconds or HH:MM:SS.mmm")
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = hours * 3600 + minutes * 60 + float(match.group(3))
    else:
        raise VideoPrepError(f"{label} must be seconds or HH:MM:SS.mmm")
    if not math.isfinite(seconds) or seconds < 0:
        raise VideoPrepError(f"{label} is outside the video")
    return seconds


def create_thumbnail(source: Path, target: Path, timestamp: float) -> None:
    run_checked(
        [
            ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(640,iw)':-2",
            "-q:v",
            "3",
            "-an",
            "-map_metadata",
            "-1",
            "-n",
            str(target),
        ],
        "review thumbnail extraction",
    )


def create_proxy(source: Path, target: Path) -> None:
    run_checked(
        [
            ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-vf",
            "scale=-2:720",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            "-an",
            "-n",
            str(target),
        ],
        "review proxy creation",
    )


def create_scene_suggestions(source: Path, output: Path) -> list[str]:
    output.mkdir(mode=0o700)
    run_checked(
        [
            scenedetect(),
            "-i",
            str(source),
            "-o",
            str(output),
            "detect-adaptive",
            "list-scenes",
        ],
        "scene suggestion generation",
    )
    return sorted(path.name for path in output.glob("*.csv") if path.is_file())


def review_videos(
    source_values: Iterable[str],
    output_value: str,
    *,
    interval_seconds: float,
    proxy: bool,
    scene_suggestions: bool,
) -> dict[str, object]:
    if not 5 <= interval_seconds <= 600:
        raise VideoPrepError("review interval must be between 5 and 600 seconds")
    sources = [regular_video(value) for value in source_values]
    if not sources or len(sources) > MAX_SOURCES:
        raise VideoPrepError("video review requires between 1 and 16 sources")
    if len(set(sources)) != len(sources):
        raise VideoPrepError("video review sources must be unique")
    output = prepare_output(output_value, "review output")
    reviewed: list[dict[str, object]] = []
    template_videos: list[dict[str, object]] = []
    html_sections: list[str] = []

    for index, source in enumerate(sources, start=1):
        video_id = f"video-{index:02d}"
        video_dir = output / video_id
        thumbnails = video_dir / "thumbnails"
        thumbnails.mkdir(mode=0o700, parents=True)
        metadata = probe_video(source, include_hash=True)
        duration = float(metadata["durationSeconds"])
        thumbnail_count = math.ceil(duration / interval_seconds)
        if thumbnail_count > MAX_REVIEW_THUMBNAILS:
            raise VideoPrepError("review would create too many thumbnails; increase the interval")
        timestamps = [number * interval_seconds for number in range(thumbnail_count)]
        thumbnail_rows: list[dict[str, object]] = []
        cards: list[str] = []
        for number, timestamp in enumerate(timestamps, start=1):
            if timestamp >= duration:
                break
            filename = f"thumb-{number:05d}.jpg"
            create_thumbnail(source, thumbnails / filename, timestamp)
            timecode = seconds_to_timecode(timestamp)
            thumbnail_rows.append({"file": f"{video_id}/thumbnails/{filename}", "seconds": timestamp, "timecode": timecode})
            cards.append(
                f'<figure><img loading="lazy" src="{html.escape(video_id)}/thumbnails/{html.escape(filename)}" '
                f'alt="{html.escape(video_id)} at {html.escape(timecode)}"><figcaption>{html.escape(timecode)}</figcaption></figure>'
            )
        proxy_file: str | None = None
        if proxy:
            proxy_file = f"{video_id}/review-proxy.mp4"
            create_proxy(source, output / proxy_file)
        scene_files: list[str] = []
        if scene_suggestions:
            scene_source = output / proxy_file if proxy_file else source
            scene_files = create_scene_suggestions(scene_source, video_dir / "scene-suggestions")
        entry = {
            "id": video_id,
            "metadata": metadata,
            "thumbnails": thumbnail_rows,
            "proxy": proxy_file,
            "sceneSuggestionFiles": [f"{video_id}/scene-suggestions/{name}" for name in scene_files],
        }
        reviewed.append(entry)
        template_videos.append(
            {
                "id": video_id,
                "source": str(source),
                "sha256": metadata["sha256"],
                "durationSeconds": metadata["durationSeconds"],
                "segments": [],
            }
        )
        proxy_link = f'<p><a href="{html.escape(proxy_file)}">Scrub the 720p review proxy</a></p>' if proxy_file else ""
        html_sections.append(
            f"<section><h2>{html.escape(video_id)} · {html.escape(source.name)}</h2>{proxy_link}"
            f'<div class="grid">{"".join(cards)}</div></section>'
        )

    review_payload = {
        "version": 1,
        "intervalSeconds": interval_seconds,
        "videos": reviewed,
    }
    template = {
        "version": 1,
        "defaultFps": 3.0,
        "maxWidth": 0,
        "includeClips": True,
        "videos": template_videos,
    }
    (output / "review.json").write_text(json.dumps(review_payload, indent=2) + "\n", encoding="utf-8")
    (output / "segments.template.json").write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Splat footage review</title><style>
body{{font:16px system-ui,sans-serif;margin:24px;background:#111;color:#eee}}a{{color:#8dc8ff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
figure{{margin:0;background:#1d1d1d;padding:8px;border-radius:8px}}img{{display:block;width:100%;height:auto}}
figcaption{{font-variant-numeric:tabular-nums;margin-top:6px}}section{{margin-bottom:36px}}
</style></head><body><h1>Splat footage review</h1>
<p>Use the displayed timecodes to populate <code>segments.template.json</code>. Scene suggestions are boundaries, not semantic labels.</p>
{"".join(html_sections)}</body></html>"""
    (output / "index.html").write_text(page, encoding="utf-8")
    return {
        "ok": True,
        "output": str(output),
        "videoCount": len(reviewed),
        "thumbnailCount": sum(len(item["thumbnails"]) for item in reviewed),
        "proxyCreated": proxy,
        "sceneSuggestionsCreated": scene_suggestions,
        "manifestTemplate": str(output / "segments.template.json"),
        "reviewPage": str(output / "index.html"),
    }


def load_manifest(value: str) -> tuple[Path, dict[str, object]]:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise VideoPrepError("segment manifest must be a regular JSON file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VideoPrepError("segment manifest is invalid JSON") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise VideoPrepError("segment manifest version must be 1")
    return path, payload


def numeric_setting(payload: dict[str, object], name: str, *, minimum: float, maximum: float) -> float:
    value = payload.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise VideoPrepError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise VideoPrepError(f"{name} is outside the allowed range")
    return result


def validate_manifest(payload: dict[str, object]) -> tuple[list[dict[str, object]], int]:
    default_fps = numeric_setting(payload, "defaultFps", minimum=0.25, maximum=10.0)
    max_width_raw = payload.get("maxWidth", 0)
    if not isinstance(max_width_raw, int) or isinstance(max_width_raw, bool) or max_width_raw < 0 or max_width_raw > 8192:
        raise VideoPrepError("maxWidth must be 0 or an integer no greater than 8192")
    if 0 < max_width_raw < 640:
        raise VideoPrepError("nonzero maxWidth must be at least 640")
    include_clips = payload.get("includeClips", True)
    if not isinstance(include_clips, bool):
        raise VideoPrepError("includeClips must be true or false")
    videos_raw = payload.get("videos")
    if not isinstance(videos_raw, list) or not 1 <= len(videos_raw) <= MAX_SOURCES:
        raise VideoPrepError("manifest requires between 1 and 16 videos")
    source_ids: set[str] = set()
    segment_names: set[str] = set()
    validated: list[dict[str, object]] = []
    estimated_frames = 0
    for video_raw in videos_raw:
        if not isinstance(video_raw, dict):
            raise VideoPrepError("each video entry must be an object")
        video_id = video_raw.get("id")
        if not isinstance(video_id, str) or not ID_RE.fullmatch(video_id) or video_id in source_ids:
            raise VideoPrepError("video IDs must be unique lowercase names")
        source_ids.add(video_id)
        source_raw = video_raw.get("source")
        if not isinstance(source_raw, str):
            raise VideoPrepError("video source must be a path")
        source = regular_video(source_raw)
        expected_hash = video_raw.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise VideoPrepError("video SHA-256 is missing or invalid")
        metadata = probe_video(source)
        duration = float(metadata["durationSeconds"])
        segments_raw = video_raw.get("segments")
        if not isinstance(segments_raw, list):
            raise VideoPrepError("video segments must be a list")
        segments: list[dict[str, object]] = []
        for segment_raw in segments_raw:
            if not isinstance(segment_raw, dict):
                raise VideoPrepError("each segment must be an object")
            name = segment_raw.get("name")
            if not isinstance(name, str) or not ID_RE.fullmatch(name) or name in segment_names:
                raise VideoPrepError("segment names must be unique lowercase names")
            segment_names.add(name)
            start = timecode_to_seconds(segment_raw.get("start"), f"{name} start")
            end = timecode_to_seconds(segment_raw.get("end"), f"{name} end")
            if end <= start or end > duration + 0.1:
                raise VideoPrepError(f"{name} has an invalid time range")
            fps_raw = segment_raw.get("fps", default_fps)
            if not isinstance(fps_raw, (int, float)) or isinstance(fps_raw, bool):
                raise VideoPrepError(f"{name} fps must be numeric")
            fps = float(fps_raw)
            if not math.isfinite(fps) or not 0.25 <= fps <= 10.0:
                raise VideoPrepError(f"{name} fps is outside the allowed range")
            estimated = math.ceil((end - start) * fps)
            estimated_frames += estimated
            segments.append({"name": name, "start": start, "end": end, "fps": fps, "estimatedFrames": estimated})
        validated.append(
            {
                "id": video_id,
                "source": source,
                "sha256": expected_hash,
                "metadata": metadata,
                "segments": segments,
                "maxWidth": max_width_raw,
                "includeClips": include_clips,
            }
        )
    if not segment_names:
        raise VideoPrepError("manifest must define at least one segment")
    if len(segment_names) > MAX_SEGMENTS:
        raise VideoPrepError("manifest contains too many segments")
    if estimated_frames > MAX_OUTPUT_FRAMES:
        raise VideoPrepError("manifest would create too many frames")
    return validated, estimated_frames


def create_segment_clip(source: Path, target: Path, start: float, duration: float) -> None:
    run_checked(
        [
            ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(source),
            "-t", f"{duration:.3f}", "-map", "0:v:0", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-map_metadata", "-1", "-an", "-n", str(target),
        ],
        "frame-accurate segment creation",
    )


def extract_segment_frames(
    source: Path,
    output_pattern: Path,
    start: float,
    duration: float,
    fps: float,
    max_width: int,
) -> None:
    filters = [f"fps={fps:g}"]
    if max_width:
        filters.append(f"scale='min({max_width},iw)':-2")
    run_checked(
        [
            ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(source),
            "-t", f"{duration:.3f}", "-vf", ",".join(filters), "-q:v", "2", "-fps_mode", "vfr",
            "-start_number", "1", "-map_metadata", "-1", "-an", "-n", str(output_pattern),
        ],
        "modeling frame extraction",
    )


def extract_manifest(manifest_value: str, output_value: str, *, dry_run: bool) -> dict[str, object]:
    manifest_path, payload = load_manifest(manifest_value)
    videos, estimated_frames = validate_manifest(payload)
    summary = {
        "ok": True,
        "dryRun": dry_run,
        "manifest": str(manifest_path),
        "videoCount": len(videos),
        "segmentCount": sum(len(item["segments"]) for item in videos),
        "estimatedFrames": estimated_frames,
    }
    if dry_run:
        return summary
    output = prepare_output(output_value, "extraction output")
    clips_dir = output / "clips"
    frames_root = output / "frames"
    clips_dir.mkdir(mode=0o700)
    frames_root.mkdir(mode=0o700)
    extracted: list[dict[str, object]] = []
    for video in videos:
        source = video["source"]
        assert isinstance(source, Path)
        actual_hash = hash_file(source)
        if actual_hash != video["sha256"]:
            raise VideoPrepError("source video changed after review")
        for segment in video["segments"]:
            assert isinstance(segment, dict)
            name = str(segment["name"])
            start = float(segment["start"])
            end = float(segment["end"])
            fps = float(segment["fps"])
            duration = end - start
            clip_path: Path | None = None
            if video["includeClips"]:
                clip_path = clips_dir / f"{name}.mp4"
                create_segment_clip(source, clip_path, start, duration)
            frames_dir = frames_root / name
            frames_dir.mkdir(mode=0o700)
            extract_segment_frames(
                source,
                frames_dir / f"{name}-%06d.jpg",
                start,
                duration,
                fps,
                int(video["maxWidth"]),
            )
            frames = sorted(frames_dir.glob("*.jpg"))
            if not frames:
                raise VideoPrepError("modeling frame extraction produced no frames")
            with (frames_dir / "frames.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("file", "approx_source_timecode"))
                for frame_index, frame in enumerate(frames):
                    writer.writerow((frame.name, seconds_to_timecode(start + frame_index / fps)))
            extracted.append(
                {
                    "name": name,
                    "sourceId": video["id"],
                    "start": seconds_to_timecode(start),
                    "end": seconds_to_timecode(end),
                    "fps": fps,
                    "frameCount": len(frames),
                    "framesDirectory": f"frames/{name}",
                    "clip": f"clips/{clip_path.name}" if clip_path else None,
                    "clipSha256": hash_file(clip_path) if clip_path else None,
                }
            )
    result = {**summary, "dryRun": False, "output": str(output), "segments": extracted}
    (output / "extraction.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
