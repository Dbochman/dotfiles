---
name: researching-inception
description: Researches NVIDIA Inception program startups, VCs, and accelerators. Searches companies by industry, location, funding, finds investor connections, analyzes membership status. Use when exploring Inception portfolio, researching startups, finding VC relationships, or preparing for startup engagements.
---

# Inception Research

Research NVIDIA Inception startups, VCs, and accelerators via `sf` CLI.

## Setup

```bash
# Requires: sf (Salesforce CLI)
sf org login web --alias nvcrm-dev --instance-url https://test.salesforce.com
```

## Inception Objects

| Object | Records | Use |
|--------|---------|-----|
| `Inception_Connect_Profile__c` | ~49K | Main startup profiles |
| `Inception_Request__c` | ~44K | Application data & use cases |
| `Inception_VC_Firm__c` | ~1.9K | VC firm connections |
| `Inception_VC_Alliance_Profile__c` | ~1.8K | VC Alliance partners |
| `Inception_Accelerator__c` | ~3.9K | Accelerator affiliations |
| `Inception_Capital_Connect_Requests__c` | ~5.9K | Funding match requests |

## Quick Reference

All commands require `--target-org nvcrm-dev` (or `-o nvcrm-dev`).

### Search Startups

```bash
# By company name
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Membership_Status__c, HQ_Location__c, Total_Funding_Raised_USD__c, Industry_Tagify__c FROM Inception_Connect_Profile__c WHERE Company_Name__c LIKE '%OpenAI%'"

# By industry
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, HQ_Location__c, Total_Funding_Raised_USD__c FROM Inception_Connect_Profile__c WHERE Industry_Tagify__c LIKE '%Healthcare%' AND Membership_Status__c = 'Member - Community' LIMIT 30"

# By location
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Industry_Tagify__c, Total_Funding_Raised_USD__c FROM Inception_Connect_Profile__c WHERE HQ_Location__c = 'Germany' AND Membership_Status__c = 'Member - Community' LIMIT 30"

# By funding range
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, HQ_Location__c, Total_Funding_Raised_USD__c, Industry_Tagify__c FROM Inception_Connect_Profile__c WHERE Total_Funding_Raised_USD__c > 10000000 AND Membership_Status__c = 'Member - Community' ORDER BY Total_Funding_Raised_USD__c DESC LIMIT 30"
```

### Get Startup Details

```bash
# Full profile with descriptions
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Company_Description__c, PB_Company_Description__c, Membership_Status__c, HQ_Location__c, Total_Funding_Raised_USD__c, Industry_Tagify__c, Workloads_Tagify__c, Website__c FROM Inception_Connect_Profile__c WHERE Id = 'aGBxxxx'" --json

# With investor details
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, PB_Active_Investors__c, PB_Financing_Status_Note__c, Engagement_Level_Justification__c FROM Inception_Connect_Profile__c WHERE Id = 'aGBxxxx'" --json
```

### Search by Use Case (from Applications)

```bash
# By AI technology
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company_Name__c, Current_Use_Case__c, How_GPUs_Used__c FROM Inception_Request__c WHERE Current_Use_Case__c LIKE '%Computer Vision%' LIMIT 20"

# By GPU usage
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company_Name__c, Current_Use_Case__c, How_Are_GPUs_being_Used__c FROM Inception_Request__c WHERE How_GPUs_Used__c LIKE '%Training%' LIMIT 20"

# By value proposition
sf data query -o nvcrm-dev --query "SELECT Id, Name, Company_Name__c, Company_s_value_proposition__c, Current_Use_Case__c FROM Inception_Request__c WHERE Company_s_value_proposition__c LIKE '%autonomous%'" --json
```

### Membership Status

```bash
# Active members
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, HQ_Location__c, Total_Funding_Raised_USD__c FROM Inception_Connect_Profile__c WHERE Membership_Status__c = 'Member - Community' ORDER BY Total_Funding_Raised_USD__c DESC NULLS LAST LIMIT 30"

# Pending review
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, HQ_Location__c, Program_Affiliation__c FROM Inception_Connect_Profile__c WHERE Membership_Status__c = 'Pending Review' LIMIT 30"

# Past members
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Past_Member_Reason__c, Past_Member_Conversion_Date__c FROM Inception_Connect_Profile__c WHERE Membership_Status__c = 'Past Member' LIMIT 30"

# Rejected (for patterns)
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Rejection_Reason__c, Rejection_Date__c FROM Inception_Connect_Profile__c WHERE Membership_Status__c = 'Rejected' LIMIT 20"
```

### VC Connections

```bash
# VC Alliance partners
sf data query -o nvcrm-dev --query "SELECT Id, Name, Account__c FROM Inception_VC_Alliance_Profile__c LIMIT 30"

# VC firms linked to startups
sf data query -o nvcrm-dev --query "SELECT Id, Name, VC_Firm_Name__c, Inception_Request__c FROM Inception_VC_Firm__c WHERE VC_Firm_Name__c != 'Other' LIMIT 30"

# Find startups by investor
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, PB_Active_Investors__c, Total_Funding_Raised_USD__c FROM Inception_Connect_Profile__c WHERE PB_Active_Investors__c LIKE '%Sequoia%'" --json
```

### Accelerator Participation

```bash
# Accelerator records
sf data query -o nvcrm-dev --query "SELECT Id, Name, Accelerator__c, Inception_Request__c FROM Inception_Accelerator__c LIMIT 30"

# Find by accelerator name
sf data query -o nvcrm-dev --query "SELECT Id, Name, Accelerator__c FROM Inception_Accelerator__c WHERE Accelerator__c LIKE '%Y Combinator%'"
```

### Capital Connect Requests

```bash
# Recent funding requests
sf data query -o nvcrm-dev --query "SELECT Id, Name, CreatedDate FROM Inception_Capital_Connect_Requests__c ORDER BY CreatedDate DESC LIMIT 20"
```

## Workflows

### Research a Startup

```bash
# 1. Profile overview
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Membership_Status__c, HQ_Location__c, Total_Funding_Raised_USD__c, Industry_Tagify__c, Workloads_Tagify__c, Website__c FROM Inception_Connect_Profile__c WHERE Company_Name__c LIKE '%CompanyName%'"

# 2. Descriptions (use ID from step 1)
sf data query -o nvcrm-dev --query "SELECT Company_Description__c, PB_Company_Description__c, Engagement_Level_Justification__c FROM Inception_Connect_Profile__c WHERE Id = 'aGBxxxx'" --json

# 3. Investor details
sf data query -o nvcrm-dev --query "SELECT PB_Active_Investors__c, PB_Financing_Status_Note__c FROM Inception_Connect_Profile__c WHERE Id = 'aGBxxxx'" --json

# 4. Application details (find by company name)
sf data query -o nvcrm-dev --query "SELECT Id, Name, Current_Use_Case__c, Company_s_value_proposition__c, Brief_Company_Description_140_character__c, How_GPUs_Used__c FROM Inception_Request__c WHERE Company_Name__c LIKE '%CompanyName%'" --json

# 5. VC connections
sf data query -o nvcrm-dev --query "SELECT Id, VC_Firm_Name__c FROM Inception_VC_Firm__c WHERE Inception_Request__c IN (SELECT Id FROM Inception_Request__c WHERE Company_Name__c LIKE '%CompanyName%')"
```

### Find Startups for Outreach

```bash
# 1. Filter (example: Healthcare, >$1M funding, US)
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Total_Funding_Raised_USD__c, Industry_Tagify__c FROM Inception_Connect_Profile__c WHERE Industry_Tagify__c LIKE '%Healthcare%' AND Total_Funding_Raised_USD__c > 1000000 AND HQ_Location__c = 'United States' AND Membership_Status__c = 'Member - Community' ORDER BY Total_Funding_Raised_USD__c DESC LIMIT 20"

# 2. Get descriptions for top prospects
sf data query -o nvcrm-dev --query "SELECT Company_Name__c, Company_Description__c, PB_Company_Description__c, Website__c FROM Inception_Connect_Profile__c WHERE Id IN ('aGBxxxx','aGBxxxx','aGBxxxx')" --json

# 3. Find contacts via Account link
sf data query -o nvcrm-dev --query "SELECT Account__c FROM Inception_Connect_Profile__c WHERE Id = 'aGBxxxx'"
# Then query Contact with AccountId
```

### Analyze Portfolio Segment

```bash
# Example: AI Infrastructure startups
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, HQ_Location__c, Total_Funding_Raised_USD__c FROM Inception_Connect_Profile__c WHERE Industry_Tagify__c LIKE '%Cloud Services%' AND Workloads_Tagify__c LIKE '%Infrastructure%' AND Membership_Status__c = 'Member - Community' ORDER BY Total_Funding_Raised_USD__c DESC LIMIT 30"
```

### Find Similar Companies

```bash
# 1. Get target profile
sf data query -o nvcrm-dev --query "SELECT Industry_Tagify__c, Workloads_Tagify__c, HQ_Location__c FROM Inception_Connect_Profile__c WHERE Company_Name__c = 'TargetCompany'"

# 2. Find similar (same industry + workload)
sf data query -o nvcrm-dev --query "SELECT Id, Company_Name__c, Total_Funding_Raised_USD__c FROM Inception_Connect_Profile__c WHERE Industry_Tagify__c LIKE '%Healthcare%' AND Workloads_Tagify__c LIKE '%Computer Vision%' AND Membership_Status__c = 'Member - Community' LIMIT 20"
```

## Notes

**Key Objects:** `Inception_Connect_Profile__c` (profiles), `Inception_Request__c` (applications), `Inception_VC_Firm__c`, `Inception_Accelerator__c`

**Membership statuses:** Member - Community, Pending Review, Rejected, Past Member
