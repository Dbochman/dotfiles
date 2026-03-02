---
name: managing-pipeline
description: Analyzes Salesforce opportunity pipeline for AEs and sales managers. Tracks deals by stage, identifies at-risk opportunities, finds stalled deals. Use when reviewing pipeline, preparing forecasts, finding deals needing attention, or analyzing win/loss.
---

# Pipeline Management

Analyze and manage Salesforce opportunity pipeline via `sf` CLI.

## Setup

```bash
# Requires: sf (Salesforce CLI)
sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com
```

## Quick Reference

All commands require `--target-org nvcrm-dev` (or `-o nvcrm-dev`).

### Pipeline Overview

```bash
# Count by stage
sf data query -o nvcrm-dev --query "SELECT StageName, COUNT(Id) cnt FROM Opportunity WHERE IsClosed = false GROUP BY StageName ORDER BY cnt DESC"

# Pipeline value by stage
sf data query -o nvcrm-dev --query "SELECT StageName, COUNT(Id) cnt, SUM(Amount) total FROM Opportunity WHERE IsClosed = false GROUP BY StageName"

# My open opportunities
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate, Account.Name FROM Opportunity WHERE IsClosed = false ORDER BY CloseDate LIMIT 30"
```

### Deals by Close Date

```bash
# Closing this month
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate, Account.Name FROM Opportunity WHERE CloseDate = THIS_MONTH AND IsClosed = false ORDER BY Amount DESC"

# Closing this quarter
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity WHERE CloseDate = THIS_QUARTER AND IsClosed = false ORDER BY CloseDate"

# Past due (close date passed, still open)
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity WHERE CloseDate < TODAY AND IsClosed = false ORDER BY CloseDate"
```

### At-Risk Deals

```bash
# No activity in 30+ days
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, LastModifiedDate, Account.Name FROM Opportunity WHERE LastModifiedDate < LAST_N_DAYS:30 AND IsClosed = false ORDER BY Amount DESC LIMIT 30"

# Pushed deals (high push count)
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, PushCount, CloseDate FROM Opportunity WHERE PushCount > 2 AND IsClosed = false ORDER BY PushCount DESC"

# Stalled in early stage
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, LastStageChangeDate FROM Opportunity WHERE StageName = 'Prospecting' AND LastStageChangeDate < LAST_N_DAYS:30 AND IsClosed = false"
```

### Win/Loss Analysis

```bash
# Recent wins
sf data query -o nvcrm-dev --query "SELECT Id, Name, Amount, CloseDate, Account.Name FROM Opportunity WHERE IsWon = true AND CloseDate = THIS_QUARTER ORDER BY CloseDate DESC LIMIT 20"

# Recent losses
sf data query -o nvcrm-dev --query "SELECT Id, Name, Amount, CloseDate, StageName, Account.Name FROM Opportunity WHERE IsClosed = true AND IsWon = false AND CloseDate = THIS_QUARTER ORDER BY CloseDate DESC LIMIT 20"

# Win rate this quarter
sf data query -o nvcrm-dev --query "SELECT IsWon, COUNT(Id) cnt FROM Opportunity WHERE IsClosed = true AND CloseDate = THIS_QUARTER GROUP BY IsWon"
```

### High Value Deals

```bash
# Largest open deals
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate, Account.Name FROM Opportunity WHERE IsClosed = false AND Amount > 100000 ORDER BY Amount DESC LIMIT 20"

# Large deals closing soon
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity WHERE Amount > 50000 AND CloseDate = THIS_MONTH AND IsClosed = false ORDER BY Amount DESC"
```

### Update Opportunities

```bash
# Update stage
sf data update record -o nvcrm-dev --sobject Opportunity --record-id 006xxxx --values "StageName='Negotiation'"

# Update close date
sf data update record -o nvcrm-dev --sobject Opportunity --record-id 006xxxx --values "CloseDate=2026-03-31"

# Update amount and stage
sf data update record -o nvcrm-dev --sobject Opportunity --record-id 006xxxx --values "Amount=150000 StageName='Proposal'"
```

## Workflows

### Weekly Pipeline Review

```
Progress:
- [ ] Get pipeline by stage
- [ ] Identify stalled deals
- [ ] Find past-due opportunities
- [ ] Review large deals closing soon
```

```bash
# 1. Pipeline summary
sf data query -o nvcrm-dev --query "SELECT StageName, COUNT(Id) cnt, SUM(Amount) total FROM Opportunity WHERE IsClosed = false GROUP BY StageName"

# 2. Stalled deals (no activity 14+ days)
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, LastModifiedDate FROM Opportunity WHERE LastModifiedDate < LAST_N_DAYS:14 AND IsClosed = false ORDER BY Amount DESC LIMIT 20"

# 3. Past due
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity WHERE CloseDate < TODAY AND IsClosed = false"

# 4. Big deals closing this month
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity WHERE CloseDate = THIS_MONTH AND IsClosed = false AND Amount > 50000 ORDER BY Amount DESC"
```

### Forecast Preparation

```
Progress:
- [ ] Commit deals (high probability)
- [ ] Best case deals
- [ ] Pipeline deals
- [ ] Calculate totals
```

```bash
# Commit (Closed Won + high probability)
sf data query -o nvcrm-dev --query "SELECT Id, Name, Amount, Probability, CloseDate FROM Opportunity WHERE CloseDate = THIS_QUARTER AND (IsWon = true OR Probability >= 90)"

# Best case
sf data query -o nvcrm-dev --query "SELECT Id, Name, Amount, Probability, CloseDate FROM Opportunity WHERE CloseDate = THIS_QUARTER AND Probability >= 70 AND IsClosed = false"

# Pipeline
sf data query -o nvcrm-dev --query "SELECT Id, Name, Amount, Probability, CloseDate FROM Opportunity WHERE CloseDate = THIS_QUARTER AND Probability >= 20 AND IsClosed = false"
```

## Notes

**Stage Flow:** Prospecting → Qualification → Proposal → Negotiation → Closed Won/Lost

**Confirm before:** Closing deals as Won/Lost, bulk stage updates, deleting opportunities
