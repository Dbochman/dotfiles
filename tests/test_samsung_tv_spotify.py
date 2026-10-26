"""Frame-only Spotify routing tests, with no account or TV access."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import unittest
from unittest.mock import patch

loader = SourceFileLoader("frame_spotify", str(Path(__file__).resolve().parents[1] / "bin/samsung-tv-spotify"))
tv = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(tv)
TRACK = "spotify:track:2JiDi0qAXsPwhPqA2qaKGt"
FRAME = {"id": "frame-id", "name": "TV", "type": "TV", "is_restricted": False}


class FrameSpotifyTests(unittest.TestCase):
    def test_renamed_tv_is_selected_and_reported_playing(self):
        old = dict(FRAME, id="old-id", name="Samsung The Frame 65")
        self.assertEqual(tv.select_frame([old, FRAME]), "frame-id")
        result = tv.summarize_status({"device": FRAME, "is_playing": True,
                                      "item": {"name": "Track", "uri": TRACK}}, TRACK)
        self.assertTrue(result["frame_playing"])
        self.assertTrue(result["requested_track_matches"])
        with self.assertRaises(RuntimeError):
            tv.select_frame([old])

    def test_provider_input_validation_precedes_connections(self):
        self.assertEqual(tv.content_uri("https://open.spotify.com/track/2JiDi0qAXsPwhPqA2qaKGt?si=share"), TRACK)
        for value in ("https://open.spotify.com.evil.test/track/2JiDi0qAXsPwhPqA2qaKGt",
                      "https://@open.spotify.com/track/2JiDi0qAXsPwhPqA2qaKGt", TRACK + ";id",
                      "spotify:track:2JiDi0qAXsPwhPqA2qaKG\nt"):
            with self.subTest(value=value), patch.object(tv, "verified_binary") as binary:
                with self.assertRaises(ValueError):
                    tv.operate("play", value)
                binary.assert_not_called()

    def test_missing_ambiguous_and_restricted_devices_fail(self):
        for devices in ([], [FRAME, FRAME], [dict(FRAME, is_restricted=True)], [dict(FRAME, type="Computer")]):
            with self.subTest(devices=devices), self.assertRaises(RuntimeError):
                tv.select_frame(devices)
        self.assertEqual(tv.select_frame([FRAME]), "frame-id")

    def test_exact_device_is_used_and_delayed_state_does_not_repeat_play(self):
        replies = [[FRAME], {"status": "ok"}, {}, {}, {}]
        with patch.object(tv, "verified_binary", return_value=Path("/reviewed/client")), \
                patch.object(tv.subprocess, "run") as launch, patch.object(tv.time, "sleep"), \
                patch.object(tv, "invoke", side_effect=replies) as invoke:
            result = tv.operate("play", TRACK)
        launch.assert_called_once()
        writes = [call for call in invoke.call_args_list if call.args[1][0] == "play"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].kwargs["device"], "frame-id")
        self.assertTrue(result["request_accepted"])
        self.assertFalse(result["observed"]["frame_playing"])

    def test_pause_targets_frame_without_launching(self):
        with patch.object(tv, "verified_binary", return_value=Path("/reviewed/client")), \
                patch.object(tv.subprocess, "run") as launch, \
                patch.object(tv, "invoke", side_effect=[[FRAME], {}, {"device": FRAME, "is_playing": False}]) as invoke:
            tv.operate("pause")
        launch.assert_not_called()
        self.assertEqual(invoke.call_args_list[1].kwargs["device"], "frame-id")

    def test_launch_waits_for_connect_registration_before_one_play(self):
        replies = [[], [], [FRAME], {}, {"device": FRAME, "is_playing": True,
                                         "item": {"uri": TRACK}}]
        with patch.object(tv, "verified_binary"), patch.object(tv.subprocess, "run") as launch, \
                patch.object(tv.time, "sleep") as sleep, \
                patch.object(tv, "invoke", side_effect=replies) as invoke:
            result = tv.operate("play", TRACK)
        launch.assert_called_once()
        self.assertEqual(sleep.call_count, 2)
        writes = [call for call in invoke.call_args_list if call.args[1][0] == "play"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].kwargs["device"], "frame-id")
        self.assertTrue(result["observed"]["frame_playing"])

    def test_registration_timeout_never_sends_play(self):
        with patch.object(tv, "verified_binary"), patch.object(tv.subprocess, "run"), \
                patch.object(tv.time, "sleep"), \
                patch.object(tv, "invoke", return_value=[]) as invoke:
            with self.assertRaises(tv.FrameUnavailable):
                tv.operate("play", TRACK)
        self.assertEqual(invoke.call_count, 6)
        self.assertTrue(all(call.args[1] == ["device", "list"] for call in invoke.call_args_list))

    def test_ambiguous_or_restricted_device_does_not_retry(self):
        for devices in ([FRAME, FRAME], [dict(FRAME, is_restricted=True)]):
            with self.subTest(devices=devices), patch.object(tv, "verified_binary"), \
                    patch.object(tv.subprocess, "run"), patch.object(tv.time, "sleep") as sleep, \
                    patch.object(tv, "invoke", return_value=devices) as invoke:
                with self.assertRaises(RuntimeError):
                    tv.operate("play", TRACK)
                invoke.assert_called_once()
                sleep.assert_not_called()

    def test_other_device_status_is_not_exposed_or_called_tv_playback(self):
        result = tv.summarize_status({"device": {"name": "Phone", "type": "Phone"},
                                      "is_playing": True, "item": {"name": "private item", "uri": TRACK}}, TRACK)
        self.assertFalse(result["frame_playing"])
        self.assertFalse(result["requested_track_matches"])
        self.assertNotIn("title", result)
        self.assertNotIn("uri", result)


if __name__ == "__main__":
    unittest.main()
