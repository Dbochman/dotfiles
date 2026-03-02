---
name: triaging-cases
description: Triages and prioritizes Salesforce support cases. Queries by priority, status, product, finds related cases, tracks SLA. Use when reviewing case queues, escalating P1 issues, finding similar cases, or analyzing support patterns.
---

# Case Triage

Triage and manage Salesforce support cases via `sf` CLI.

## Setup

```bash
# Requires: sf (Salesforce CLI)
sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com
```

## Quick Reference

All commands require `--target-org nvcrm-dev` (or `-o nvcrm-dev`).

### Open Cases by Priority

```bash
# P1 cases (critical)
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate, Account.Name FROM Case WHERE Priority = 'P1' AND IsClosed = false ORDER BY CreatedDate"

# P2 cases (high)
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate FROM Case WHERE Priority = 'P2' AND IsClosed = false ORDER BY CreatedDate LIMIT 30"

# All open by priority
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate FROM Case WHERE IsClosed = false ORDER BY Priority, CreatedDate LIMIT 50"
```

### Cases by Status

```bash
# New cases
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, CreatedDate FROM Case WHERE Status = 'New' ORDER BY Priority, CreatedDate LIMIT 30"

# In Progress
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, Account.Name FROM Case WHERE Status = 'In Progress' ORDER BY Priority LIMIT 30"

# Escalated cases
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, Status, Account.Name FROM Case WHERE IsEscalated = true AND IsClosed = false"
```

### Search Cases

```bash
# By keyword in subject
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority FROM Case WHERE Subject LIKE '%ConnectX%' ORDER BY CreatedDate DESC LIMIT 20"

# By account
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate FROM Case WHERE AccountId = '001xxxx' ORDER BY CreatedDate DESC LIMIT 20"

# By contact email
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority FROM Case WHERE SuppliedEmail LIKE '%@company.com' ORDER BY CreatedDate DESC LIMIT 20"
```

### Case Aging

```bash
# Open cases older than 7 days
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, Status, CreatedDate FROM Case WHERE IsClosed = false AND CreatedDate < LAST_N_DAYS:7 ORDER BY CreatedDate LIMIT 30"

# Stale cases (no update in 3+ days)
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, LastModifiedDate FROM Case WHERE IsClosed = false AND LastModifiedDate < LAST_N_DAYS:3 ORDER BY Priority LIMIT 30"
```

### Find Related Cases

```bash
# Same account
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate FROM Case WHERE AccountId = '001xxxx' ORDER BY CreatedDate DESC LIMIT 10"

# Similar subject
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Account.Name FROM Case WHERE Subject LIKE '%error message%' ORDER BY CreatedDate DESC LIMIT 15"

# Same product issue
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, Priority FROM Case WHERE Subject LIKE '%vGPU%' AND IsClosed = false"
```

### Case Statistics

```bash
# Count by status
sf data query -o nvcrm-dev --query "SELECT Status, COUNT(Id) cnt FROM Case WHERE IsClosed = false GROUP BY Status"

# Count by priority
sf data query -o nvcrm-dev --query "SELECT Priority, COUNT(Id) cnt FROM Case WHERE IsClosed = false GROUP BY Priority ORDER BY cnt DESC"

# Created this week
sf data query -o nvcrm-dev --query "SELECT Priority, COUNT(Id) cnt FROM Case WHERE CreatedDate = THIS_WEEK GROUP BY Priority"
```

### Update Cases

```bash
# Update status
sf data update record -o nvcrm-dev --sobject Case --record-id 500xxxx --values "Status='In Progress'"

# Update priority
sf data update record -o nvcrm-dev --sobject Case --record-id 500xxxx --values "Priority='P1'"

# Escalate
sf data update record -o nvcrm-dev --sobject Case --record-id 500xxxx --values "IsEscalated=true Priority='P1'"
```

## Workflows

### Morning Triage

```
Progress:
- [ ] Check P1 cases
- [ ] Review new cases
- [ ] Find stale cases
- [ ] Check escalations
```

```bash
# 1. P1 cases
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Status, CreatedDate, Account.Name FROM Case WHERE Priority = 'P1' AND IsClosed = false"

# 2. New cases
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, CreatedDate FROM Case WHERE Status = 'New' ORDER BY Priority, CreatedDate LIMIT 20"

# 3. Stale (no update 3+ days)
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, LastModifiedDate FROM Case WHERE IsClosed = false AND LastModifiedDate < LAST_N_DAYS:3 ORDER BY Priority LIMIT 15"

# 4. Escalated
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Priority, Status FROM Case WHERE IsEscalated = true AND IsClosed = false"
```

### Investigate Issue Pattern

```
Progress:
- [ ] Find cases with similar symptoms
- [ ] Identify affected accounts
- [ ] Check timeline
```

```bash
# 1. Similar cases
sf data query -o nvcrm-dev --query "SELECT Id, CaseNumber, Subject, Account.Name, CreatedDate FROM Case WHERE Subject LIKE '%keyword%' ORDER BY CreatedDate DESC LIMIT 20"

# 2. Affected accounts
sf data query -o nvcrm-dev --query "SELECT Account.Name, COUNT(Id) cnt FROM Case WHERE Subject LIKE '%keyword%' GROUP BY Account.Name ORDER BY cnt DESC"

# 3. Timeline
sf data query -o nvcrm-dev --query "SELECT CreatedDate, Subject, Account.Name FROM Case WHERE Subject LIKE '%keyword%' ORDER BY CreatedDate DESC LIMIT 20"
```

## Notes

**Priority Levels:** P1 (Immediate), P2 (4hrs), P3 (1 day), P4 (best effort)

**Confirm before:** Closing cases, changing priority to P1, bulk status updates
