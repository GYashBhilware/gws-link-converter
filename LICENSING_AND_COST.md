# LinkFinder: licensing, product requirements and cost estimate
*SharePoint / OneDrive link → Google Drive link*

## In short
1. **LinkFinder itself costs nothing.** There's no license and no Marketplace purchase; you receive the package.
2. You need a **Google Workspace edition that includes BigQuery Export** (for example Enterprise Standard / Plus).
3. You need a **Google Cloud project with a billing account**. You pay only for what the app uses (pay as you go).
4. **Example:** for **5,000 people using it every day**, the current version costs roughly **$2–10 per day** for light to typical use (about **$60–280 per month**). A planned optimization brings typical use down to about **$3 per day**. Details in section 4.

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
1. **Lookups.** Each lookup reads a small amount from BigQuery, but Google bills a **minimum of 10 MB per table** for each query.
2. **The copy job** runs **288 times a day** (every 5 minutes).
3. **Audit log size.** Your Workspace audit log grows with company size and activity (all apps, not only migrations). It affects storage and how much the copy job reads.
4. **"Not found" links** (live check). For links not yet in its lookup tables, the app also searches the newest audit-log rows, so files migrated in the last few minutes are found immediately. This is the **largest cost** for big companies. The planned optimization limits it (section 4.3).
5. **Website traffic** (Cloud Run) is small, because it scales to zero.

## 4. Example: 5,000 people using it every day

### 4.1 Assumptions
| Assumption | Light | Typical | Heavy |
|---|---|---|---|
| People using it per day | 5,000 | 5,000 | 5,000 |
| Lookups per person per day | 1 | 3 | 10 |
| Lookups per day (total) | 5,000 | 15,000 | 50,000 |
| Links not found (not migrated, typos) | 10% | 20% | 30% |
| Audit-log events per person per day | 100 | 300 | 1,000 |

Other assumptions:
- Each person opens the app once a day (about 4 small page requests plus their lookups).
- Copy job every 5 minutes.
- **US prices** (BigQuery on-demand $6.25 per TiB read; storage $0.02 per GiB per month; standard Cloud Run rates).
- Free monthly allowances are subtracted.
- Usage figures (bytes billed per copy run and per lookup) were **measured on a running installation** and scaled up.

### 4.2 Result: cost per day and per month (USD, estimate)
| Version | Light | Typical | Heavy |
|---|---|---|---|
| ✅ **Current version** (what LinkFinder does today) | **≈ $2.00 / day** (≈ $60 / month) | **≈ $9.40 / day** (≈ $280 / month) | **≈ $95 / day** (≈ $2,870 / month) |
| 🔜 Planned optimization (not yet released: live check limited, single lookup table) | ≈ $1.10 / day (≈ $33 / month) | ≈ $2.65 / day (≈ $80 / month) | ≈ $8 / day (≈ $245 / month) |

> [!IMPORTANT]
> The **current version** row is what an installation costs today. The planned optimization row shows the expected effect of a change that is **not yet released**. Large organizations ("Heavy" usage) should wait for it, or ask for it, before rollout.

### 4.3 Where the money goes ("Typical", 15,000 lookups a day)
| Item | Current version | Planned optimization |
|---|---|---|
| BigQuery: lookups | $2.56 | $0.85 |
| BigQuery: live search for "not found" links | $5.03 | $0.00 (off) |
| BigQuery: copy job (288 runs) | $0.76 | $0.76 |
| BigQuery: app start / freshness check | $0.28 | $0.28 |
| Cloud Run (website) | $1.02 | $1.02 |
| BigQuery storage (audit log, after 1 year) | $0.12 | $0.12 |
| Scheduler, logs, alerts | $0.02 | $0.02 |
| Free monthly allowances (per day) | −$0.41 | −$0.41 |
| **Total per day** | **≈ $9.40** | **≈ $2.65** |

**What the planned optimization will change for users:** a file migrated **in the last few minutes** becomes findable at the next copy run (within 5 minutes), instead of instantly. Nothing else changes.

> [!NOTE]
> These are **estimates**, not quotes. Real cost depends on how people use the app, the size of your audit log, your region (prices outside the US can differ), and any discounts on your billing account. For an exact figure, use the Google Cloud pricing calculator: https://cloud.google.com/products/calculator

### 4.4 Other company sizes (planned optimization, "Typical" usage, rough guide)
| Daily users | Lookups / day | Approx. cost / day | Approx. cost / month |
|---|---|---|---|
| 500 | 1,500 | ≈ $0.10 (mostly covered by free allowances) | ≈ $2 |
| 5,000 | 15,000 | ≈ $2.65 | ≈ $80 |
| 20,000 | 60,000 | ≈ $11 | ≈ $340 |

## 5. Keep the cost under control
1. **Set a budget alert** (5 minutes): Google Cloud console → **Billing → Budgets & alerts → Create budget** → choose the project → set a monthly amount (e.g. $150) → alerts at 50%, 90% and 100% → **Finish**. You get an email if spending rises.
2. **Ask for the planned optimization** (live check limited, single lookup table) before rolling out to a large organization.
3. **Copy job every 15 minutes** instead of 5, if a short delay is acceptable (saves a little more).
4. **Audit log retention:** the audit log export is usually the largest storage item over time. Ask your Google Cloud admin whether to keep it indefinitely or set a retention period in BigQuery, in line with your compliance rules.
5. **Remove it when the project ends:** `./uninstall.sh --yes` stops all app costs. The audit-log export stays under your control.

## 6. Summary checklist
| # | Item | Purchase? |
|---|---|---|
| 1 | Workspace edition with **BigQuery Export** | Upgrade only if you're on an edition without it |
| 2 | Google Workspace **Data Migration service** | Included in Workspace (confirm availability) |
| 3 | Google Cloud **billing account** | No purchase, pay as you go |
| 4 | LinkFinder app | ❌ No |
| 5 | Microsoft Entra app (optional) | ❌ No |
| 6 | Expected running cost, 5,000 daily users | ≈ $2–10 per day today (light–typical); ≈ $1–3 per day after the planned optimization |

*Prices and editions as published by Google at the time of writing. Always confirm current pricing and edition features with Google or your Google partner.*
