---
name: managing-navigator-customers
description: Manage customers, initiatives, and capacity allocations in Navigator. Lists customers, service providers, initiatives, node allocations, contracts, and block deliverables. Use when checking who has capacity, viewing customer allocations, tracking initiatives, or managing supply contracts.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata
- Level 2 (This file): Overview, quick start, commands
- Level 3: navigator-cli --help for detailed flags

Related skills:
- tracking-gpu-capacity: For cross-provider capacity overview
- exploring-oci-infrastructure: For OCI-specific resources
-->

# Navigator Customer Management

Manage customers, initiatives, and capacity allocations via `navigator-cli` - track who has capacity, view allocations, and monitor contracts.

## Verify Installation

```bash
# Check tool is available
navigator-cli --version

# Test API connectivity
navigator-cli customer list --json | head -20
```

If command not found, build from source: `cd ai-pim-utils && make build`

## When to Use This Skill

Use this skill when users want to:

- **View customers**: List all customers, service providers, service users
- **Track initiatives**: Active projects and their owners
- **Check allocations**: Who has node allocations and how much
- **Monitor contracts**: Active supply contracts and deliverables
- **Find users**: Navigator system users and their details
- **Understand relationships**: Customer → Initiative → Allocation mapping

## Quick Start Examples

### Customer Management
```bash
# List all customers
navigator-cli customer list --json

# Get specific customer by ID
navigator-cli customer get 20 --json

# List service users only
navigator-cli customer service-users --json
```

### Initiatives
```bash
# List all initiatives
navigator-cli initiative list --json

# Get specific initiative
navigator-cli initiative get 15 --json
```

### Node Allocations
```bash
# List all node allocations
navigator-cli node-allocation list --json

# Get specific allocation
navigator-cli node-allocation get 1699 --json

# Use v1.1 API version
navigator-cli node-allocation list --version v1.1 --json
```

### Contracts and Deliverables
```bash
# List all contracts
navigator-cli contract list --json

# List block deliverables
navigator-cli block-deliverable list --json

# Get specific deliverable
navigator-cli block-deliverable get 430 --json
```

### Users
```bash
# List all Navigator users
navigator-cli user list --json
```

## Understanding the Data Model

```
Customer (service provider or service user)
    │
    ├── Initiative (project/program)
    │       │
    │       └── Node Allocation (capacity assigned)
    │
    └── Contract (supply agreement)
            │
            └── Block Deliverable (contracted delivery)
```

### Customer Types
- **SERVICE_PROVIDER**: Internal teams providing GPU capacity
- **SERVICE_USER**: Teams consuming GPU capacity
- **INITIATIVE**: Project-based allocation

## Common Queries

### Find Customer's Allocations
```bash
# Get customer ID first
navigator-cli customer list --json | jq '.data[] | select(.name | contains("LLM"))'

# Then check allocations for that customer
navigator-cli node-allocation list --json | jq '[.data[] | select(.customer_id == "15")]'
```

### Active Initiatives with Owners
```bash
navigator-cli initiative list --json | jq '.data[] | {name, id, description}'
```

### Allocations by Status
```bash
navigator-cli node-allocation list --json | jq '.data | group_by(.status) | map({status: .[0].status, count: length})'
```

### Contracts by Status
```bash
navigator-cli contract list --json | jq '.data | group_by(.status) | map({status: .[0].status, count: length})'
```

### Find User by Email
```bash
navigator-cli user list --json | jq '.data[] | select(.email | contains("jsmith"))'
```

## Workflows

1. **Customer Lookup**: Find customer and their allocations
   - Search customer list by name
   - Get customer details by ID
   - Query node allocations for that customer

2. **Initiative Review**: Track active initiatives
   - List all initiatives
   - Check initiative owners and descriptions
   - Cross-reference with allocations

3. **Contract Audit**: Review supply contracts
   - List active contracts
   - Check contract dates and status
   - Review associated block deliverables

## Troubleshooting

**Command not found:**
```bash
cd ai-pim-utils && make build
```

**Customer not found:**
- IDs are strings (e.g., "20" not 20)
- Use `customer list` to find valid IDs
- Check spelling for name searches

**Empty allocations:**
- Some customers may have no active allocations
- Check allocation status (Active, Transferred, Withdrawn)

**API errors:**
- Check network connectivity
- Try with `--verbose` for debug info
- Some endpoints may have server-side issues
