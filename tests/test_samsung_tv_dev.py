"""Ownership and cleanup regression tests; no network or TV access."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import io
import json
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch


loader = SourceFileLoader("samsung_tv_dev", str(
    Path(__file__).resolve().parents[1] / "bin/samsung-tv-dev"
))
tv = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(tv)


class ServerOwnershipTests(unittest.TestCase):
    def test_refuses_replacement_server(self):
        with patch.object(tv, "server_pid", return_value=22), patch.object(tv.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                tv.stop_owned_server(11, Path("/verified/sdb"))
            kill.assert_not_called()

    def test_refuses_unrecognized_executable(self):
        result = subprocess.CompletedProcess([], 0, "n/other/sdb\n", "")
        with patch.object(tv, "server_pid", return_value=11), \
                patch.object(tv.subprocess, "run", return_value=result), \
                patch.object(tv.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "Cannot confirm"):
                tv.stop_owned_server(11, Path("/verified/sdb"))
            kill.assert_not_called()

    def test_stops_verified_executable_even_with_rosetta_mapping(self):
        result = subprocess.CompletedProcess([], 0, "n/verified/sdb\nn/rosetta/runtime\n", "")
        with patch.object(tv, "server_pid", side_effect=[11, None]), \
                patch.object(tv.subprocess, "run", return_value=result), \
                patch.object(tv.os, "kill") as kill:
            tv.stop_owned_server(11, Path("/verified/sdb"))
            kill.assert_called_once_with(11, tv.signal.SIGTERM)

    def test_listener_inspection_error_is_not_absence(self):
        result = subprocess.CompletedProcess([], 1, "", "inspection failed")
        with patch.object(tv.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                tv.server_pid()

    def test_no_listener_is_normal(self):
        result = subprocess.CompletedProcess([], 1, "", "")
        with patch.object(tv.subprocess, "run", return_value=result):
            self.assertIsNone(tv.server_pid())


class ControlInputTests(unittest.TestCase):
    def test_app_id_cannot_escape_command_or_path(self):
        for value in ("../browser", "YouTube;id", "$(id)", "", "--help"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tv.validate_app_id(value)

    def test_link_remains_one_literal_argument(self):
        uri = "https://example.com/watch?v=a'$(touch${IFS}/tmp/not-real)&x=1"
        words = shlex.split(tv.native_launch_command("com.example.player", uri))
        self.assertEqual(words[-1], uri)
        self.assertEqual(words[1], "com.example.player")
        self.assertEqual(len(words), 6)

    def test_rejects_executable_and_credential_links(self):
        for uri in ("javascript:alert(1)", "file:///etc/passwd", "https://u:p@example.com/",
                    "https:///missing", "https://example.com/\nother"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                tv.validate_uri(uri)

    def test_video_urls_are_exact_hosts_and_preserve_time(self):
        self.assertEqual(tv.youtube_video("https://youtu.be/eyZEqSPeMwg?t=1m23s"), ("eyZEqSPeMwg", 83))
        self.assertEqual(tv.youtube_video("https://www.youtube.com/shorts/eyZEqSPeMwg"), ("eyZEqSPeMwg", 0))
        for value in ("https://youtube.com.evil.test/watch?v=eyZEqSPeMwg",
                      "https://www.youtube.com/results?search_query=cats", "not a video"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tv.youtube_video(value)

    def test_manifest_returns_only_requested_app_and_safe_link_fields(self):
        raw = '''<manifest><ui-application appid="com.example.player">
          <app-control><operation name="view"/><uri name="https"/></app-control>
          <metadata key="http://samsung.com/tv/metadata/nativeID" value="com.example.native"/>
          <metadata key="secret" value="do-not-output"/>
          </ui-application><ui-application appid="other"><app-control>
          <uri name="unrelated"/></app-control></ui-application></manifest>'''
        result = tv.manifest_summary(raw, "com.example.player")
        self.assertEqual(result["link_metadata"], {"nativeID": "com.example.native"})
        self.assertEqual(result["controls"][0]["uri"], ["https"])
        self.assertNotIn("do-not-output", json.dumps(result))


class SDKCallbackTests(unittest.TestCase):
    def test_early_exit_and_errexit_still_report_failure_and_cleanup(self):
        for command in ("exit 17", "set -e; false; echo should-not-run"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                remote, gate = Path(directory) / "operation.sh", Path(directory) / "gate"
                remote.touch()
                gate.mkdir()
                result = subprocess.run(["/bin/sh", "-c", tv.sdk_operation_body(
                    command, remote, gate)], capture_output=True, text=True, check=True)
                self.assertFalse(remote.exists())
                self.assertFalse(gate.exists())
                self.assertIn("__OC_CLEANUP__=1", result.stdout)
                self.assertIn("__OC_RESULT__=" + ("17" if command == "exit 17" else "1"), result.stdout)
                self.assertNotIn("should-not-run", result.stdout)

    def test_nonempty_gate_does_not_claim_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            remote, gate = Path(directory) / "operation.sh", Path(directory) / "gate"
            remote.touch()
            gate.mkdir()
            (gate / "unexpected").touch()
            result = subprocess.run(["/bin/sh", "-c", tv.sdk_operation_body(
                "true", remote, gate)], capture_output=True, text=True, check=True)
            self.assertNotIn("__OC_CLEANUP__=1", result.stdout)
            self.assertTrue((gate / "unexpected").exists())

    def test_repeated_transport_does_not_recreate_cleaned_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            remote, gate = Path(directory) / "operation.sh", Path(directory) / "gate"
            remote.write_text(f"rm -f {remote}; rmdir {gate}\n")
            command = tv.guarded_sdk_command(remote, gate)
            for _ in range(2):
                subprocess.run(["/bin/sh", "-c", command], check=False, capture_output=True)
            self.assertFalse(remote.exists())
            self.assertFalse(gate.exists())
        tv.injection_argument(tv.guarded_sdk_command(
            "/home/owner/share/tmp/sdk_tools/.oc-tv-" + "a" * 16 + ".sh",
            "/tmp/oc-tv-" + "a" * 16))

    def run_capture(self, payload):
        listener, connection = MagicMock(), MagicMock()
        listener.__enter__.return_value = listener
        listener.getsockname.return_value = (tv.CALLBACK_HOST, 54321)
        listener.accept.return_value = (connection, ("192.168.165.2", 40000))
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [payload, b""]
        with patch.object(tv.socket, "socket", return_value=listener), \
                patch.object(tv.secrets, "token_hex", return_value="abc123"), \
                patch.object(tv, "sdb_command"), \
                patch.object(tv.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
                patch.object(tv.sys, "stderr", new_callable=io.StringIO):
            return tv.sdk_capture(Path("/verified/sdb"), "true")

    def test_callback_proves_success_despite_transport_exit_one(self):
        result = self.run_capture(b"abc123\n... successfully launched\n__OC_CLEANUP__=1\n__OC_RESULT__=0\n")
        self.assertEqual(result, "... successfully launched")

    def test_bad_callback_proof_fails(self):
        with self.assertRaisesRegex(RuntimeError, "proof mismatch"):
            self.run_capture(b"wrong\n__OC_CLEANUP__=1\n__OC_RESULT__=0\n")

    def test_cleanup_and_command_failure_are_not_success(self):
        for payload in (b"abc123\n__OC_RESULT__=0\n",
                        b"abc123\n__OC_CLEANUP__=1\n__OC_RESULT__=1\n"):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.run_capture(payload)


class ForegroundTests(unittest.TestCase):
    def test_focused_app_does_not_imply_ui_or_playback(self):
        raw = ("[get_focused_pid] result: 4553\n"
               "pkgname = org.tizen.netflix-app, pid = 4553\n"
               "[get_focused_pid] result: 4553\n")
        result = tv.foreground_summary(raw)
        self.assertEqual(result["app"], "org.tizen.netflix-app")
        self.assertTrue(result["foreground_verified"])
        for key in ("screen_observed", "playback_verified", "ui_tree_available"):
            self.assertFalse(result[key])

    def test_focus_change_or_wrong_mapping_is_not_verified(self):
        for pid, after in ((4553, 9999), (9999, 4553)):
            result = tv.foreground_summary(
                f"[get_focused_pid] result: 4553\npkgname = org.tizen.netflix-app, pid = {pid}\n"
                f"[get_focused_pid] result: {after}\n")
            self.assertFalse(result["foreground_verified"])
            self.assertIsNone(result["app"])

    def test_missing_mapping_and_missing_observation(self):
        result = tv.foreground_summary("[get_focused_pid] result: 0\n[get_focused_pid] result: 0\n")
        self.assertIsNone(result["focused_pid"])
        self.assertFalse(result["foreground_verified"])
        with self.assertRaises(RuntimeError):
            tv.foreground_summary("[get_focused_pid] result: 4553\n")


class ScreenshotTests(unittest.TestCase):
    def test_only_focused_window_inside_owned_directory_is_selected(self):
        directory = "/tmp/oc-screen-test"
        raw = ("  3 0x0202e538 47 4553 1920 1080 0 0 O 24 -1 0 0 1 V Netflix\n"
               "/tmp/oc-screen-test/topvwins-20261024.132751/0x0202e538_0.png SAVED\n"
               "/tmp/oc-screen-test/topvwins-20261024.132751/0x02056930_0.png SAVED\n")
        self.assertEqual(tv.focused_capture_path(raw, directory, 4553),
                         directory + "/topvwins-20261024.132751/0x0202e538_0.png")
        for modified in (raw.replace("/tmp/oc-screen-test/", "/tmp/other/"),
                         raw.replace(" 4553 ", " 9999 "), raw.replace(" 0 0 O ", " 0 0 ")):
            with self.subTest(raw=modified), self.assertRaises(RuntimeError):
                tv.focused_capture_path(modified, directory, 4553)

    def test_changed_manifest_prevents_root_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            assets = home / tv.ROOT_RELATIVE
            assets.mkdir(parents=True)
            (assets / "manifest.json").write_text('{}')
            with patch.object(tv.Path, "home", return_value=home), patch.object(tv, "sdk_capture") as capture:
                with self.assertRaisesRegex(RuntimeError, "manifest"):
                    tv.screenshot(Path("/verified/sdb"))
                capture.assert_not_called()


class MaxControlTests(unittest.TestCase):
    video = "dae9e532-3714-4f2e-b758-fb9a13def902"

    def test_exact_provider_links_and_explicit_target_types(self):
        self.assertEqual(tv.max_target("https://play.hbomax.com/video/watch/" + self.video + "?utm_source=test", "video"), self.video)
        self.assertEqual(tv.max_target(self.video, "show"), self.video)
        for value, kind in [("https://play.hbomax.com.evil.test/video/watch/" + self.video, "video"),
                            ("https://user:secret@play.hbomax.com/video/watch/" + self.video, "video"),
                            ("https://play.hbomax.com/show/" + self.video, "video"),
                            (self.video + ";id", "video"), ("title,type=video", "search"),
                            ("title?type=video", "search"), ("title\n", "search"), ("", "search")]:
            with self.subTest(value=value, kind=kind), patch.object(tv, "installed_apps") as installed:
                with self.assertRaises(ValueError):
                    tv.max_request(None, value, kind)
                installed.assert_not_called()

    def test_cold_launcher_preserves_comma_payload_as_one_argument(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.HBO_MAX}]), \
                patch.object(tv, "app_running", return_value=False), \
                patch.object(tv, "sdk_capture", return_value="... test successful ret = 12345") as capture:
            result = tv.max_request(None, self.video, "video")
        words = shlex.split(capture.call_args.args[1])
        self.assertEqual(words, ["/usr/bin/aul_test", "launch", tv.HBO_MAX, "PAYLOAD", "id=" + self.video + ",type=video", "||", "true"])
        self.assertFalse(result["playback_verified"])
        self.assertFalse(result["root_required"])
        self.assertEqual(result["pid"], 12345)
        capture.assert_called_once()

    def test_restart_waits_then_sends_once(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.HBO_MAX}]), \
                patch.object(tv, "app_running", side_effect=[True, True, False]), \
                patch.object(tv.time, "sleep"), \
                patch.object(tv, "sdk_capture", side_effect=["terminated", "... test successful ret = 12345"]) as capture:
            result = tv.max_request(None, "The Last of Us", "search")
        self.assertTrue(result["restart_performed"])
        self.assertEqual(capture.call_count, 2)
        self.assertIn("--terminate=" + tv.HBO_MAX, capture.call_args_list[0].args[1])

    def test_incomplete_stop_prevents_launch(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.HBO_MAX}]), \
                patch.object(tv, "app_running", return_value=True), \
                patch.object(tv.time, "sleep"), patch.object(tv, "sdk_capture") as capture:
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                tv.max_request(None, self.video, "video")
        capture.assert_called_once()
        self.assertIn("--terminate=", capture.call_args.args[1])

    def test_transport_normalization_does_not_hide_failure_or_repeat_write(self):
        for raw in ("permission denied", "... test successful ret = -1", "... test successful ret = 0", "... test successful ret = 123\n... test successful ret = 456"):
            with self.subTest(raw=raw), patch.object(tv, "installed_apps", return_value=[{"id": tv.HBO_MAX}]), \
                    patch.object(tv, "app_running", return_value=False), \
                    patch.object(tv, "sdk_capture", return_value=raw) as capture:
                with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
                    tv.max_request(None, self.video, "video")
                capture.assert_called_once()


class PeacockControlTests(unittest.TestCase):
    episode = "58fabbf4-9291-3575-bcdc-bd25eee192c8"
    link = "https://www.peacocktv.com/watch-online/tv/the-office/4902514835143843112/seasons/1/episodes/pilot-episode-1/" + episode

    def decode(self, payload):
        from urllib.parse import unquote
        import re
        # Match the actual Samsung adapter and worker, including extraction after
        # decoding the whole URL. Ordinary parse_qs would miss delimiter loss.
        url = unquote(json.loads(payload)["values"].replace("+", "%20"))
        encoded = re.search(r"deeplinkData=([^&#]+)", url)[1]
        return json.loads(unquote(encoded))

    def test_public_episode_link_maps_to_uuid_not_internal_content_id(self):
        for kind, action in (("episode", "PDP"), ("play", "PLAY")):
            target, payload = tv.peacock_payload(self.link + "?tracking=discard#ignored", kind)
            self.assertEqual(target, self.episode)
            self.assertEqual(self.decode(payload), {"type": "EPISODE", "action": action, "pvid": self.episode})
            self.assertNotIn("tracking", payload)

    def test_search_is_encoded_data_and_show_does_not_request_playback(self):
        title = 'Title, & "another"? # % +'
        self.assertEqual(self.decode(tv.peacock_payload(title, "search")[1]), {"search": title})
        self.assertEqual(self.decode(tv.peacock_payload("4902514835143843112", "show")[1]),
                         {"type": "SERIES", "action": "PDP", "providerSeriesId": "4902514835143843112"})

    def test_invalid_or_oversized_input_precedes_tv_access(self):
        for value, kind in [(self.link.replace("www.peacocktv.com", "www.peacocktv.com.evil.test"), "play"),
                            (self.link.replace("https://", "https://user:secret@"), "play"),
                            ("4902514835143843112", "play"), (self.episode, "show"),
                            ("GMO_00000000059625_01", "play"), (self.episode + ";id", "play"),
                            ("title\n", "search"), ("", "search"), ("😀" * 120, "search"),
                            (self.episode, "watchlist")]:
            with self.subTest(value=value, kind=kind), patch.object(tv, "installed_apps") as installed:
                with self.assertRaises(ValueError):
                    tv.peacock_request(None, value, kind)
                installed.assert_not_called()

    def test_native_launch_is_one_intact_payload_and_no_playback_overclaim(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.PEACOCK}]), \
                patch.object(tv, "app_running", return_value=False), \
                patch.object(tv, "sdk_capture", return_value="... test successful ret = 12345") as capture:
            result = tv.peacock_request(None, self.link, "play")
        words = shlex.split(capture.call_args.args[1])
        self.assertEqual(words[:4], ["/usr/bin/aul_test", "launch", tv.PEACOCK, "PAYLOAD"])
        self.assertEqual(self.decode(words[4]), {"type": "EPISODE", "action": "PLAY", "pvid": self.episode})
        self.assertEqual(result["action"], "play")
        self.assertFalse(result["playback_verified"])
        self.assertFalse(result["root_required"])
        capture.assert_called_once()


class PrimeVideoControlTests(unittest.TestCase):
    gti = "amzn1.dv.gti.22e31bb7-24ed-4ee1-a15c-800a1e9da25f"

    def test_invalid_or_web_id_never_reaches_tv(self):
        for value in ("0H7YPDCNBQ6LAHGQB0SZDBLCNZ", self.gti + ";id", self.gti + "\n", "https://www.primevideo.com/detail/example"):
            with self.subTest(value=value), patch.object(tv, "sdk_capture") as capture:
                with self.assertRaises(ValueError):
                    tv.prime_video_request(None, value)
                capture.assert_not_called()

    def test_native_gti_request_has_no_blind_keys_or_playback_claim(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.PRIME_VIDEO}]), \
                patch.object(tv, "sdk_capture", return_value="successfully launched") as capture:
            result = tv.prime_video_request(None, self.gti)
        self.assertEqual(capture.call_count, 1)
        words = shlex.split(capture.call_args.args[1])
        self.assertEqual(words[:3], ["/usr/bin/launch_app", tv.PRIME_VIDEO, "PAYLOAD"])
        self.assertEqual(json.loads(words[3]), {"values": self.gti})
        self.assertFalse(result["playback_verified"])
        self.assertEqual(result["action"], "details")

    def test_failed_acknowledgement_does_not_retry(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.PRIME_VIDEO}]), \
                patch.object(tv, "sdk_capture", return_value="failed") as capture:
            with self.assertRaises(RuntimeError):
                tv.prime_video_request(None, self.gti)
        self.assertEqual(capture.call_count, 1)


class AppleTVControlTests(unittest.TestCase):
    url = "https://tv.apple.com/us/episode/half-loop/umc.cmc.23qox5n6larygqgg6u74fvyj"

    def test_only_provider_content_links_and_safe_generated_query(self):
        canonical, content_id = tv.apple_tv_url(self.url + "?profileId=private&action=play&diagnosticHlsUrl=bad")
        self.assertEqual(canonical, "https://tv.apple.com/episode/" + content_id)
        self.assertTrue(tv.apple_tv_url(self.url, True)[0].endswith("?action=play"))
        for value in (self.url.replace("tv.apple.com", "tv.apple.com.evil.test"),
                      self.url.replace("https://", "https://user@"), self.url + "\n",
                      "https://tv.apple.com/us/search", self.url.replace("/half-loop/", "/../")):
            with self.subTest(value=value), patch.object(tv, "installed_apps") as apps:
                with self.assertRaises(ValueError):
                    tv.apple_tv_request(None, value)
                apps.assert_not_called()

    def test_native_json_values_payload_and_no_restart_or_browser(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.APPLE_TV}]), \
                patch.object(tv, "sdk_capture", return_value="successfully launched") as capture:
            result = tv.apple_tv_request(None, self.url, True)
        capture.assert_called_once()
        words = shlex.split(capture.call_args.args[1])
        self.assertEqual(words[:3], ["/usr/bin/launch_app", tv.APPLE_TV, "PAYLOAD"])
        self.assertEqual(json.loads(words[3]), {"values": tv.apple_tv_url(self.url, True)[0]})
        self.assertEqual(result["action"], "play")
        self.assertFalse(result["playback_verified"])
        self.assertFalse(result["root_required"])

    def test_failed_request_is_not_retried(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.APPLE_TV}]), \
                patch.object(tv, "sdk_capture", return_value="failed") as capture:
            with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
                tv.apple_tv_request(None, self.url)
        capture.assert_called_once()


class TwitchControlTests(unittest.TestCase):
    def test_accepts_only_channel_logins_and_exact_provider_links(self):
        for value in ("BobRoss", "https://www.twitch.tv/bobross", "https://twitch.tv/bobross/"):
            self.assertEqual(tv.twitch_channel(value), "bobross")
        for value in ("bobross;id", "https://twitch.tv.evil.test/bobross",
                      "https://user:secret@twitch.tv/bobross", "https://twitch.tv:444/bobross",
                      "https://twitch.tv/videos/123", "https://twitch.tv/directory",
                      "https://twitch.tv/bobross?x=1", "https://twitch.tv/bobross#x",
                      "https://twitch.tv/bob\nross", "https://twitch.tv//bobross", ""):
            with self.subTest(value=value), patch.object(tv, "installed_apps") as apps:
                with self.assertRaises(ValueError):
                    tv.twitch_play(None, value)
                apps.assert_not_called()

    def test_native_payload_does_not_claim_playback(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.TWITCH}]), \
                patch.object(tv, "app_running", return_value=False), \
                patch.object(tv, "sdk_capture", return_value="... successfully launched") as capture:
            result = tv.twitch_play(None, "BobRoss")
        capture.assert_called_once()
        self.assertEqual(shlex.split(capture.call_args.args[1]),
                         ["/usr/bin/launch_app", tv.TWITCH, "PAYLOAD", "https://www.twitch.tv/bobross"])
        self.assertTrue(result["content_request_submitted"])
        self.assertFalse(result["playback_verified"])
        self.assertFalse(result["browser_fallback"])
        self.assertFalse(result["root_required"])
        self.assertFalse(result["restart_performed"])

    def test_waits_for_graceful_stop_before_one_request(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.TWITCH}]), \
                patch.object(tv, "app_running", side_effect=[True, True, False]), \
                patch.object(tv.time, "sleep"), \
                patch.object(tv, "sdk_capture", side_effect=["terminated", "successfully launched"]) as capture:
            result = tv.twitch_play(None, "bobross")
        self.assertTrue(result["restart_performed"])
        self.assertEqual(capture.call_count, 2)
        self.assertIn("--terminate=" + tv.TWITCH, capture.call_args_list[0].args[1])
        self.assertIn("PAYLOAD", capture.call_args_list[1].args[1])

    def test_refuses_request_when_stop_is_incomplete(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.TWITCH}]), \
                patch.object(tv, "app_running", return_value=True), \
                patch.object(tv.time, "sleep"), patch.object(tv, "sdk_capture") as capture:
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                tv.twitch_play(None, "bobross")
        capture.assert_called_once()
        self.assertIn("--terminate=", capture.call_args.args[1])

    def test_unknown_state_prevents_any_write(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.TWITCH}]), \
                patch.object(tv, "sdk_capture", return_value="permission denied") as capture:
            with self.assertRaisesRegex(RuntimeError, "state is unknown"):
                tv.twitch_play(None, "bobross")
        capture.assert_called_once()
        self.assertIn("--is-running=", capture.call_args.args[1])

    def test_missing_app_and_failed_acknowledgement_do_not_retry(self):
        with patch.object(tv, "installed_apps", return_value=[]), patch.object(tv, "sdk_capture") as capture:
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                tv.twitch_play(None, "bobross")
            capture.assert_not_called()
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.TWITCH}]), \
                patch.object(tv, "app_running", return_value=False), \
                patch.object(tv, "sdk_capture", return_value="failed") as capture:
            with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
                tv.twitch_play(None, "bobross")
            capture.assert_called_once()


class NetflixControlTests(unittest.TestCase):
    def test_exact_provider_links_and_ids_only(self):
        for value in ("80094031", "https://www.netflix.com/watch/80094031?trackId=1",
                      "https://netflix.com/title/80094031/"):
            self.assertEqual(tv.netflix_video(value), "80094031")
        for value in ("0", "80094031;id", "https://netflix.com.evil.test/watch/80094031",
                      "https://user:secret@netflix.com/watch/80094031",
                      "https://netflix.com:444/watch/80094031", "https://netflix.com/browse",
                      "https://netflix.com/watch/8009\n4031"):
            with self.subTest(value=value), patch.object(tv, "installed_apps") as apps:
                with self.assertRaises(ValueError):
                    tv.netflix_play(None, value)
                apps.assert_not_called()

    def test_restart_waits_for_stop_then_submits_one_native_request(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.NETFLIX}]), \
                patch.object(tv, "netflix_running", side_effect=[True, True, False]), \
                patch.object(tv.time, "sleep"), \
                patch.object(tv, "sdk_capture", side_effect=["terminated", "... successfully launched"]) as capture:
            result = tv.netflix_play(None, "80094031")
        self.assertEqual(capture.call_count, 2)
        self.assertIn("--terminate=" + tv.NETFLIX, capture.call_args_list[0].args[1])
        words = shlex.split(capture.call_args_list[1].args[1])
        self.assertEqual(words[:3], ["/usr/bin/launch_app", tv.NETFLIX, "action_play_url"])
        self.assertEqual(words[3], "m=80094031&trackId=15539201&showMovieInfoOnly=false")
        self.assertTrue(result["restart_performed"])
        self.assertFalse(result["playback_verified"])
        self.assertFalse(result["root_required"])

    def test_already_stopped_app_is_not_terminated(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.NETFLIX}]), \
                patch.object(tv, "netflix_running", return_value=False), \
                patch.object(tv, "sdk_capture", return_value="... successfully launched") as capture:
            self.assertFalse(tv.netflix_play(None, "80094030")["restart_performed"])
        capture.assert_called_once()
        self.assertNotIn("--terminate", capture.call_args.args[1])

    def test_does_not_launch_if_termination_never_completes(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.NETFLIX}]), \
                patch.object(tv, "netflix_running", return_value=True), \
                patch.object(tv.time, "sleep"), patch.object(tv, "sdk_capture") as capture:
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                tv.netflix_play(None, "80094031")
        capture.assert_called_once()
        self.assertIn("--terminate=", capture.call_args.args[1])

    def test_unknown_state_is_never_treated_as_stopped(self):
        for raw in ("permission denied", "", "result: running\nresult: not running"):
            with self.subTest(raw=raw), patch.object(tv, "sdk_capture", return_value=raw):
                with self.assertRaisesRegex(RuntimeError, "state is unknown"):
                    tv.netflix_running(None)
        with patch.object(tv, "sdk_capture", return_value="result: not running"):
            self.assertFalse(tv.netflix_running(None))

    def test_failed_launch_is_not_retried(self):
        with patch.object(tv, "installed_apps", return_value=[{"id": tv.NETFLIX}]), \
                patch.object(tv, "netflix_running", return_value=False), \
                patch.object(tv, "sdk_capture", return_value="... launch failed") as capture:
            with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
                tv.netflix_play(None, "80094031")
        capture.assert_called_once()


class YouTubeControlTests(unittest.TestCase):
    def test_length_prefixed_session_frames(self):
        self.assertEqual(tv.lounge_session(b'20\n[[0,["c","session"]]]\n20\n[[1,["S","google"]]]'),
                         ("session", "google"))

    def test_playback_is_one_request_no_browser_and_no_token_output(self):
        replies = [
            b'<service><name>YouTube</name><state>running</state><additionalData><screenId>screen</screenId></additionalData></service>',
            b'{"screens":[{"screenId":"screen","loungeToken":"private-token"}]}',
            b'42\n[[0,["c","session"]],[1,["S","google"]]]', b'[]',
        ]
        with patch.object(tv, "youtube_request", side_effect=replies) as request:
            result = tv.youtube_play("eyZEqSPeMwg")
        self.assertEqual(request.call_count, 4)
        self.assertIn(b"req0__sc=setPlaylist", request.call_args.args[1])
        self.assertFalse(result["browser_fallback"])
        self.assertFalse(result["playback_verified"])
        self.assertNotIn("private-token", json.dumps(result))

    def test_wrong_screen_stops_before_playback(self):
        replies = [
            b'<service><name>YouTube</name><state>running</state><additionalData><screenId>screen</screenId></additionalData></service>',
            b'{"screens":[{"screenId":"other","loungeToken":"private-token"}]}',
        ]
        with patch.object(tv, "youtube_request", side_effect=replies) as request:
            with self.assertRaisesRegex(RuntimeError, "different screen"):
                tv.youtube_play("eyZEqSPeMwg")
        self.assertEqual(request.call_count, 2)

    def test_player_state_never_proves_visible_playback_or_profile_selection(self):
        for video, state, verified in (("eyZEqSPeMwg", "1", True),
                                       ("eyZEqSPeMwg", "2", False),
                                       ("aaaaaaaaaaa", "1", False)):
            with self.subTest(video=video, state=state):
                raw = json.dumps([[5, ["nowPlaying", {"videoId": video, "state": state,
                                                      "currentTime": "42.5", "token": "private"}]]]).encode()
                connection = ("https://www.youtube.com/api/lounge", {}, "session", "google")
                with patch.object(tv, "youtube_connection", return_value=connection), \
                        patch.object(tv, "youtube_request", side_effect=[b"[]", raw]) as request:
                    result = tv.youtube_state("eyZEqSPeMwg")
                self.assertEqual(result["requested_video_playing_in_api"], verified)
                self.assertFalse(result["playback_verified"])
                self.assertFalse(result["screen_observed"])
                self.assertFalse(result["profile_selection_verified"])
                self.assertIn(b"getNowPlaying", request.call_args_list[0].args[1])
                self.assertNotIn("private", json.dumps(result))

    def test_state_does_not_start_a_stopped_app(self):
        raw = b'<service><name>YouTube</name><state>stopped</state></service>'
        with patch.object(tv, "youtube_request", return_value=raw) as request:
            result = tv.youtube_state()
        self.assertFalse(result["running"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(request.call_args.args), 1)

    def test_state_without_player_event_is_not_verified(self):
        connection = ("https://www.youtube.com/api/lounge", {}, "session", "google")
        with patch.object(tv, "youtube_connection", return_value=connection), \
                patch.object(tv, "youtube_request", side_effect=[b"[]", b'[[1,["noop"]]]']):
            self.assertFalse(tv.youtube_state("eyZEqSPeMwg")["playback_verified"])


if __name__ == "__main__":
    unittest.main()
