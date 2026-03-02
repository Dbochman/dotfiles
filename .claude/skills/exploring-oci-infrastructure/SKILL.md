---
name: exploring-oci-infrastructure
description: Explore Oracle Cloud Infrastructure resources in Navigator. Lists HPC islands, regions, availability domains, compartments, shapes, and tenants. Use when investigating OCI infrastructure, finding HPC islands, checking regional capacity, or understanding OCI topology.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata
- Level 2 (This file): Overview, quick start, commands
- Level 3: navigator-cli --help for detailed flags

Related skills:
- tracking-gpu-capacity: For cross-provider capacity overview
- managing-navigator-customers: For customer allocations
-->

# OCI Infrastructure Explorer

Explore Oracle Cloud Infrastructure resources via `navigator-cli` - view HPC islands, regions, availability domains, compartments, shapes, and tenants.

## Verify Installation

```bash
# Check tool is available
navigator-cli --version

# Test API connectivity
navigator-cli oci region list --json | head -20
```

If command not found, build from source: `cd ai-pim-utils && make build`

## When to Use This Skill

Use this skill when users want to:

- **Explore HPC islands**: GPU cluster locations within availability domains
- **View regions**: OCI regions with GPU capacity
- **Check availability domains**: ADs within regions
- **Browse compartments**: OCI compartment hierarchy
- **Find shapes**: Available GPU instance types (BM.GPU.H100.8, etc.)
- **List tenants**: OCI tenancy information

## OCI Topology Overview

```
Tenant (nvidiangc, nvidiangcnonprd)
    │
    └── Region (us-ashburn-1, ap-tokyo-1, etc.)
            │
            └── Availability Domain (AD-1, AD-2, AD-3)
                    │
                    └── HPC Island (bldg8-block39, etc.)
                            │
                            └── Reservation (capacity allocation)
```

## Quick Start Examples

### Regions
```bash
# List all OCI regions
navigator-cli oci region list --json

# Regions have: id, region_name, region_identifier, city, country
```

### Availability Domains
```bash
# List all availability domains
navigator-cli oci availability-domain list --json

# Fields: id, region_identifier, availability_domains, tenant_id
```

### HPC Islands
```bash
# List all HPC islands
navigator-cli oci hpc-island list --json

# With region info
navigator-cli oci hpc-island list --with-region --json

# Filter by availability domain ID
navigator-cli oci hpc-island list --availability-domain 5 --json
```

### Compartments
```bash
# List all compartments
navigator-cli oci compartment list --json

# Subcompartments
navigator-cli oci subcompartment list --json

# Get specific compartment
navigator-cli oci subcompartment get 186 --json
```

### Shapes (Instance Types)
```bash
# List all GPU shapes
navigator-cli oci shape list --json

# Common shapes:
# - BM.GPU.H100.8 (8x H100)
# - BM.GPU.A100-v2.8 (8x A100)
# - BM.GPU.L40S.4 (4x L40S)
```

### Tenants
```bash
# List OCI tenants
navigator-cli oci tenant list --json

# Primary tenants: nvidiangc, nvidiangcnonprd
```

### Clusters
```bash
# OCI clusters
navigator-cli oci cluster list --json
```

## Common Queries

### Find HPC Islands in a Region
```bash
# First find region's availability domains
navigator-cli oci availability-domain list --json | jq '[.data[] | select(.region_identifier == "us-ashburn-1")]'

# Then find HPC islands (need AD ID)
navigator-cli oci hpc-island list --availability-domain 3 --json
```

### Shapes by HPC Island
```bash
navigator-cli oci shape list --json | jq '.data | group_by(.hpc_island_id) | map({hpc_island: .[0].hpc_island_id, shapes: [.[].shape] | unique})'
```

### Regions with H100 Capacity
```bash
# Get reservations with H100
navigator-cli oci reservation list --json | jq '[.data[] | select(.instance_shape | contains("H100"))] | [.[].region] | unique'
```

### Compartment Hierarchy
```bash
navigator-cli oci compartment list --json | jq '.data[] | {id, name, parent_id, tenant_id}'
```

### Count Resources by Region
```bash
navigator-cli oci reservation list --json | jq '.data | group_by(.region) | map({region: .[0].region, reservations: length, total_instances: (map(.reserved_instance_count) | add)})'
```

## Workflows

1. **Region Exploration**: Understand OCI regional topology
   - List regions to find target region
   - Get availability domains in that region
   - Find HPC islands within ADs

2. **Capacity Investigation**: Find where GPU capacity exists
   - List shapes to see instance types
   - Check which HPC islands have target shapes
   - Cross-reference with reservations

3. **Compartment Navigation**: Explore OCI organization
   - List tenants (nvidiangc, nvidiangcnonprd)
   - Browse compartment hierarchy
   - Find specific compartment details

## Troubleshooting

**Command not found:**
```bash
cd ai-pim-utils && make build
```

**Empty HPC island list:**
- Try without `--availability-domain` filter first
- Verify AD ID exists: `navigator-cli oci availability-domain list --json`

**Region not found:**
- Use `region_identifier` (e.g., "us-ashburn-1") not display name
- Check available regions: `navigator-cli oci region list --json`

**API errors:**
- Check network connectivity
- Try with `--verbose` for debug info
- Some OCI endpoints may be slow
