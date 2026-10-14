# LinkFinder: licensing, product requirements and cost estimate
*SharePoint / OneDrive link → Google Drive link*

## In short
1. **LinkFinder itself costs nothing.** There's no license and no Marketplace purchase; you receive the package.
2. You need a **Google Workspace edition that includes BigQuery Export** (for example Enterprise Standard / Plus).
3. You need a **Google Cloud project with a billing account**. You pay only for what the app uses (pay as you go).
4. **Example:** for **5,000 people using it every day**, the current version costs from about **$9 per day** (small migration, ~100,000 files) to about **$135 per day** (large migration, ~5 million files). A planned optimization brings this down to about **$3–7 per day**. Details in section 4.

> [!WARNING]
> **All costs in this document are estimates, not quotes. Actual costs may be higher**, depending on how your users use the app, how many files you migrate, your audit-log volume, region, and prices at the time. Please follow the cost best practices in section 5 (budget alerts, quotas, regular reviews) from day one.

## 1. Licensing: what you need

### 1.1 Google Workspace (required)
The app reads the Workspace **audit log**, which Google copies into BigQuery through the **BigQuery Export** feature. Only some editions include it:

| Includes BigQuery Export ✅ | Does not include it ❌ (examples) |
|---|---|
| Enterprise Standard, Enterprise Plus | Business Starter, Business Standard, Business Plus |
| Education Standard, Education Plus | Education Fundamentals |
| Frontline Standard, Frontline Plus | Essentials Starter |
| Enterprise Essentials Plus | |

📖 Always confirm with Google's current list: https://support.google.com/a/answer/9079365

- **License level:** your **organization's edition**. Users don't need an extra add-on to use LinkFinder.
- **Migration tool:** the files must be moved with Google's **Data Migration service** (Admin console → Data → Data import & export → Data migration). It's part of Google Workspace, not a separate purchase. Confirm it's available for your edition and source (SharePoint Online / OneDrive).
- **How to check your edition:** Admin console → **Billing → Subscriptions**.

### 1.2 Google Cloud (required, pay as you go)
- A **Google Cloud project inside your company's organization**.
- A **Cloud Billing account** linked to it (credit card or monthly invoice).
- **No upfront commitment and no license.** You're billed monthly for usage (section 3).
- Optional but recommended: a **budget alert** (section 5).

### 1.3 Microsoft 365 (optional, no purchase)
- To collect older-format sharing links before SharePoint is switched off, an admin creates a free **Microsoft Entra app registration** with read-only permission (`Sites.Read.All`). Nothing to buy.

### 1.4 LinkFinder (no purchase)
- The package (code, installer, guides) is provided to you. It runs **entirely in your own Google Cloud project**.

### 1.5 Admin roles needed (not purchases)
| Role | Needed for |
|---|---|
| Google Workspace **super admin** | Turning on the BigQuery Export, allowing Google Cloud / Cloud Shell |
| Google Cloud **Owner** on the project | Running the installer |
| **Billing Account Administrator or User** | Linking billing to the project |
| *(Optional)* Microsoft Entra **admin** | Creating the read-only app registration |

## 2. Product requirements (what the app runs on)

### 2.1 Google Cloud products used
| Product | What LinkFinder uses it for | How it's billed |
|---|---|---|
| **BigQuery** | Stores the Workspace audit log (from the export) and the app's lookup tables; answers lookups | Data **read by queries** (per TiB) and data **stored** (per GiB per month). Free: first 1 TiB of queries and 10 GiB of storage per month |
| **Cloud Run** | Runs the website and the 5-minute copy job | CPU and memory time **while working**, plus per request. **Scales to zero** when unused. Free monthly allowance (e.g. 2 million requests) |
| **Cloud Scheduler** | Starts the copy job every 5 minutes, plus nightly | Per schedule per month (a few free per billing account) |
| **Identity-Aware Proxy (IAP)** | Google sign-in in front of the app | No extra charge for basic use |
| **Cloud Build, Artifact Registry, Cloud Storage** | Building and storing the app at install and upgrade | Small, mostly one-off |
| **Cloud Logging, Cloud Monitoring** | Logs, install records, email alerts | Usually within free allowances |

### 2.2 Technical requirements
| Requirement | Detail |
|---|---|
| Workspace BigQuery Export | Switched on **before** files are migrated |
| Google Cloud APIs | 10 services enabled (the Setup Guide, P8, has the one-line command) |
| Region | Any Cloud Run region; BigQuery data stays in your export's location (e.g. US or EU) |
| Network | Users reach the app over HTTPS through Google sign-in; nothing to install on PCs |
| Browser | Any modern browser (Chrome, Edge, Safari, Firefox) |
| Users | Google accounts in your allowed Workspace domains |

## 3. What drives the cost
1. **Number of migrated files** (the biggest factor). Every lookup searches LinkFinder's lookup table, and BigQuery charges by the amount of data a search reads. The more files you migrated, the bigger the table, and in the current version **the more each lookup costs** (section 4.2).
2. **Number of lookups.** More users, or more links per user, means more searches. Bulk lookups (up to 500 links) read about as much as a single one.
3. **"Not found" links.** For links not yet in the lookup table, the app also checks the newest audit-log rows, so files migrated minutes ago are found immediately. That extra check reads part of the audit log every time.
4. **The copy job** runs 288 times a day. Each run reads the last 1–2 days of audit log (more during active migration waves).
5. **Audit log size and retention.** Your Workspace audit log (all apps, not only migrations) is stored in BigQuery and grows every day.
6. **Website traffic** (Cloud Run) is small, because it scales to zero when unused.

## 4. Example: 5,000 people using it every day

> [!WARNING]
> **Estimate only.** These numbers come from usage measured on a test installation plus a 5-million-file load test, scaled up with Google's published prices. **Real costs can be higher.** Use them for planning, set a budget alert (section 5), and confirm with the Google Cloud pricing calculator: https://cloud.google.com/products/calculator

### 4.1 Assumptions ("typical" usage)
| Assumption | Value |
|---|---|
| People using it per day | 5,000 (each opens the app once) |
| Lookups per person per day | 3 (15,000 lookups a day) |
| Links not found (not migrated, typos) | 20% |
| Audit-log events per person per day | 300 |
| Prices | US region: BigQuery $6.25 per TiB read (first 1 TiB/month free), storage $0.02 per GiB/month, standard Cloud Run rates; free monthly allowances subtracted |

### 4.2 Result: cost per day (and per month) by migration size
| Files migrated | Data read per lookup (current) | **Current version** | Planned optimization (not yet released) |
|---|---|---|---|
| 100,000 | ≈ 30 MB (BigQuery minimum) | **≈ $9 / day** (≈ $280 / month) | ≈ $2.65 / day (≈ $80 / month) |
| 1 million | ≈ 300 MB | **≈ $33 / day** (≈ $985 / month) | ≈ $2.90 / day (≈ $87 / month) |
| 5 million | ≈ 1.5 GB (measured) | **≈ $137 / day** (≈ $4,100 / month) | ≈ $7.25 / day (≈ $220 / month) |
| 10 million | ≈ 3 GB | **≈ $265 / day** (≈ $8,000 / month) | ≈ $12.70 / day (≈ $380 / month) |

**How these were checked:** a temporary table with **5 million migrated files** (3 GB) was loaded into BigQuery and the app's real lookup query was run against it. Google **billed ≈ 1.5 GB for a single lookup** (≈ 2 GB for 10 or 200 links). The same lookup on a table organized the "optimized" way was billed **≈ 64 MB**. Other sizes are scaled from these measurements.

**What scales the numbers:** cost grows roughly in line with the number of lookups. Half the usage (1–2 lookups per person) costs about half; three times the usage costs about three times as much.

> [!IMPORTANT]
> **Large migrations (more than about 1 million files): plan for the optimization before rollout.**
> - In the current version, lookup cost rises with the number of files.
> - Each lookup also has a built-in safety cap of 2 GiB. Above roughly **6 million files**, single lookups would reach that cap and be refused until the optimization is in place.

### 4.3 What the planned optimization changes
- The lookup table is organized by link and by file ID, so each lookup reads only a few tens of MB instead of the whole table.
- The extra "newest rows" check for links not found is limited. A file migrated **in the last few minutes** becomes findable at the next copy run (within 5 minutes) instead of instantly.
- Nothing else changes for users.

## 5. Cost best practices (please follow from day one)
1. **Set a budget alert** (5 minutes): Google Cloud console → **Billing → Budgets & alerts → Create budget** → choose the project → set a monthly amount → alerts at 50%, 90% and 100%. You get an email if spending rises.
2. **Cap BigQuery usage with a quota**: Google Cloud console → **IAM & Admin → Quotas** → search **BigQuery API, "Query usage per day"** → set a daily limit for the project (for example 2–5 TiB). Queries above the limit are refused instead of billed.
3. **Review the bill weekly** during rollout (Billing → Reports, filter by project). Compare it with section 4.
4. **Remember that free allowances are shared.** The free monthly BigQuery and Cloud Run allowances apply per billing account. If other projects already use them, LinkFinder's cost is higher than shown.
5. **Large migration (> 1 million files)?** Ask for the planned optimization before rolling out to everyone, or start with a pilot group.
6. **Keep lookups purposeful.** Bulk lookups are efficient for many links, but repeated refreshes of the same list cost the same each time.
7. **Choose the region deliberately.** Prices differ by region. Keep BigQuery and Cloud Run in your preferred location.
8. **Audit-log retention:** the audit log export is usually the largest storage item over time. Agree a retention period with your compliance team (never shorter than 7 days; LinkFinder's own lookup tables keep the mappings).
9. **Remove it when the project ends:** `./uninstall.sh --yes` stops all LinkFinder costs. The audit-log export stays under your control.

## 6. Summary checklist
| # | Item | Purchase? |
|---|---|---|
| 1 | Workspace edition with **BigQuery Export** | Upgrade only if you're on an edition without it |
| 2 | Google Workspace **Data Migration service** | Included in Workspace (confirm availability) |
| 3 | Google Cloud **billing account** | No purchase, pay as you go |
| 4 | LinkFinder app | ❌ No |
| 5 | Microsoft Entra app (optional) | ❌ No |
| 6 | Expected running cost, 5,000 daily users (estimate) | Today: ≈ $9/day (100k files) to ≈ $137/day (5M files); planned optimization: ≈ $3–7/day. **May be higher**; follow section 5 |

*Disclaimer: all costs are estimates based on measured test usage and Google's published prices at the time of writing; actual costs may be higher. Always confirm current pricing and edition features with Google or your Google partner, and monitor your bill.*
