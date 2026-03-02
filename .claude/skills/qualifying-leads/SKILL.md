---
name: qualifying-leads
description: Qualifies and prioritizes Salesforce leads for SDRs. Queries leads by status, checks for duplicates, identifies stale leads. Use when reviewing lead queues, triaging new leads, checking for duplicate accounts, or prioritizing outreach.
---

# Lead Qualification

Qualify and prioritize Salesforce leads via `sf` CLI.

## Setup

```bash
# Requires: sf (Salesforce CLI)
sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com
```

## Quick Reference

All commands require `--target-org nvcrm-dev` (or `-o nvcrm-dev`).

### Query Leads by Status

```bash
# New/unworked leads
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Email, Status, LeadSource, CreatedDate FROM Lead WHERE Status = 'Prospect' ORDER BY CreatedDate DESC LIMIT 50"

# Sales qualified leads ready for conversion
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Email, Status, CreatedDate FROM Lead WHERE Status = 'Sales Qualified' AND IsConverted = false ORDER BY CreatedDate DESC LIMIT 50"

# Leads by specific status
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Status FROM Lead WHERE Status = 'Working' LIMIT 50"
```

### Check for Duplicates

```bash
# Find accounts matching company name
sf data query -o nvcrm-dev --query "SELECT Id, Name, Industry, Type FROM Account WHERE Name LIKE '%CompanyName%'"

# Find contacts by email domain
sf data query -o nvcrm-dev --query "SELECT Id, Name, Email, Account.Name FROM Contact WHERE Email LIKE '%@domain.com'"

# Find existing leads from same company
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Status, Email FROM Lead WHERE Company LIKE '%CompanyName%'"
```

### Find Stale Leads

```bash
# Leads untouched for 30+ days
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Status, LastModifiedDate FROM Lead WHERE LastModifiedDate < LAST_N_DAYS:30 AND IsConverted = false ORDER BY LastModifiedDate LIMIT 50"

# Leads stuck in 'Working' status
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Status, LastModifiedDate FROM Lead WHERE Status = 'Working' AND LastModifiedDate < LAST_N_DAYS:14 ORDER BY LastModifiedDate LIMIT 50"
```

### Lead Source Analysis

```bash
# Count leads by source
sf data query -o nvcrm-dev --query "SELECT LeadSource, COUNT(Id) cnt FROM Lead WHERE CreatedDate = THIS_QUARTER GROUP BY LeadSource ORDER BY cnt DESC"

# Recent leads by source
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, LeadSource, CreatedDate FROM Lead WHERE LeadSource = 'Web' AND CreatedDate = THIS_WEEK ORDER BY CreatedDate DESC"
```

### Update Lead Status

```bash
# Update status
sf data update record -o nvcrm-dev --sobject Lead --record-id 00Qxxxx --values "Status='Working'"

# Add to working queue
sf data update record -o nvcrm-dev --sobject Lead --record-id 00Qxxxx --values "Status='Working' Rating='Hot'"
```

## Workflows

### Triage New Leads

```
Progress:
- [ ] Query new leads
- [ ] Check each for duplicates
- [ ] Update status or flag for review
```

```bash
# 1. Get new leads
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Email, Industry, LeadSource FROM Lead WHERE Status = 'Prospect' AND CreatedDate = THIS_WEEK ORDER BY CreatedDate DESC LIMIT 20"

# 2. For each lead, check for existing account
sf data query -o nvcrm-dev --query "SELECT Id, Name FROM Account WHERE Name LIKE '%CompanyName%'"

# 3. If no duplicate, update to Working
sf data update record -o nvcrm-dev --sobject Lead --record-id 00Qxxxx --values "Status='Working'"
```

### Qualify for Conversion

```
Progress:
- [ ] Find sales qualified leads
- [ ] Verify no existing account
- [ ] Check contact doesn't exist
- [ ] Ready for conversion
```

```bash
# 1. Sales qualified leads
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company, Email, Status FROM Lead WHERE Status = 'Sales Qualified' AND IsConverted = false LIMIT 20"

# 2. Check account exists
sf data query -o nvcrm-dev --query "SELECT Id, Name FROM Account WHERE Name LIKE '%CompanyName%'"

# 3. Check contact exists
sf data query -o nvcrm-dev --query "SELECT Id, Name, Email FROM Contact WHERE Email = 'user@company.com'"
```

## Notes

**Status Flow:** Prospect → Working → Sales Qualified → Convert (or Disqualified/Recycling/Declined)

**Confirm before:** Converting leads, bulk status updates, deleting/merging duplicates
