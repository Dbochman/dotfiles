---
name: exploring-accounts
description: Explores Salesforce account data with 360-degree views including contacts, opportunities, and cases. Use when researching accounts, preparing for customer meetings, onboarding to a territory, or analyzing account health.
---

# Account Explorer

Explore Salesforce accounts and related records via `sf` CLI.

## Setup

```bash
# Requires: sf (Salesforce CLI)
sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com
```

## Quick Reference

All commands require `--target-org nvcrm-dev` (or `-o nvcrm-dev`).

### Search Accounts

```bash
# By name
sf data query -o nvcrm-dev --query "SELECT Id, Name, Industry, Type, Website, Phone FROM Account WHERE Name LIKE '%nvidia%'"

# By industry
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, Website FROM Account WHERE Industry = 'Healthcare' LIMIT 30"

# By type
sf data query -o nvcrm-dev --query "SELECT Id, Name, Industry FROM Account WHERE Type = 'ISV' LIMIT 30"

# By location
sf data query -o nvcrm-dev --query "SELECT Id, Name, Industry, BillingCity, BillingCountry FROM Account WHERE BillingCountry = 'United States' AND BillingState = 'California' LIMIT 30"
```

### Account Details

```bash
# Full account record
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, Industry, Website, Phone, BillingCity, BillingState, BillingCountry, Description FROM Account WHERE Id = '001xxxx'"

# With parent account
sf data query -o nvcrm-dev --query "SELECT Id, Name, Parent.Name, Industry, Type FROM Account WHERE Id = '001xxxx'"
```

### Related Contacts

```bash
# All contacts for account
sf data query -o nvcrm-dev --query "SELECT Id, Name, Title, Email, Phone FROM Contact WHERE AccountId = '001xxxx'"

# Key contacts (by title)
sf data query -o nvcrm-dev --query "SELECT Id, Name, Title, Email FROM Contact WHERE AccountId = '001xxxx' AND (Title LIKE '%VP%' OR Title LIKE '%Director%' OR Title LIKE '%Manager%')"
```

### Related Opportunities

```bash
# All opportunities
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate, IsWon FROM Opportunity WHERE AccountId = '001xxxx' ORDER BY CloseDate DESC"

# Open opportunities
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity WHERE AccountId = '001xxxx' AND IsClosed = false"

# Win/loss history
sf data query -o nvcrm-dev --query "SELECT Id, Name, Amount, CloseDate, IsWon FROM Opportunity WHERE AccountId = '001xxxx' AND IsClosed = true ORDER BY CloseDate DESC LIMIT 10"
```

### Related Cases

```bash
# Open cases
sf data query -o nvcrm-dev --query "SELECT Id, Subject, Status, Priority, CreatedDate FROM Case WHERE AccountId = '001xxxx' AND IsClosed = false ORDER BY Priority"

# Recent cases
sf data query -o nvcrm-dev --query "SELECT Id, Subject, Status, Priority, CreatedDate FROM Case WHERE AccountId = '001xxxx' ORDER BY CreatedDate DESC LIMIT 10"
```

### Account Types Overview

```bash
# Count by type
sf data query -o nvcrm-dev --query "SELECT Type, COUNT(Id) cnt FROM Account WHERE Type != null GROUP BY Type ORDER BY cnt DESC"

# Count by industry
sf data query -o nvcrm-dev --query "SELECT Industry, COUNT(Id) cnt FROM Account WHERE Industry != null GROUP BY Industry ORDER BY cnt DESC"
```

## Workflows

### Account 360 View

```
Progress:
- [ ] Get account details
- [ ] List contacts
- [ ] Review opportunities
- [ ] Check open cases
```

```bash
# 1. Account details
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, Industry, Website, Phone, BillingCity, BillingCountry FROM Account WHERE Id = '001xxxx'"

# 2. Contacts
sf data query -o nvcrm-dev --query "SELECT Id, Name, Title, Email, Phone FROM Contact WHERE AccountId = '001xxxx'"

# 3. Opportunities
sf data query -o nvcrm-dev --query "SELECT Id, Name, StageName, Amount, CloseDate, IsWon FROM Opportunity WHERE AccountId = '001xxxx' ORDER BY CloseDate DESC LIMIT 10"

# 4. Cases
sf data query -o nvcrm-dev --query "SELECT Id, Subject, Status, Priority FROM Case WHERE AccountId = '001xxxx' AND IsClosed = false"
```

### Meeting Prep

```
Progress:
- [ ] Account overview
- [ ] Key stakeholders
- [ ] Current deals
- [ ] Recent issues
```

```bash
# 1. Account summary
sf data query -o nvcrm-dev --query "SELECT Name, Industry, Type, Website, Description FROM Account WHERE Id = '001xxxx'"

# 2. Decision makers
sf data query -o nvcrm-dev --query "SELECT Name, Title, Email, Phone FROM Contact WHERE AccountId = '001xxxx' AND (Title LIKE '%VP%' OR Title LIKE '%CTO%' OR Title LIKE '%Director%')"

# 3. Active opportunities
sf data query -o nvcrm-dev --query "SELECT Name, StageName, Amount, CloseDate, NextStep FROM Opportunity WHERE AccountId = '001xxxx' AND IsClosed = false"

# 4. Recent cases
sf data query -o nvcrm-dev --query "SELECT Subject, Status, Priority, Description FROM Case WHERE AccountId = '001xxxx' ORDER BY CreatedDate DESC LIMIT 5"
```

### Territory Analysis

```
Progress:
- [ ] List accounts by region
- [ ] Summarize by type
- [ ] Identify whitespace
```

```bash
# Accounts in territory
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, Industry FROM Account WHERE BillingState = 'California' LIMIT 50"

# By type in territory
sf data query -o nvcrm-dev --query "SELECT Type, COUNT(Id) cnt FROM Account WHERE BillingState = 'California' GROUP BY Type"

# No recent opportunities
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type FROM Account WHERE BillingState = 'California' AND Id NOT IN (SELECT AccountId FROM Opportunity WHERE CreatedDate = THIS_YEAR) LIMIT 30"
```

## Notes

**Account Types:** ISV (40K+), Reseller, OEM, End User, Academic Institution, Venture Capital Firm

**Confirm before:** Merging duplicates, changing ownership, deleting accounts
