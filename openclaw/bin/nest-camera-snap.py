#!/usr/bin/python3
"""Capture a JPEG frame or short MP4 from a Nest camera via SDM WebRTC.

The helper accepts a single JSON object on stdin with ``device_id``,
``access_token``, ``project_id``, ``output_path``, ``capture_kind``, and
``duration_seconds`` fields.  Credentials are never accepted on the command
line.  The destination is replaced atomically with an owner-only file.

Requires: aiortc, Pillow (installed for /usr/bin/python3 on the Mac Mini).
"""

import asyncio
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.request

from aiortc import RTCPeerConnection, RTCRtpReceiver, RTCSessionDescription
from aiortc.contrib.media import MediaRecorder


MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
RESOURCE_ID_PATTERN = re.compile(r"^[-A-Za-z0-9._~]+$")
ACCESS_TOKEN_PATTERN = re.compile(r"^[-A-Za-z0-9._~+/=]+$")
REQUEST_FIELDS = {
    "device_id",
    "access_token",
    "project_id",
    "output_path",
    "capture_kind",
    "duration_seconds",
}
CAPTURE_KINDS = frozenset(("image", "video"))
MIN_VIDEO_SECONDS = 1
MAX_VIDEO_SECONDS = 30


class CaptureRequestError(ValueError):
    """The private stdin request is absent or malformed."""


def read_capture_request(stream):
    """Read and validate the bounded private request from a binary stream."""
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise CaptureRequestError
    try:
        request = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CaptureRequestError
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise CaptureRequestError

    device_id = request.get("device_id")
    access_token = request.get("access_token")
    project_id = request.get("project_id")
    output_path = request.get("output_path")
    string_fields = (device_id, access_token, project_id, output_path)
    if not all(isinstance(value, str) and value for value in string_fields):
        raise CaptureRequestError
    if not RESOURCE_ID_PATTERN.fullmatch(device_id):
        raise CaptureRequestError
    if not RESOURCE_ID_PATTERN.fullmatch(project_id):
        raise CaptureRequestError
    if not ACCESS_TOKEN_PATTERN.fullmatch(access_token):
        raise CaptureRequestError
    if len(output_path) > 4096 or any(ord(character) < 32 for character in output_path):
        raise CaptureRequestError

    capture_kind = request.get("capture_kind")
    duration_seconds = request.get("duration_seconds")
    if capture_kind not in CAPTURE_KINDS or isinstance(duration_seconds, bool):
        raise CaptureRequestError
    if not isinstance(duration_seconds, int):
        raise CaptureRequestError
    if capture_kind == "image" and duration_seconds != 0:
        raise CaptureRequestError
    if capture_kind == "video" and not (
        MIN_VIDEO_SECONDS <= duration_seconds <= MAX_VIDEO_SECONDS
    ):
        raise CaptureRequestError

    normalized_output = os.path.abspath(os.path.expanduser(output_path))
    return (
        device_id,
        access_token,
        project_id,
        normalized_output,
        capture_kind,
        duration_seconds,
    )


def clean_answer_sdp(answer_sdp):
    """Normalize Nest's non-standard ICE candidates for aiortc."""
    cleaned_lines = []
    for line in answer_sdp.splitlines():
        if line.startswith("a=candidate:"):
            if " ssltcp " in line:
                continue
            value = line[len("a=candidate:") :].strip()
            parts = value.split()
            if len(parts) >= 7 and parts[1] in ("udp", "tcp"):
                line = "a=candidate:0 " + value
            else:
                line = "a=candidate:" + value
        cleaned_lines.append(line)
    return "\r\n".join(cleaned_lines) + "\r\n"


def save_frame_atomic(image, output_path):
    """Atomically replace output_path with an fsynced mode-0600 JPEG."""
    destination = Path(output_path)
    previous_umask = os.umask(0o077)
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    finally:
        os.umask(previous_umask)
    descriptor = -1
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix="." + destination.name + ".",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            image.save(handle, "JPEG", quality=90)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None

        directory_descriptor = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def prepare_destination(output_path):
    """Create a private temporary path beside output_path for atomic media."""
    destination = Path(output_path)
    previous_umask = os.umask(0o077)
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    finally:
        os.umask(previous_umask)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix="." + destination.name + ".",
        suffix=".tmp.mp4",
        dir=str(destination.parent),
    )
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    return destination, Path(temporary_path)


def publish_video_atomic(temporary_path, destination):
    """Fsync and atomically publish a completed owner-only MP4."""
    descriptor = os.open(temporary_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise OSError("invalid temporary video")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary_path, destination)
    directory_descriptor = os.open(str(destination.parent), os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def post_json(url, body, access_token, timeout):
    """POST JSON and return a bounded body without disclosing request details."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + access_token,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("oversized response")
    return payload


async def capture_media(
    device_id,
    access_token,
    project_id,
    output_path,
    capture_kind="image",
    duration_seconds=0,
    timeout=15.0,
):
    """Negotiate a Nest WebRTC stream and save a frame or short clip."""
    pc = None
    recorder = None
    temporary_video = None
    media_session_id = None
    command_url = (
        "https://smartdevicemanagement.googleapis.com/v1/enterprises/"
        + project_id
        + "/devices/"
        + device_id
        + ":executeCommand"
    )
    receive_tasks = []

    try:
        pc = RTCPeerConnection()

        # Nest requires m-lines in order: audio, video, application (data).
        pc.addTransceiver("audio", direction="recvonly")
        video_transceiver = pc.addTransceiver("video", direction="recvonly")

        # Nest can duplicate H.264 payload entries in its answer when aiortc
        # offers both baseline profiles.  Offer only the profile selected by
        # Nest; otherwise RTP connects but no complete frame is assembled.
        h264_codecs = [
            codec
            for codec in RTCRtpReceiver.getCapabilities("video").codecs
            if codec.mimeType.lower() == "video/h264"
            and codec.parameters.get("profile-level-id") == "42e01f"
        ]
        if not h264_codecs:
            print("Required H.264 codec profile is unavailable", file=sys.stderr)
            return False
        video_transceiver.setCodecPreferences(h264_codecs)
        pc.createDataChannel("data")

        video_received = asyncio.Event()
        frame_received = asyncio.Event()
        saved_frame = [None]
        video_track = [None]

        async def receive_first_frame(track):
            try:
                frame = await asyncio.wait_for(track.recv(), timeout=timeout)
                saved_frame[0] = frame.to_image()
            except Exception:
                # The caller emits one stable, sanitized failure message.
                pass
            finally:
                frame_received.set()

        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                video_track[0] = track
                video_received.set()
                if capture_kind == "image":
                    receive_tasks.append(
                        asyncio.ensure_future(receive_first_frame(track))
                    )

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        generate_body = {
            "command": "sdm.devices.commands.CameraLiveStream.GenerateWebRtcStream",
            "params": {"offerSdp": pc.localDescription.sdp},
        }
        try:
            response_body = post_json(
                command_url, generate_body, access_token, timeout=10
            )
        except urllib.error.HTTPError as error:
            try:
                error.close()
            except Exception:
                pass
            print(
                "SDM camera request failed (HTTP {})".format(error.code),
                file=sys.stderr,
            )
            return False
        except (urllib.error.URLError, TimeoutError):
            print("SDM camera request failed", file=sys.stderr)
            return False

        try:
            result = json.loads(response_body)
            results = result["results"]
            answer_sdp = results["answerSdp"]
            media_session_id = results["mediaSessionId"]
            if not all(
                isinstance(value, str) and value
                for value in (answer_sdp, media_session_id)
            ):
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            print("SDM camera returned an invalid response", file=sys.stderr)
            return False

        answer = RTCSessionDescription(sdp=clean_answer_sdp(answer_sdp), type="answer")
        await pc.setRemoteDescription(answer)

        try:
            await asyncio.wait_for(video_received.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            print("Could not receive a camera video track", file=sys.stderr)
            return False

        if capture_kind == "image":
            try:
                await asyncio.wait_for(frame_received.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            if saved_frame[0] is None:
                print("Could not receive a camera frame", file=sys.stderr)
                return False
            try:
                save_frame_atomic(saved_frame[0], output_path)
            except Exception:
                print("Camera frame could not be saved", file=sys.stderr)
                return False
        else:
            destination, temporary_video = prepare_destination(output_path)
            try:
                first_recorded_frame = asyncio.Event()

                class SignalingVideoTrack:
                    kind = "video"

                    def __init__(self, source):
                        self.source = source

                    async def recv(self):
                        frame = await self.source.recv()
                        first_recorded_frame.set()
                        return frame

                recorder = MediaRecorder(
                    str(temporary_video),
                    format="mp4",
                    options={"movflags": "+faststart"},
                )
                recorder.addTrack(SignalingVideoTrack(video_track[0]))
                await recorder.start()
                await asyncio.wait_for(first_recorded_frame.wait(), timeout=timeout)
                await asyncio.sleep(duration_seconds)
                await recorder.stop()
                recorder = None
                publish_video_atomic(temporary_video, destination)
                temporary_video = None
            except Exception:
                print("Camera video could not be saved", file=sys.stderr)
                return False
        return True
    except Exception:
        print("Camera stream negotiation failed", file=sys.stderr)
        return False
    finally:
        if recorder is not None:
            try:
                await recorder.stop()
            except Exception:
                pass
        if temporary_video is not None:
            try:
                os.unlink(temporary_video)
            except FileNotFoundError:
                pass
        if media_session_id is not None:
            stop_body = {
                "command": "sdm.devices.commands.CameraLiveStream.StopWebRtcStream",
                "params": {"mediaSessionId": media_session_id},
            }
            try:
                post_json(command_url, stop_body, access_token, timeout=10)
            except Exception:
                pass
        for task in receive_tasks:
            if not task.done():
                task.cancel()
        if receive_tasks:
            await asyncio.gather(*receive_tasks, return_exceptions=True)
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass


def main():
    if len(sys.argv) != 1:
        print("Camera capture request must be provided on stdin", file=sys.stderr)
        return 2
    try:
        (
            device_id,
            access_token,
            project_id,
            output_path,
            capture_kind,
            duration_seconds,
        ) = read_capture_request(sys.stdin.buffer)
    except CaptureRequestError:
        print("Invalid camera capture request", file=sys.stderr)
        return 2

    try:
        ok = asyncio.run(
            capture_media(
                device_id,
                access_token,
                project_id,
                output_path,
                capture_kind,
                duration_seconds,
            )
        )
    except (KeyboardInterrupt, SystemExit):
        print("Camera capture interrupted", file=sys.stderr)
        return 1
    except Exception:
        print("Camera capture failed", file=sys.stderr)
        return 1
    if ok:
        print(output_path)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
