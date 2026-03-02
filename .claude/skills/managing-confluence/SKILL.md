---
name: managing-confluence
description: Manage Confluence pages, spaces, and content via CLI. Creates, reads, updates, searches, and exports documentation. Use when working with Confluence wiki pages, searching documentation, managing page content, or exporting pages to markdown.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata and description
- Level 2 (This file): Overview, quick start, commands
- Level 3: workflows/, examples/

Shared resources (in managing-outlook-email):
- Installation: https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/

-->

# Confluence Content Management

Manage Confluence wiki content via `confluence-cli` - create pages, search documentation, manage attachments, and export content.

## Verify Installation

```bash
# Check tool is available
confluence-cli --version

# Check authentication status
confluence-cli auth status
```

If not authenticated:
1. Generate an API token at https://id.atlassian.com/manage-profile/security/api-tokens
2. Store the token: `confluence-cli auth set-token <your-token>`

## When to Use This Skill

Use this skill when users want to:

- **Read documentation**: Fetch page content by ID, export to markdown
- **Search Confluence**: Find pages by keywords, filter by space or type
- **Create/update pages**: Add new documentation or modify existing pages
- **Manage page metadata**: Add labels, comments, or attachments
- **Export content**: Convert pages to markdown for local use
- **Manage page lifecycle**: Version control, reviews, archival

## Quick Start Examples

### Read a Page
```bash
# Get page by ID (JSON output)
confluence-cli page get 12345

# Get page as markdown (for reading or export)
confluence-cli page get 12345 --format markdown

# Extract just the title
confluence-cli page get 12345 | jq '.title'
```

### Search Content
```bash
# Search all content
confluence-cli search "deployment guide"

# Search within a specific space
confluence-cli search "API documentation" --space ENGINEERING

# Search only pages (not blogs, comments, etc.)
confluence-cli search "onboarding" --type page

# Advanced CQL search
confluence-cli search "type=page AND lastModified >= now('-7d')"
```

### Create and Update Pages
```bash
# Create a new page
confluence-cli page create \
  --space DOCS \
  --title "Feature Documentation" \
  --body "# Overview

This document describes the feature." \
  --format markdown

# Create under a parent page
confluence-cli page create \
  --space DOCS \
  --title "API Reference" \
  --body "# API Reference..." \
  --parent 12345 \
  --format markdown

# Update existing page
confluence-cli page update 12345 \
  --body "# Updated Content

New information..." \
  --format markdown
```

### Manage Labels
```bash
# Add labels for categorization
confluence-cli label add 12345 --label "api-docs"
confluence-cli label add 12345 --label "reviewed"

# List page labels
confluence-cli label list 12345

# Remove a label
confluence-cli label remove 12345 --label "draft"
```

### Export to Markdown
```bash
# Export single page
confluence-cli page get 12345 --format markdown > documentation.md

# Export with metadata
{
  echo "---"
  echo "title: $(confluence-cli page get 12345 --json | jq -r '.title')"
  echo "exported: $(date -Iseconds)"
  echo "---"
  confluence-cli page get 12345 --format markdown
} > doc_with_meta.md
```

Run `confluence-cli --help` for all commands and flags. Run `confluence-cli <command> --help` for detailed options.

## Workflows

1. **Create Documentation** ([workflows/create-documentation.md](workflows/create-documentation.md))
   - Plan documentation structure
   - Create page hierarchies
   - Apply labels and metadata

2. **Search and Export** ([workflows/search-and-export.md](workflows/search-and-export.md))
   - Build effective search queries
   - Export pages to markdown
   - Batch export operations

3. **Page Lifecycle** ([workflows/manage-page-lifecycle.md](workflows/manage-page-lifecycle.md))
   - Update with versioning
   - Review and approval workflows
   - Archive and deprecate content

## Example Scripts

Ready-to-use shell scripts for common tasks:

- **[examples/export-page.sh](examples/export-page.sh)** - Export page with metadata
- **[examples/create-from-markdown.sh](examples/create-from-markdown.sh)** - Create page from markdown file
- **[examples/search-and-list.sh](examples/search-and-list.sh)** - Search and display formatted results

## Troubleshooting

**Authentication fails:**
```bash
# Clear and re-set token
confluence-cli auth logout
confluence-cli auth set-token <your-token>
```

**Command not found:**
- Verify `confluence-cli` is in PATH
- Check installation: `which confluence-cli`
- See [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/)

**Page not found:**
- Verify page ID is correct (numeric ID from URL)
- Check you have access to the space
- Try: `confluence-cli search "page title"` to find ID

**Search returns nothing:**
- Try broader search terms
- Remove space filter to search everywhere
- Check spelling and try partial matches

**Permission denied:**
- You may not have edit rights
- Contact space administrator for access
