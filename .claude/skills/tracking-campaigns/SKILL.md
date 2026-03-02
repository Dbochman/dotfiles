---
name: tracking-campaigns
description: Tracks Salesforce marketing campaign performance and attribution. Analyzes lead generation, conversion rates, ROI metrics. Use when reviewing campaign effectiveness, checking lead sources, preparing marketing reports, or analyzing GTC/webinar performance.
---

# Campaign Tracking

Track marketing campaign performance via `sf` CLI.

## Setup

```bash
# Requires: sf (Salesforce CLI)
sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com
```

## Quick Reference

All commands require `--target-org nvcrm-dev` (or `-o nvcrm-dev`).

### Campaign Overview

```bash
# Active campaigns
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, Status, StartDate, EndDate, NumberOfLeads, NumberOfOpportunities FROM Campaign WHERE IsActive = true ORDER BY StartDate DESC LIMIT 30"

# Recent campaigns
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, Status, StartDate, NumberOfLeads FROM Campaign ORDER BY CreatedDate DESC LIMIT 30"

# By status
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, StartDate, NumberOfLeads FROM Campaign WHERE Status = 'In Progress' LIMIT 30"
```

### Campaign Performance

```bash
# Top lead generators
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, NumberOfLeads, NumberOfConvertedLeads FROM Campaign WHERE NumberOfLeads > 0 ORDER BY NumberOfLeads DESC LIMIT 20"

# Best conversion rates
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, NumberOfLeads, NumberOfConvertedLeads, NumberOfOpportunities FROM Campaign WHERE NumberOfLeads > 10 ORDER BY NumberOfConvertedLeads DESC LIMIT 20"

# Revenue attribution
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, NumberOfOpportunities, NumberOfWonOpportunities, AmountAllOpportunities, AmountWonOpportunities FROM Campaign WHERE AmountAllOpportunities > 0 ORDER BY AmountWonOpportunities DESC LIMIT 20"
```

### Campaign Types

```bash
# Count by type
sf data query -o nvcrm-dev --query "SELECT Type, COUNT(Id) cnt FROM Campaign WHERE CreatedDate = THIS_YEAR GROUP BY Type ORDER BY cnt DESC"

# Performance by type
sf data query -o nvcrm-dev --query "SELECT Type, SUM(NumberOfLeads) leads, SUM(NumberOfOpportunities) opps FROM Campaign WHERE CreatedDate = THIS_YEAR GROUP BY Type"
```

### Search Campaigns

```bash
# By name
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, Status, NumberOfLeads FROM Campaign WHERE Name LIKE '%GTC%' ORDER BY StartDate DESC LIMIT 20"

# By region (NALA, APAC, etc.)
sf data query -o nvcrm-dev --query "SELECT Id, Name, Status, StartDate, NumberOfLeads FROM Campaign WHERE Name LIKE '%NALA%' ORDER BY StartDate DESC LIMIT 20"

# Webinars
sf data query -o nvcrm-dev --query "SELECT Id, Name, Status, StartDate, NumberOfLeads, NumberOfResponses FROM Campaign WHERE Type = 'Webinar' ORDER BY StartDate DESC LIMIT 20"
```

### Campaign Members

```bash
# Member count by campaign
sf data query -o nvcrm-dev --query "SELECT CampaignId, COUNT(Id) cnt FROM CampaignMember GROUP BY CampaignId ORDER BY cnt DESC LIMIT 20"

# Leads from specific campaign
sf data query -o nvcrm-dev --query "SELECT LeadId, Status FROM CampaignMember WHERE CampaignId = '701xxxx' AND LeadId != null LIMIT 50"

# Response rate
sf data query -o nvcrm-dev --query "SELECT HasResponded, COUNT(Id) cnt FROM CampaignMember WHERE CampaignId = '701xxxx' GROUP BY HasResponded"
```

### Time-Based Analysis

```bash
# This quarter's campaigns
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, StartDate, NumberOfLeads, NumberOfOpportunities FROM Campaign WHERE StartDate = THIS_QUARTER ORDER BY StartDate"

# This year performance
sf data query -o nvcrm-dev --query "SELECT Status, COUNT(Id) cnt, SUM(NumberOfLeads) leads FROM Campaign WHERE StartDate = THIS_YEAR GROUP BY Status"
```

## Workflows

### Campaign ROI Report

```
Progress:
- [ ] Get campaign metrics
- [ ] Calculate conversion rates
- [ ] Review revenue attribution
- [ ] Compare to cost
```

```bash
# 1. Campaign metrics
sf data query -o nvcrm-dev --query "SELECT Id, Name, Type, NumberOfLeads, NumberOfConvertedLeads, NumberOfOpportunities, NumberOfWonOpportunities, AmountWonOpportunities, ActualCost FROM Campaign WHERE Id = '701xxxx'"

# 2. Conversion rate = NumberOfConvertedLeads / NumberOfLeads
# 3. Opportunity rate = NumberOfOpportunities / NumberOfLeads
# 4. ROI = (AmountWonOpportunities - ActualCost) / ActualCost
```

### Weekly Marketing Review

```
Progress:
- [ ] Active campaigns status
- [ ] Lead generation this week
- [ ] Top performers
- [ ] Upcoming campaigns
```

```bash
# 1. Active campaigns
sf data query -o nvcrm-dev --query "SELECT Name, Type, Status, NumberOfLeads, NumberOfResponses FROM Campaign WHERE IsActive = true ORDER BY NumberOfLeads DESC LIMIT 15"

# 2. Leads generated this week (via LeadSource)
sf data query -o nvcrm-dev --query "SELECT LeadSource, COUNT(Id) cnt FROM Lead WHERE CreatedDate = THIS_WEEK GROUP BY LeadSource ORDER BY cnt DESC"

# 3. Top performers this quarter
sf data query -o nvcrm-dev --query "SELECT Name, Type, NumberOfLeads, NumberOfOpportunities FROM Campaign WHERE StartDate = THIS_QUARTER ORDER BY NumberOfLeads DESC LIMIT 10"

# 4. Upcoming (not started)
sf data query -o nvcrm-dev --query "SELECT Name, Type, StartDate, Status FROM Campaign WHERE StartDate > TODAY AND Status = 'Planned' ORDER BY StartDate LIMIT 10"
```

### Compare Campaign Types

```
Progress:
- [ ] Performance by type
- [ ] Cost efficiency
- [ ] Best channels
```

```bash
# Lead gen by type
sf data query -o nvcrm-dev --query "SELECT Type, COUNT(Id) campaigns, SUM(NumberOfLeads) leads, SUM(NumberOfOpportunities) opps FROM Campaign WHERE StartDate = THIS_YEAR GROUP BY Type ORDER BY leads DESC"

# Cost per lead by type
sf data query -o nvcrm-dev --query "SELECT Type, SUM(ActualCost) cost, SUM(NumberOfLeads) leads FROM Campaign WHERE ActualCost > 0 GROUP BY Type"
```

## Notes

**Key Metrics:** Lead Conversion, Opportunity Rate, Win Rate, Cost per Lead, ROI

**Campaign Types:** GTC, WBN (Webinar), EVTP (Event), DL; Regions: NALA, APAC, LABR, Global

**Confirm before:** Deleting campaigns with members, bulk status changes
