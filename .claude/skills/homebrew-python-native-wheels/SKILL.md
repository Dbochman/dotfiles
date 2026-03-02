---
name: homebrew-python-native-wheels
description: |
  Fix Homebrew Python formula builds failing or taking 10+ minutes when dependencies
  have native C extensions (pillow, pyobjc, pyyaml, charset-normalizer). Use when:
  (1) `brew install` compiles cmake/pillow/pyobjc from source taking forever,
  (2) "Directory is not installable. Neither setup.py nor pyproject.toml found" error
  when using .whl resources, (3) `venv.pip_install` fails with platform-specific wheels
  (cp312-macosx), (4) `system pip install` fails silently during brew build due to
  network sandbox. Covers the split-resource pattern for Homebrew Python virtualenv formulas.
author: Claude Code
version: 1.0.0
date: 2026-02-14
---

# Homebrew Python Formula: Native Wheel Resources

## Problem

Homebrew's `Language::Python::Virtualenv` helper uses `--no-binary=:all:` (hardcoded in
`std_pip_args` in `formula.rb:1980`), forcing all Python dependencies to build from source.
For packages with C extensions (pillow, pyobjc-core, PyYAML, charset-normalizer), this
means compiling from source — including pulling in build-time deps like cmake and ninja —
turning a 30-second install into a 10+ minute ordeal.

Additionally, Homebrew **sandboxes network access** during builds, so `system pip install`
from PyPI doesn't work. All dependencies must be pre-fetched as `resource` blocks.

## Context / Trigger Conditions

- Formula uses `include Language::Python::Virtualenv` with `venv.pip_install resources`
- Dependencies include packages with C extensions (pillow, pyobjc, PyYAML, etc.)
- Build takes 10+ minutes compiling native code
- Error: `Directory '/private/tmp/...' is not installable. Neither 'setup.py' nor 'pyproject.toml' found.`
  when using platform-specific `.whl` URLs in resource blocks
- Error: pip produces no output and fails silently (network sandbox blocking PyPI)

## Root Cause

Homebrew's `pip_install` method (in `language/python.rb:401-414`) handles wheel files via
a regex on line 407:

```ruby
target /= t.downloader.basename if t.url&.match?("[.-]py3[^-]*-none-any.whl$")
```

This **only matches pure-Python wheels** (`py3-none-any.whl`). Platform-specific wheels
like `cp312-cp312-macosx_11_0_arm64.whl` don't match, so Homebrew extracts the .whl
(which is just a zip), tries to `pip install` the extracted directory, and fails because
there's no `setup.py`/`pyproject.toml` inside a wheel.

## Solution: Split Resources Pattern

Separate dependencies into two groups:

1. **Pure-python deps**: Use `venv.pip_install` (handles sdist and `py3-none-any` wheels)
2. **Native wheels**: Install manually with `system python3, "-m", "pip"` on the `.whl` file

```ruby
# Names of resources that are pre-built wheels with native code
NATIVE_WHEELS = %w[charset-normalizer pillow pyobjc-core pyobjc-framework-Cocoa
                   pyobjc-framework-Quartz PyYAML].freeze

def install
  python3 = "python3.12"
  venv = virtualenv_create(libexec, python3)

  # Install pure-python resources the standard way
  pure_resources = resources.reject { |r| NATIVE_WHEELS.include?(r.name) }
  venv.pip_install pure_resources

  # Install native wheels directly (bypass --no-binary=:all:)
  native_resources = resources.select { |r| NATIVE_WHEELS.include?(r.name) }
  native_resources.each do |r|
    r.stage do
      whl = Dir["*.whl"].first
      system python3, "-m", "pip", "--python=#{libexec}/bin/python",
             "install", "--verbose", "--no-deps", "--ignore-installed",
             "--no-compile", Pathname.pwd/whl
    end
  end

  venv.pip_install_and_link buildpath/"my-package"
end
```

For native wheel resources, use **platform-specific wheel URLs** from PyPI:

```ruby
resource "pillow" do
  url "https://files.pythonhosted.org/packages/.../pillow-12.1.1-cp312-cp312-macosx_11_0_arm64.whl"
  sha256 "..."
end
```

## Finding Wheel URLs

Query PyPI JSON API for platform-specific wheels:

```bash
curl -sL "https://pypi.org/pypi/pillow/12.1.1/json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for f in data['urls']:
    fn = f['filename']
    if 'cp312' in fn and ('arm64' in fn or 'universal2' in fn) and fn.endswith('.whl'):
        print(f'url: {f[\"url\"]}')
        print(f'sha256: {f[\"digests\"][\"sha256\"]}')
        break
"
```

## Key Gotchas

- **Network sandbox**: Homebrew blocks network during builds. Cannot `pip install` from PyPI.
  All deps must be `resource` blocks with pre-fetched URLs.
- **`homebrew-pypi-poet`**: Useful for generating resource blocks but broken on Python 3.13
  (pkg_resources removed from setuptools 82+). Downgrade: `pip install 'setuptools<80'`.
  Also has SSL cert issues on some macOS installs. Manual PyPI queries are more reliable.
- **Wheel-only packages**: Some packages (pystray, sseclient-py) only publish wheels, no
  sdist. Pure-python `py3-none-any` wheels work with `venv.pip_install`. Platform-specific
  wheels need the split pattern above.
- **`pip_install_and_link`**: Use this (not `pip_install` + manual symlinks) for the main
  package to automatically symlink entry points to `bin/`.
- **`[extras]` syntax**: Homebrew's `system` method doesn't handle `path[extras]` well.
  Install extras deps as separate resources instead.

## Verification

```bash
brew tap yourusername/yourtap
brew install your-formula
your-command --help  # entry points work
brew test your-formula  # formula test passes
```

Install should complete in under 60 seconds with pre-built wheels.

## References

- Homebrew `Language::Python::Virtualenv` source: `/opt/homebrew/Library/Homebrew/language/python.rb`
- Homebrew `std_pip_args` source: `/opt/homebrew/Library/Homebrew/formula.rb:1979`
- PyPI JSON API: `https://pypi.org/pypi/{package}/{version}/json`
