---
name: querying-salesforce
description: Query and explore Salesforce CRM data including accounts, leads, opportunities, contacts, cases, campaigns, and products. Use when users mention salesforce, CRM, accounts, leads, opportunities, pipeline, contacts, cases, support tickets, campaigns, or need to search/query customer data.
---

# Salesforce CLI Skill

Query and explore Salesforce CRM data via the `sf` CLI (Salesforce CLI).

## Setup

```bash
# Requires: sf (Salesforce CLI)
sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com
```

## Quick Reference

Always include `--target-org nvcrm-dev` (or `-o nvcrm-dev`).

### Accounts

```bash
sf data query --query "SELECT Id, Name, Industry, Type FROM Account ORDER BY CreatedDate DESC LIMIT 20" -o nvcrm-dev
sf data query --query "SELECT Id, Name, Industry FROM Account WHERE Name LIKE '%nvidia%'" -o nvcrm-dev
sf data query --query "SELECT COUNT() FROM Account" -o nvcrm-dev
```

### Leads

```bash
sf data query --query "SELECT Id, Name, Status, Company, Email FROM Lead ORDER BY CreatedDate DESC LIMIT 20" -o nvcrm-dev
sf data query --query "SELECT Id, Name, Company FROM Lead WHERE Status = 'Sales Qualified'" -o nvcrm-dev
sf data query --query "SELECT Status, COUNT(Id) cnt FROM Lead GROUP BY Status" -o nvcrm-dev
```

### Opportunities

```bash
sf data query --query "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity ORDER BY CreatedDate DESC LIMIT 20" -o nvcrm-dev
sf data query --query "SELECT Id, Name, Amount, CloseDate FROM Opportunity WHERE CloseDate = THIS_MONTH" -o nvcrm-dev
sf data query --query "SELECT Id, Name, Amount, StageName FROM Opportunity WHERE Amount > 100000 ORDER BY Amount DESC" -o nvcrm-dev
```

### Contacts

```bash
sf data query --query "SELECT Id, Name, Email, Account.Name FROM Contact ORDER BY CreatedDate DESC LIMIT 20" -o nvcrm-dev
sf data query --query "SELECT Id, Name, Email, Account.Name FROM Contact WHERE Name LIKE '%smith%'" -o nvcrm-dev
```

### Cases (Support)

```bash
sf data query --query "SELECT Id, Subject, Status, Priority FROM Case ORDER BY CreatedDate DESC LIMIT 20" -o nvcrm-dev
sf data query --query "SELECT Id, Subject, Priority FROM Case WHERE Status != 'Closed' ORDER BY Priority" -o nvcrm-dev
```

### Campaigns & Products

```bash
sf data query --query "SELECT Id, Name, Status, StartDate FROM Campaign ORDER BY CreatedDate DESC LIMIT 20" -o nvcrm-dev
sf data query --query "SELECT Id, Name, IsActive FROM Product2 WHERE Name LIKE '%vGPU%'" -o nvcrm-dev
```

### Describe Objects

```bash
sf sobject list -o nvcrm-dev                          # List all objects
sf sobject describe --sobject Account -o nvcrm-dev    # Describe fields
sf sobject list --sobject-type custom -o nvcrm-dev    # Custom objects
```

## Common Workflows

### Account 360 View

```bash
# Get account, then related contacts, opportunities, and cases
sf data query --query "SELECT Id, Name, Industry, Type, Website FROM Account WHERE Id = '001xxx'" -o nvcrm-dev
sf data query --query "SELECT Id, Name, Email, Title FROM Contact WHERE AccountId = '001xxx'" -o nvcrm-dev
sf data query --query "SELECT Id, Name, StageName, Amount FROM Opportunity WHERE AccountId = '001xxx'" -o nvcrm-dev
sf data query --query "SELECT Id, Subject, Status, Priority FROM Case WHERE AccountId = '001xxx'" -o nvcrm-dev
```

### Lead Qualification Review

```bash
sf data query --query "SELECT Id, Name, Company, Email, LeadSource FROM Lead WHERE Status = 'Sales Qualified' ORDER BY CreatedDate DESC LIMIT 50" -o nvcrm-dev
sf data query --query "SELECT Id, Name FROM Account WHERE Name LIKE '%CompanyName%'" -o nvcrm-dev
```

### Pipeline Review

```bash
sf data query --query "SELECT StageName, COUNT(Id) cnt FROM Opportunity GROUP BY StageName" -o nvcrm-dev
sf data query --query "SELECT Id, Name, StageName, LastModifiedDate FROM Opportunity WHERE LastModifiedDate < LAST_N_DAYS:30 AND IsClosed = false" -o nvcrm-dev
```

## SOQL Query Patterns

### Date Filters
```sql
CreatedDate = TODAY | THIS_WEEK | THIS_MONTH | LAST_N_DAYS:30
CreatedDate > 2024-01-01
```

### Text Search
```sql
Name LIKE '%keyword%'
Email LIKE '%nvidia.com'
```

### Relationships
```sql
SELECT Contact.Name, Contact.Email FROM Account
SELECT Account.Name FROM Contact WHERE AccountId != null
```

### Aggregations
```sql
SELECT Status, COUNT(Id) FROM Lead GROUP BY Status
SELECT SUM(Amount) FROM Opportunity WHERE StageName = 'Closed Won'
```

## Notes

- **Read-only by default**: Confirm with user before create/update operations
- **Sandbox**: `nvcrm-dev` is dev sandbox, not production
- **Auth errors**: Re-run `sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com`
