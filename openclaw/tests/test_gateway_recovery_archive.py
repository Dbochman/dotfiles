import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin", "macOS recovery archive contract")
class GatewayRecoveryArchiveTests(unittest.TestCase):
    def test_preserves_group_links_and_committed_wal_without_changing_archive(self):
        groups = [group for group in os.getgroups() if group != os.getgid()]
        if not groups:
            self.skipTest("An alternate owned group is required")
        group = 80 if 80 in groups else groups[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            restored = root / "restored"
            source.mkdir(mode=0o700)
            restored.mkdir(mode=0o700)
            payload = source / "payload"
            payload.write_bytes(b"synthetic recovery evidence\x00")
            payload.chmod(0o600)
            os.chown(payload, -1, group)
            os.chown(source, -1, group)
            (source / "absolute-link").symlink_to("/usr/bin/open")
            (source / "relative-link").symlink_to("payload")
            database = sqlite3.connect(source / "state.sqlite")
            try:
                database.execute("PRAGMA journal_mode=WAL")
                database.execute("PRAGMA wal_autocheckpoint=0")
                database.execute("CREATE TABLE evidence (value TEXT)")
                database.execute("INSERT INTO evidence VALUES (?)", ("committed WAL",))
                database.commit()
                self.assertGreater((source / "state.sqlite-wal").stat().st_size, 0)
                archive = root / "checkpoint.tar"
                subprocess.run(
                    ["/usr/bin/tar", "--mac-metadata", "--acls", "--xattrs", "-cf",
                     str(archive), "-C", str(root), "source"],
                    check=True, capture_output=True, timeout=15,
                )
                archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
                subprocess.run(
                    ["/usr/bin/tar", "--same-owner", "--mac-metadata", "--acls", "--xattrs",
                     "-xpf", str(archive), "-C", str(restored)],
                    check=True, capture_output=True, timeout=15,
                )
                copy = restored / "source"
                for relative in (".", "payload", "absolute-link", "relative-link"):
                    original = (source / relative).lstat()
                    recovered = (copy / relative).lstat()
                    self.assertEqual(recovered.st_uid, original.st_uid)
                    self.assertEqual(recovered.st_gid, original.st_gid)
                    self.assertEqual(stat.S_IMODE(recovered.st_mode), stat.S_IMODE(original.st_mode))
                self.assertEqual((copy / "payload").read_bytes(), payload.read_bytes())
                self.assertEqual(os.readlink(copy / "absolute-link"), "/usr/bin/open")
                self.assertEqual(os.readlink(copy / "relative-link"), "payload")
                with sqlite3.connect(copy / "state.sqlite") as recovered_database:
                    self.assertEqual(recovered_database.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                    self.assertEqual(recovered_database.execute("SELECT value FROM evidence").fetchall(), [("committed WAL",)])
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), archive_hash)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
