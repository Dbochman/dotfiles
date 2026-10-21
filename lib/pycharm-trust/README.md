# PyCharm external public trust store

Deployed on the Crosstown MacBook (`dylans-macbook-pro`), not globally across
Macs or Java installations. The vendor app remains unmodified and signed.

## Installed configuration

- `/Applications/PyCharm.app.vmoptions` is the adjacent options file supported
  by the native launcher (Toolbox-style lookup). Its tracked source is here.
  It takes precedence over versioned user options; future edits through
  **Help → Edit Custom VM Options** must preserve the three trust properties.
- `~/.local/share/app-trust/pycharm/public-ca.p12` contains public CA certificates
  only, derived from the repaired Homebrew CA bundle. The fixed password is
  public integrity metadata, not a private key or account credential.
- `~/.local/lib/app-trust/` holds a deployed copy of `bin/refresh-pycharm-trust`
  and this directory's Java sources under the same relative layout.
- The old `PyCharm2024.1` settings and 4 GiB heap preference are preserved.
  Using the adjacent options file avoids pre-creating `PyCharm2026.2` and
  suppressing the IDE's first-launch configuration migration.

## Refresh and verification

After CA-package changes, run on the MacBook:

```sh
~/.local/lib/app-trust/bin/refresh-pycharm-trust
```

From a current checkout, `bin/refresh-pycharm-trust` is equivalent. The command
copies the public PEM source, builds a fresh PKCS12 store with PyCharm's Java,
and checks 12 incident canaries plus two ordinary sites before atomically
replacing the installed store. Failed/inconclusive checks retain the previous
store. Each refresh directory retains the source, test output and, when
replacing a store, the previous public store. No automatic refresh job is
installed. Restart PyCharm after a successful refresh to clear cached contexts.

The canaries are an incident-specific check, not a comprehensive TLS audit.
Transport failures and changed accepted issuers fail verification; network
interception can still disguise rejection results. Do not use this check to
certify an intercepted network without independent chain evidence.

For launcher verification, scoped `IJ_LAUNCHER_DEBUG=1` with the native
`/Applications/PyCharm.app/Contents/MacOS/pycharm --help` shows which options
file it loads. Do not publish full launcher diagnostics from a configured IDE;
select only the relevant trust properties. The deployed configuration was also
tested through a temporary external Java premain probe, which ran TLS checks
inside the native-launched JVM and exited before the IDE entry point. That
probe is not installed in the permanent options file.

## Boundaries and rollback

This replaces default JVM trust; it does not certify plugins, Python/Git
subprocesses, JCEF, or manually accepted IDE certificates. Keep automatic
acceptance of untrusted certificates disabled. The legacy configuration had
no custom `ssl/cacerts` store at deployment. First GUI launch and migration
remain pending; recheck custom certificate policy after migration.

After an app upgrade, verify that the adjacent options file still exists and
is selected, refresh the store, and repeat the checks. Do not silently fall
back to vendor roots if the external store is missing or a check fails.

Removing the adjacent options file restores vendor default trust, which was
vulnerable when tested; that is not a safe routine rollback. Keep the app
closed and investigate instead. Restoring an older store likewise requires
fresh verification.

Sources: [JetBrains options guidance](https://www.jetbrains.com/help/pycharm/tuning-the-ide.html),
[native launcher lookup](https://github.com/JetBrains/intellij-community/blob/master/native/XPlatLauncher/src/default.rs),
[IDE certificate storage](https://www.jetbrains.com/help/pycharm/settings-tools-server-certificates.html).
