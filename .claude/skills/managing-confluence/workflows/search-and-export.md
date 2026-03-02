# Search and Export Workflow

This workflow guides you through finding, retrieving, and exporting Confluence content using `confluence-cli`.

## Workflow Overview

1. Understand the search requirements
2. Build and execute search queries
3. Review and filter results
4. Export content in desired format
5. Process and organize exports

## Step-by-Step Process

### Step 1: Understand Search Requirements

**Clarify what to find:**

```
To help search Confluence effectively, I need to understand:

1. What are you looking for?
   - Specific topic or keyword
   - Documentation by a team
   - Pages in a specific space
   - Recently modified content

2. What scope?
   - All of Confluence
   - Specific space(s)
   - By author
   - By date range

3. What will you do with results?
   - Read and review
   - Export to markdown
   - Update content
   - Archive/delete

Please describe what you're searching for.
```

**Common search scenarios:**

| Scenario | Approach |
|----------|----------|
| Find specific doc | Text search + space filter |
| Find all team docs | Label or space filter |
| Find recent changes | CQL date filter |
| Find by author | CQL creator filter |
| Find outdated docs | CQL lastModified filter |

### Step 2: Build Search Queries

**Simple text search:**
```bash
# Basic keyword search
confluence-cli search "authentication guide"

# Search in specific space
confluence-cli search "deployment" --space ENG

# Search specific content type
confluence-cli search "API" --type page
```

**Advanced CQL queries:**
```bash
# Pages modified in last 7 days
confluence-cli search "type=page AND lastModified >= now('-7d')"

# Pages by specific author
confluence-cli search "creator = 'jdoe@company.com' AND type=page"

# Pages with specific label
confluence-cli search "label = 'api-docs' AND space = ENG"

# Pages matching title pattern
confluence-cli search "type=page AND title ~ 'API*'"

# Recently created in a space
confluence-cli search "space = DOCS AND created >= now('-30d') AND type=page"

# Combine multiple conditions
confluence-cli search "type=page AND space IN (DOCS, ENG) AND label = 'reviewed'"
```

**CQL Reference:**

| Operator | Description | Example |
|----------|-------------|---------|
| `=` | Exact match | `space = DOCS` |
| `~` | Contains/pattern | `title ~ 'API*'` |
| `IN` | Multiple values | `space IN (DOCS, ENG)` |
| `>=`, `<=` | Comparison | `created >= now('-7d')` |
| `AND`, `OR` | Logical | `type=page AND space=DOCS` |
| `now()` | Current time | `now('-30d')` = 30 days ago |

### Step 3: Review and Filter Results

**Get search results:**
```bash
# Search with JSON output for processing
RESULTS=$(confluence-cli search "API documentation" --space ENG --json)

# Count results
echo "$RESULTS" | jq '.totalSize'

# List titles
echo "$RESULTS" | jq -r '.results[].title'

# Get IDs and titles
echo "$RESULTS" | jq '.results[] | {id: .id, title: .title, space: .space.key}'
```

**Filter results with jq:**
```bash
# Find pages with specific word in title
confluence-cli search "guide" --json | jq '.results[] | select(.title | test("Setup"; "i"))'

# Sort by title
confluence-cli search "documentation" --json | jq '.results | sort_by(.title)'

# Get unique spaces from results
confluence-cli search "API" --json | jq '[.results[].space.key] | unique'

# Filter by excerpt content
confluence-cli search "configuration" --json | jq '.results[] | select(.excerpt | test("database"; "i"))'
```

**Present results:**
```
Search Results: "API documentation" in ENG
══════════════════════════════════════════

Found 15 pages matching your search:

1. API Authentication Guide
   Space: ENG | ID: 12345
   Excerpt: "...API authentication using OAuth2..."
   
2. REST API Reference
   Space: ENG | ID: 12346
   Excerpt: "...complete REST API reference for..."

3. API Rate Limiting
   Space: ENG | ID: 12347
   Excerpt: "...rate limiting policies for API calls..."

[... more results ...]

Total: 15 results

Would you like to:
1. Read a specific page
2. Export all results to markdown
3. Refine the search
4. See more results
```

### Step 4: Export Content

**Export single page to markdown:**
```bash
# Get page as markdown
confluence-cli page get 12345 --format markdown > page.md

# With metadata header
{
  echo "---"
  echo "title: $(confluence-cli page get 12345 --json | jq -r '.title')"
  echo "id: 12345"
  echo "exported: $(date -Iseconds)"
  echo "---"
  echo ""
  confluence-cli page get 12345 --format markdown
} > page_with_meta.md
```

**Export multiple pages:**
```bash
#!/bin/bash
# export-search-results.sh - Export all search results to markdown

QUERY="$1"
OUTPUT_DIR="${2:-.}"

if [ -z "$QUERY" ]; then
    echo "Usage: $0 <search-query> [output-dir]"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "Searching: $QUERY"
RESULTS=$(confluence-cli search "$QUERY" --type page --limit 100 --json)
COUNT=$(echo "$RESULTS" | jq '.results | length')
echo "Found $COUNT pages"

echo "$RESULTS" | jq -r '.results[] | "\(.id)|\(.title)"' | while IFS='|' read -r id title; do
    # Sanitize filename
    filename=$(echo "$title" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
    
    echo "Exporting: $title -> $filename.md"
    
    # Export with metadata
    {
        echo "---"
        echo "title: \"$title\""
        echo "page_id: $id"
        echo "exported: $(date -Iseconds)"
        echo "---"
        echo ""
        confluence-cli page get "$id" --format markdown
    } > "$OUTPUT_DIR/$filename.md"
done

echo "Exported $COUNT pages to $OUTPUT_DIR"
```

**Export space documentation:**
```bash
#!/bin/bash
# export-space.sh - Export all pages from a space

SPACE="$1"
OUTPUT_DIR="${2:-./$SPACE}"

if [ -z "$SPACE" ]; then
    echo "Usage: $0 <space-key> [output-dir]"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "Exporting space: $SPACE"

# Get all pages in space
PAGES=$(confluence-cli search "space=$SPACE AND type=page" --limit 500 --json)
COUNT=$(echo "$PAGES" | jq '.results | length')
echo "Found $COUNT pages"

# Create index file
{
    echo "# $SPACE Space Export"
    echo ""
    echo "Exported: $(date)"
    echo "Total pages: $COUNT"
    echo ""
    echo "## Pages"
    echo ""
} > "$OUTPUT_DIR/INDEX.md"

# Export each page
echo "$PAGES" | jq -r '.results[] | "\(.id)|\(.title)"' | while IFS='|' read -r id title; do
    filename=$(echo "$title" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
    
    echo "Exporting: $title"
    
    confluence-cli page get "$id" --format markdown > "$OUTPUT_DIR/$filename.md"
    
    # Add to index
    echo "- [$title]($filename.md)" >> "$OUTPUT_DIR/INDEX.md"
done

echo "Export complete: $OUTPUT_DIR"
```

### Step 5: Process and Organize Exports

**Create structured export with hierarchy:**
```bash
#!/bin/bash
# export-with-hierarchy.sh - Export preserving page hierarchy

PARENT_ID="$1"
OUTPUT_DIR="${2:-.}"

export_children() {
    local parent=$1
    local dir=$2
    
    mkdir -p "$dir"
    
    # Get children of this page
    CHILDREN=$(confluence-cli search "parent=$parent AND type=page" --json)
    
    echo "$CHILDREN" | jq -r '.results[] | "\(.id)|\(.title)"' | while IFS='|' read -r id title; do
        filename=$(echo "$title" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
        
        # Export page
        confluence-cli page get "$id" --format markdown > "$dir/$filename.md"
        
        # Check for children
        HAS_CHILDREN=$(confluence-cli search "parent=$id AND type=page" --json | jq '.results | length')
        
        if [ "$HAS_CHILDREN" -gt 0 ]; then
            # Create subdirectory for children
            export_children "$id" "$dir/$filename"
        fi
    done
}

# Export starting page
PAGE=$(confluence-cli page get "$PARENT_ID" --json)
TITLE=$(echo "$PAGE" | jq -r '.title')
ROOT_DIR="$OUTPUT_DIR/$(echo "$TITLE" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')"

mkdir -p "$ROOT_DIR"
confluence-cli page get "$PARENT_ID" --format markdown > "$ROOT_DIR/README.md"

# Export children recursively
export_children "$PARENT_ID" "$ROOT_DIR"

echo "Export complete: $ROOT_DIR"
```

**Combine exports into single document:**
```bash
#!/bin/bash
# combine-exports.sh - Combine multiple pages into single document

SEARCH_QUERY="$1"
OUTPUT_FILE="${2:-combined.md}"

echo "Searching and combining pages..."

{
    echo "# Combined Documentation"
    echo ""
    echo "Generated: $(date)"
    echo ""
    echo "---"
    echo ""
} > "$OUTPUT_FILE"

confluence-cli search "$SEARCH_QUERY" --type page --limit 50 --json | \
  jq -r '.results | sort_by(.title) | .[] | "\(.id)|\(.title)"' | while IFS='|' read -r id title; do
    
    echo "Adding: $title"
    
    {
        echo "# $title"
        echo ""
        confluence-cli page get "$id" --format markdown | tail -n +2  # Skip first heading
        echo ""
        echo "---"
        echo ""
    } >> "$OUTPUT_FILE"
done

echo "Combined document: $OUTPUT_FILE"
```

## Advanced Scenarios

### Find and Update Outdated Documentation

```bash
#!/bin/bash
# find-outdated.sh - Find pages not updated in 6 months

echo "Finding outdated documentation..."
echo "════════════════════════════════════════"

confluence-cli search "type=page AND lastModified <= now('-180d') AND space=DOCS" --json | \
  jq -r '.results[] | "\(.id)|\(.title)|\(.version.when)"' | while IFS='|' read -r id title modified; do
    echo "📄 $title"
    echo "   ID: $id"
    echo "   Last modified: $modified"
    echo "   Action needed: Review and update or archive"
    echo ""
done
```

### Bulk Search Across Spaces

```bash
#!/bin/bash
# search-all-spaces.sh - Search across multiple spaces

QUERY="$1"
SPACES=("DOCS" "ENG" "PLATFORM" "DEVOPS")

echo "Searching for '$QUERY' across ${#SPACES[@]} spaces..."
echo "════════════════════════════════════════"

for space in "${SPACES[@]}"; do
    RESULTS=$(confluence-cli search "$QUERY" --space "$space" --json 2>/dev/null)
    COUNT=$(echo "$RESULTS" | jq '.results | length')
    
    if [ "$COUNT" -gt 0 ]; then
        echo ""
        echo "📁 $space: $COUNT results"
        echo "$RESULTS" | jq -r '.results[] | "   - \(.title) (ID: \(.id))"'
    fi
done

echo ""
echo "════════════════════════════════════════"
TOTAL=$(confluence-cli search "$QUERY" --json | jq '.totalSize')
echo "Total across all spaces: $TOTAL"
```

### Export with Attachments

```bash
#!/bin/bash
# export-with-attachments.sh - Export page with all attachments

PAGE_ID="$1"
OUTPUT_DIR="${2:-.}"

mkdir -p "$OUTPUT_DIR/attachments"

# Export page content
echo "Exporting page content..."
confluence-cli page get "$PAGE_ID" --format markdown > "$OUTPUT_DIR/page.md"

# Get and download attachments
echo "Downloading attachments..."
confluence-cli attachment list "$PAGE_ID" --json | jq -r '.[].title' | while read -r filename; do
    echo "  Downloading: $filename"
    confluence-cli attachment download "$PAGE_ID" "$filename" --output "$OUTPUT_DIR/attachments/"
done

# Update image references in markdown
echo "Updating image references..."
sed -i.bak 's|/download/attachments/[0-9]*/|./attachments/|g' "$OUTPUT_DIR/page.md"
rm "$OUTPUT_DIR/page.md.bak"

echo "Export complete: $OUTPUT_DIR"
```

## Error Handling

### No Results Found
```
No pages found matching your search.

Suggestions:
1. Check spelling of search terms
2. Try broader search (remove filters)
3. Check if content is in a space you have access to
4. Try alternative keywords

Example broader search:
  confluence-cli search "authentication"
Instead of:
  confluence-cli search "OAuth2 authentication configuration"
```

### Export Failures
```
Failed to export page 12345: permission denied

Some pages may have restricted access. Skipping...

Successfully exported: 15/18 pages
Failed: 3 pages (permission denied)

Failed pages:
- Restricted Document (12345)
- Confidential Guide (12346)
- Private Notes (12347)
```

### Rate Limiting During Export
```bash
#!/bin/bash
# Rate-limit safe export

export_with_retry() {
    local id=$1
    local output=$2
    local retries=3
    
    for i in $(seq 1 $retries); do
        if confluence-cli page get "$id" --format markdown > "$output" 2>/dev/null; then
            return 0
        fi
        echo "Retry $i/$retries for page $id..."
        sleep 5
    done
    return 1
}
```

## Example Conversations

### Simple Search

**User:** Find all API documentation

**Assistant:** Searching for API documentation...

```
Search Results: "API"
══════════════════════════════════════════

Found 23 pages:

By Space:
├── ENG (12 pages)
│   ├── REST API Reference
│   ├── API Authentication
│   ├── API Rate Limiting
│   └── [9 more...]
├── DOCS (8 pages)
│   ├── API Getting Started
│   ├── API Best Practices
│   └── [6 more...]
└── PLATFORM (3 pages)
    ├── Platform API Overview
    └── [2 more...]

Would you like to:
1. Read a specific page
2. Export all API docs to markdown
3. Search within a specific space
4. See full list with details
```

### Export Request

**User:** Export all the ENG space documentation to markdown

**Assistant:** I'll export all pages from the ENG space...

```
Exporting ENG Space
══════════════════════════════════════════

Scanning space... Found 45 pages

Exporting:
[============================] 45/45 pages

✅ Export Complete!

Output directory: ./ENG-export/
├── INDEX.md (table of contents)
├── api-reference.md
├── deployment-guide.md
├── architecture-overview.md
└── [42 more files...]

Total size: 2.3 MB
Pages exported: 45

The INDEX.md file contains a linked table of contents.

Would you like me to create a combined single document instead?
```

## Tips for Search and Export

1. **Start broad, then narrow**: Begin with simple searches, add filters as needed
2. **Use CQL for precision**: Learn CQL syntax for complex queries
3. **Check access first**: Some content may be restricted
4. **Export incrementally**: For large spaces, export in batches
5. **Preserve metadata**: Include page ID, export date in frontmatter
6. **Handle attachments**: Download images/files separately if needed
7. **Verify exports**: Spot-check exported markdown for formatting issues
8. **Organize output**: Use meaningful filenames and directory structure
