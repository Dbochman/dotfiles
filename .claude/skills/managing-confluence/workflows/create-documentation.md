# Create Documentation Workflow

This workflow guides you through creating well-structured documentation in Confluence using `confluence-cli`.

## Workflow Overview

1. Plan the documentation structure
2. Create or identify the parent space/page
3. Create pages with proper hierarchy
4. Add content with formatting
5. Apply labels and metadata
6. Review and finalize

## Step-by-Step Process

### Step 1: Plan the Documentation Structure

**Clarify the documentation needs:**

```
To create effective documentation, I need to understand:

1. What is being documented?
   - Feature/product documentation
   - Process/procedure guide
   - API reference
   - Team wiki/knowledge base

2. Who is the audience?
   - Internal team
   - Cross-functional teams
   - External users
   - All of the above

3. What structure is needed?
   - Single page
   - Page hierarchy (parent + children)
   - Multi-section guide

4. Which space should it live in?
   - Existing space (provide key)
   - New space needed

Please describe what documentation you need to create.
```

**Common documentation structures:**

| Type | Structure |
|------|-----------|
| Feature Guide | Overview → Getting Started → Configuration → Reference → FAQ |
| API Docs | Overview → Authentication → Endpoints → Examples → Errors |
| Process Doc | Purpose → Prerequisites → Steps → Troubleshooting |
| Project Wiki | Home → Architecture → Setup → Development → Deployment |

### Step 2: Identify or Create the Space

**List available spaces:**
```bash
confluence-cli space list --json | jq '.[] | {key: .key, name: .name}'
```

**Check if target space exists:**
```bash
confluence-cli space get DOCS --json | jq '{key: .key, name: .name, homepage: .homepage.id}'
```

**Present options:**
```
Available spaces for your documentation:

1. DOCS - Documentation (general)
2. ENG - Engineering
3. TEAM - Team Wiki
4. PROD - Product Documentation

Which space should I use? Or should this go in a new space?
(Note: Space creation requires admin permissions via Confluence UI)
```

### Step 3: Create the Page Hierarchy

**Create parent page first:**
```bash
confluence-cli page create \
  --space DOCS \
  --title "Feature X Documentation" \
  --body "# Feature X

Welcome to the Feature X documentation.

## Contents

- [Getting Started](child-link)
- [Configuration](child-link)
- [API Reference](child-link)
- [FAQ](child-link)" \
  --format markdown \
  --json
```

**Capture the parent page ID:**
```bash
PARENT_ID=$(confluence-cli page create --space DOCS --title "Feature X Documentation" --body "..." --json | jq -r '.id')
echo "Created parent page: $PARENT_ID"
```

**Create child pages:**
```bash
# Getting Started page
confluence-cli page create \
  --space DOCS \
  --title "Getting Started with Feature X" \
  --parent "$PARENT_ID" \
  --body "# Getting Started

## Prerequisites

- Requirement 1
- Requirement 2

## Quick Start

1. Step one
2. Step two
3. Step three" \
  --format markdown

# Configuration page
confluence-cli page create \
  --space DOCS \
  --title "Feature X Configuration" \
  --parent "$PARENT_ID" \
  --body "# Configuration

## Basic Settings

| Setting | Default | Description |
|---------|---------|-------------|
| option1 | true | Enable feature |
| option2 | 100 | Limit value |" \
  --format markdown

# API Reference page
confluence-cli page create \
  --space DOCS \
  --title "Feature X API Reference" \
  --parent "$PARENT_ID" \
  --body "# API Reference

## Endpoints

### GET /api/feature-x

Returns feature status.

**Response:**
\`\`\`json
{\"status\": \"active\"}
\`\`\`" \
  --format markdown
```

### Step 4: Add Rich Content

**Markdown to Confluence formatting:**

The `--format markdown` flag converts common markdown:

| Markdown | Confluence Result |
|----------|-------------------|
| `# Heading` | h1 macro |
| `## Heading` | h2 macro |
| `**bold**` | strong text |
| `*italic*` | emphasis |
| `` `code` `` | monospace |
| ```` ```code``` ```` | code block |
| `- item` | bullet list |
| `1. item` | numbered list |
| `[text](url)` | link |
| `![alt](url)` | image |
| `| table |` | table |

**Create page from file:**
```bash
# Write content to file first
cat > feature-guide.md << 'EOF'
# Feature X Guide

## Overview

Feature X provides powerful capabilities for...

## Installation

```bash
npm install feature-x
```

## Configuration

Create a config file:

```json
{
  "enabled": true,
  "options": {
    "timeout": 30
  }
}
```

## Usage

### Basic Usage

```javascript
import { FeatureX } from 'feature-x';

const fx = new FeatureX();
fx.start();
```

### Advanced Usage

See [API Reference](./api-reference) for more options.
EOF

# Create page from file
confluence-cli page create \
  --space DOCS \
  --title "Feature X Guide" \
  --body "$(cat feature-guide.md)" \
  --format markdown
```

**Add images and diagrams:**
```bash
# First create the page
PAGE_ID=$(confluence-cli page create --space DOCS --title "Architecture" --body "# Architecture" --json | jq -r '.id')

# Then upload diagram
confluence-cli attachment upload "$PAGE_ID" --file ./architecture-diagram.png

# Update page to reference the image
confluence-cli page update "$PAGE_ID" --body "# Architecture

![Architecture Diagram](/download/attachments/$PAGE_ID/architecture-diagram.png)

## Components

..." --format markdown
```

### Step 5: Apply Labels and Metadata

**Add labels for discoverability:**
```bash
PAGE_ID="12345"

# Add category labels
confluence-cli label add "$PAGE_ID" --label "documentation"
confluence-cli label add "$PAGE_ID" --label "feature-x"
confluence-cli label add "$PAGE_ID" --label "api"

# Add status labels
confluence-cli label add "$PAGE_ID" --label "reviewed"
confluence-cli label add "$PAGE_ID" --label "v2"
```

**Common label conventions:**

| Category | Examples |
|----------|----------|
| Type | `guide`, `reference`, `tutorial`, `faq` |
| Status | `draft`, `review`, `approved`, `deprecated` |
| Team | `platform`, `frontend`, `backend`, `devops` |
| Version | `v1`, `v2`, `2025-q1` |
| Audience | `internal`, `external`, `engineering` |

**Apply labels to all child pages:**
```bash
#!/bin/bash
PARENT_ID="12345"
LABELS=("documentation" "feature-x")

# Get all child pages
confluence-cli search "ancestor=$PARENT_ID" --type page --json | jq -r '.results[].id' | while read -r page_id; do
  for label in "${LABELS[@]}"; do
    confluence-cli label add "$page_id" --label "$label"
  done
  echo "Labeled page $page_id"
done
```

### Step 6: Review and Finalize

**Verify the structure:**
```bash
# List all pages under parent
PARENT_ID="12345"
confluence-cli search "ancestor=$PARENT_ID" --type page --json | jq '.results[] | {id: .id, title: .title}'
```

**Check each page:**
```bash
# Get page in markdown to review
confluence-cli page get 12345 --format markdown
```

**Present summary:**
```
Documentation Created Successfully!
══════════════════════════════════════════

📚 Parent Page: Feature X Documentation (ID: 12345)
   URL: https://company.atlassian.net/wiki/spaces/DOCS/pages/12345

📄 Child Pages:
├── Getting Started with Feature X (ID: 12346)
├── Feature X Configuration (ID: 12347)
├── Feature X API Reference (ID: 12348)
└── Feature X FAQ (ID: 12349)

🏷️ Labels Applied:
- documentation
- feature-x
- api
- reviewed

Would you like to:
1. Add more child pages
2. Update any content
3. Add more labels
4. Share the documentation link
```

## Advanced Scenarios

### Create Documentation from Template

```bash
#!/bin/bash
# create-feature-docs.sh - Create standard feature documentation

SPACE="DOCS"
FEATURE_NAME="$1"

if [ -z "$FEATURE_NAME" ]; then
    echo "Usage: $0 <feature-name>"
    exit 1
fi

echo "Creating documentation for: $FEATURE_NAME"

# Create parent page
PARENT=$(confluence-cli page create \
  --space "$SPACE" \
  --title "$FEATURE_NAME Documentation" \
  --body "# $FEATURE_NAME

Welcome to the $FEATURE_NAME documentation.

## Quick Links

- Getting Started
- Configuration
- API Reference
- FAQ" \
  --format markdown \
  --json)

PARENT_ID=$(echo "$PARENT" | jq -r '.id')
echo "Created parent: $PARENT_ID"

# Create standard child pages
for section in "Getting Started" "Configuration" "API Reference" "FAQ"; do
    confluence-cli page create \
      --space "$SPACE" \
      --title "$FEATURE_NAME - $section" \
      --parent "$PARENT_ID" \
      --body "# $section

Content coming soon..." \
      --format markdown
    echo "Created: $section"
done

# Apply labels
confluence-cli label add "$PARENT_ID" --label "documentation"
confluence-cli label add "$PARENT_ID" --label "$(echo "$FEATURE_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')"

echo "Documentation structure created!"
echo "Parent page: https://company.atlassian.net/wiki/spaces/$SPACE/pages/$PARENT_ID"
```

### Import Markdown Files

```bash
#!/bin/bash
# import-markdown.sh - Import multiple markdown files as pages

SPACE="DOCS"
PARENT_ID="$1"
MARKDOWN_DIR="$2"

if [ -z "$PARENT_ID" ] || [ -z "$MARKDOWN_DIR" ]; then
    echo "Usage: $0 <parent-page-id> <markdown-directory>"
    exit 1
fi

find "$MARKDOWN_DIR" -name "*.md" | while read -r md_file; do
    # Extract title from first heading or filename
    TITLE=$(head -1 "$md_file" | sed 's/^#\s*//')
    if [ -z "$TITLE" ]; then
        TITLE=$(basename "$md_file" .md)
    fi
    
    echo "Importing: $TITLE"
    
    confluence-cli page create \
      --space "$SPACE" \
      --title "$TITLE" \
      --parent "$PARENT_ID" \
      --body "$(cat "$md_file")" \
      --format markdown
done

echo "Import complete!"
```

### Create Documentation with Diagrams

```bash
#!/bin/bash
# create-architecture-doc.sh

SPACE="DOCS"
TITLE="System Architecture"

# Create page first
PAGE=$(confluence-cli page create \
  --space "$SPACE" \
  --title "$TITLE" \
  --body "# $TITLE

(Diagram will be added)

## Overview

Description of the architecture..." \
  --format markdown \
  --json)

PAGE_ID=$(echo "$PAGE" | jq -r '.id')

# Upload diagram
confluence-cli attachment upload "$PAGE_ID" --file ./architecture.png --comment "Architecture diagram v1"

# Update page with embedded image
confluence-cli page update "$PAGE_ID" --body "# $TITLE

![Architecture](/download/attachments/$PAGE_ID/architecture.png)

## Overview

The system consists of the following components:

1. **Frontend** - React application
2. **API Gateway** - Kong
3. **Backend Services** - Kubernetes pods
4. **Database** - PostgreSQL

## Component Details

..." --format markdown

echo "Created architecture doc with diagram: $PAGE_ID"
```

## Error Handling

### Space Not Found
```
Error: Space 'INVALID' not found.

Available spaces:
$(confluence-cli space list --json | jq -r '.[] | "  \(.key) - \(.name)"')

Please specify a valid space key.
```

### Permission Denied
```
Error: Permission denied for space 'RESTRICTED'.

You don't have permission to create pages in this space.
Options:
1. Request access from space administrator
2. Use a different space
3. Create in your personal space

Your personal space key: ~username
```

### Title Already Exists
```
Error: Page with title 'Feature X' already exists in space DOCS.

Options:
1. Use a different title
2. Update the existing page instead:
   confluence-cli page update <existing-id> --body "..."
3. Delete the existing page first (if appropriate)

Existing page ID: 12345
```

## Example Conversations

### Simple Page Creation

**User:** Create a page documenting our new authentication system

**Assistant:** I'll create documentation for the authentication system. First, let me check available spaces...

```
Available spaces:
1. DOCS - Documentation
2. ENG - Engineering
3. SECURITY - Security

Which space should I use for the auth system documentation?
```

**User:** Use ENG

**Assistant:** Creating the authentication documentation in ENG space...

```
Created: Authentication System Documentation
══════════════════════════════════════════

📄 Page Details:
- Title: Authentication System
- Space: ENG
- ID: 12345
- URL: https://company.atlassian.net/wiki/spaces/ENG/pages/12345

Would you like me to:
1. Add sections (Overview, Setup, API Reference)?
2. Create child pages for each section?
3. Add labels for discoverability?
```

### Full Documentation Structure

**User:** Create complete API documentation for the User Service with getting started, endpoints, and examples

**Assistant:** I'll create a complete API documentation structure for User Service...

```
Creating User Service API Documentation
══════════════════════════════════════════

📁 Structure Created:

User Service API (Parent)
├── Getting Started
│   - Prerequisites
│   - Authentication
│   - Quick Start
├── API Endpoints
│   - Users
│   - Profiles
│   - Permissions
├── Examples
│   - Common Use Cases
│   - Error Handling
└── Changelog

📊 Summary:
- Pages created: 8
- Labels applied: api-docs, user-service, v1
- Space: ENG

🔗 Main page URL:
https://company.atlassian.net/wiki/spaces/ENG/pages/12345

Would you like to add content to any specific page?
```

## Tips for Documentation Creation

1. **Plan structure first**: Map out hierarchy before creating pages
2. **Use consistent naming**: Follow team conventions for titles
3. **Apply labels consistently**: Aids discoverability
4. **Include navigation**: Add links between related pages
5. **Use templates**: Create reusable structures for common doc types
6. **Version appropriately**: Use labels like `v1`, `v2` for major versions
7. **Add metadata**: Include author, date, status in page content
8. **Review before publishing**: Check formatting and links
