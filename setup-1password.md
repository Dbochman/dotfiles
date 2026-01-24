# 1Password Setup for Secrets

## SSH Agent (Already configured in ssh_config)

1. Open 1Password → Settings → Developer
2. Enable "SSH Agent"
3. Enable "Use SSH Agent"
4. Add your SSH keys to 1Password (drag .pub files or create new)

Test: `ssh -T git@github.com` (should prompt 1Password for approval)

## 1Password CLI (for environment variables)

### Install

```bash
brew install 1password-cli
```

### Sign in

```bash
eval $(op signin)
```

### Store secrets in 1Password

Create items in 1Password like:
- **OPENAI_API_KEY** in vault "Development"
- **GITHUB_TOKEN** in vault "Development"

### Use secrets in shell

**Option 1: Run command with secrets injected**
```bash
op run --env-file=.env.1password -- npm run dev
```

Where `.env.1password` contains references:
```
OPENAI_API_KEY=op://Development/OpenAI/credential
GITHUB_TOKEN=op://Development/GitHub Token/credential
```

**Option 2: Export to current shell**
```bash
export OPENAI_API_KEY=$(op read "op://Development/OpenAI/credential")
```

**Option 3: Add to .zshrc (loaded on shell start)**
```bash
# In ~/.zshrc - only runs if op is available
if command -v op &> /dev/null; then
  export OPENAI_API_KEY=$(op read "op://Development/OpenAI/credential" 2>/dev/null)
fi
```

### Reference Format

```
op://VAULT/ITEM/FIELD

Examples:
op://Development/OpenAI/credential
op://Personal/GitHub Token/token
op://Work/AWS/access-key-id
```

## Per-Project Secrets with direnv

For project-specific secrets:

1. Install direnv: `brew install direnv`
2. Add to .zshrc: `eval "$(direnv hook zsh)"`
3. Create `.envrc` in project:
   ```bash
   export OPENAI_API_KEY=$(op read "op://Development/OpenAI/credential")
   ```
4. Allow: `direnv allow`

Secrets auto-load when entering directory, unload when leaving.
