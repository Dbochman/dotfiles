#!/usr/bin/python3
"""Capture a single frame from a Nest camera via WebRTC (SDM API).

Usage: nest-camera-snap.py <device_id> <access_token> <project_id> <output_path>

Requires: aiortc, Pillow (pip3 install aiortc Pillow)
"""

import asyncio
import json
import sys
import urllib.request
import urllib.error
from aiortc import RTCPeerConnection, RTCSessionDescription


async def capture_frame(device_id: str, access_token: str, project_id: str, output_path: str, timeout: float = 15.0):
    pc = RTCPeerConnection()

    # Nest requires m-lines in order: audio, video, application (data channel)
    pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")
    dc = pc.createDataChannel("data")

    frame_received = asyncio.Event()
    saved_frame = [None]

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            asyncio.ensure_future(_recv_first_frame(track, frame_received, saved_frame))

    async def _recv_first_frame(track, event, container):
        try:
            frame = await asyncio.wait_for(track.recv(), timeout=timeout)
            img = frame.to_image()  # PIL Image
            container[0] = img
            event.set()
        except Exception as e:
            print(f"Error receiving frame: {e}", file=sys.stderr)
            event.set()

    # Create SDP offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Send offer to Nest SDM API
    url = f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{project_id}/devices/{device_id}:executeCommand"
    body = json.dumps({
        "command": "sdm.devices.commands.CameraLiveStream.GenerateWebRtcStream",
        "params": {"offerSdp": pc.localDescription.sdp}
    }).encode()

    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"SDM API error ({e.code}): {error_body}", file=sys.stderr)
        await pc.close()
        return False

    answer_sdp = result["results"]["answerSdp"]
    media_session_id = result["results"]["mediaSessionId"]

    # Fix Nest's non-standard ICE candidates in the answer SDP.
    # Nest sends: "a=candidate: 1 udp 2113939711 ip port typ host generation 0"
    # This has no foundation field — aiortc expects "foundation component protocol ..."
    # Fix: insert a dummy foundation "0" when the format is detected as missing it.
    cleaned_sdp_lines = []
    for line in answer_sdp.splitlines():
        if line.startswith("a=candidate:"):
            if " ssltcp " in line:
                continue
            value = line[len("a=candidate:"):].strip()
            parts = value.split()
            if len(parts) >= 7 and parts[1] in ("udp", "tcp"):
                line = "a=candidate:0 " + value
            else:
                line = "a=candidate:" + value
            cleaned_sdp_lines.append(line)
        else:
            cleaned_sdp_lines.append(line)
    cleaned_sdp = "\r\n".join(cleaned_sdp_lines) + "\r\n"

    # Set remote description
    answer = RTCSessionDescription(sdp=cleaned_sdp, type="answer")
    await pc.setRemoteDescription(answer)

    # Wait for first frame
    try:
        await asyncio.wait_for(frame_received.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        print("Timeout waiting for video frame", file=sys.stderr)

    # Save frame
    if saved_frame[0]:
        saved_frame[0].save(output_path, "JPEG", quality=90)
        print(output_path)

    # Stop the stream
    try:
        stop_body = json.dumps({
            "command": "sdm.devices.commands.CameraLiveStream.StopWebRtcStream",
            "params": {"mediaSessionId": media_session_id}
        }).encode()
        stop_req = urllib.request.Request(url, data=stop_body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        })
        urllib.request.urlopen(stop_req, timeout=10)
    except Exception:
        pass

    await pc.close()
    return saved_frame[0] is not None


def main():
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <device_id> <access_token> <project_id> <output_path>", file=sys.stderr)
        sys.exit(1)

    device_id, access_token, project_id, output_path = sys.argv[1:5]
    ok = asyncio.run(capture_frame(device_id, access_token, project_id, output_path))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
