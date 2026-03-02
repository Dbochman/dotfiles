---
name: tracking-gpu-capacity
description: Query GPU capacity across OCI, Azure, GCP, AWS, CoreWeave, and Forge via Navigator CLI. Lists reservations, clusters, GPU status, and availability by region. Use when checking GPU availability, viewing capacity reservations, monitoring cloud resources, or comparing capacity across providers.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata
- Level 2 (This file): Overview, quick start, commands
- Level 3: navigator-cli --help for detailed flags

Related skills:
- managing-navigator-customers: For customer/initiative allocations
- exploring-oci-infrastructure: For OCI-specific deep dives
-->

# GPU Capacity Tracking

Query GPU capacity across multiple cloud providers via `navigator-cli` - view reservations, clusters, GPU availability, and regional capacity.

## Verify Installation

```bash
# Check tool is available
navigator-cli --version

# Test API connectivity (no auth required for read operations)
navigator-cli gpu list --json
```

If command not found, build from source: `cd ai-pim-utils && make build`

## When to Use This Skill

Use this skill when users want to:

- **Check GPU availability**: View H100, A100, L40S capacity across providers
- **List reservations**: OCI capacity reservations by region/tenant
- **View clusters**: Active clusters across all providers
- **Monitor provider status**: GCP health, CoreWeave GPU status
- **Compare capacity**: Cross-provider capacity overview
- **Find regional resources**: What's available in specific regions

## Quick Start Examples

### List GPU Types
```bash
# All GPU configurations
navigator-cli gpu list --json

# Human-readable output
navigator-cli gpu list
```

### OCI Reservations (Primary Provider)
```bash
# All OCI reservations
navigator-cli oci reservation list --json

# Filter by region
navigator-cli oci reservation list --region us-ashburn-1 --json

# Filter by tenant
navigator-cli oci reservation list --tenant nvidiangc --json

# Limit results
navigator-cli oci reservation list -l 20 --json
```

### Clusters Across Providers
```bash
# All clusters
navigator-cli cluster list --json

# OCI-specific clusters
navigator-cli oci cluster list --json
```

### Azure Resources
```bash
# Azure subscriptions
navigator-cli azure subscription list --json

# Azure group quotas
navigator-cli azure group-quota list --json
```

### GCP Resources
```bash
# GCP projects
navigator-cli gcp project list --json

# GCP reservations
navigator-cli gcp reservation list --json

# GCP health status
navigator-cli gcp health --json
```

### AWS Resources
```bash
# AWS accounts
navigator-cli aws account list --json
```

### CoreWeave GPU Status
```bash
# GPU status by region (DFW, LAS1, PDX, etc.)
navigator-cli coreweave gpu-status list --json
```

### Forge Tenants
```bash
# Forge platform tenants
navigator-cli forge tenant list --json
```

### Cross-Provider Analysis
```bash
# Get all reservations and cluster counts
echo "=== OCI Reservations ==="
navigator-cli oci reservation list --json | jq 'length'

echo "=== GCP Reservations ==="
navigator-cli gcp reservation list --json | jq '.data | length'

echo "=== Clusters ==="
navigator-cli cluster list --json | jq '.data | length'
```

Run `navigator-cli --help` for all commands. Run `navigator-cli <provider> --help` for provider-specific options.

## Workflows

1. **Capacity Overview**: Check GPU availability across all providers
   - Run `navigator-cli gpu list` for GPU types
   - Check each provider's reservations/status
   - Compare regional capacity

2. **Regional Deep Dive**: Investigate specific region capacity
   - Filter reservations by region
   - Check cluster availability
   - Review provider health status

3. **Provider Comparison**: Compare capacity across CSPs
   - Query each provider's reservation/project list
   - Aggregate counts with jq
   - Identify capacity gaps

## Common Queries

### Find H100 Capacity
```bash
navigator-cli oci reservation list --json | jq '[.data[] | select(.instance_shape | contains("H100"))] | length'
```

### Reservations by Region
```bash
navigator-cli oci reservation list --json | jq '.data | group_by(.region) | map({region: .[0].region, count: length})'
```

### Active CoreWeave Regions
```bash
navigator-cli coreweave gpu-status list --json | jq '[.data[].region] | unique'
```

## Troubleshooting

**Command not found:**
```bash
# Build from source
cd ai-pim-utils && make build
# Binary is in ./bin/navigator-cli
```

**API errors:**
- Check network connectivity to api.navigator.server.nvidia.com
- Some endpoints may return 500 (server-side issues)
- Try with `--verbose` for debug info

**Empty results:**
- Try without filters first
- Check filter spelling (case-sensitive)
- Some resources may have no data

**JSON parsing:**
- All list commands support `--json` for structured output
- Use `jq` for filtering and transformation
