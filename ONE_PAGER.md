# Link Finder: one-pager
**Keep every old SharePoint / OneDrive link working after moving to Google Drive.**

## The problem
Files move to Google Drive with Google's **Data Migration service**, but the **old SharePoint / OneDrive links** in emails, chats, documents and Outlook "modern attachments" don't. Once SharePoint is switched off, they're dead.

## How Link Finder solves it
1. **Google's own record:** for every migrated file, Google logs the **old link → new Drive link** in the Workspace audit log.
2. **Automatic collection:** BigQuery Export plus a 5-minute sync turn that log into a lookup table in the customer's project.
3. **Any link works:** address-bar, Office viewer, **sharing links and Outlook modern attachments**.
4. **Instant answer:** employees sign in, paste an old link (or 200), and get the Drive link.
5. **Survives SharePoint shutdown:** answers come from Google's record, not from SharePoint.

## Architecture
![Link Finder architecture](docs/link-finder-architecture.png)

## What you need to have this in place
| Area | Requirement |
|---|---|
| Google Workspace | Edition with **BigQuery Export** (e.g. Enterprise Standard / Plus), switched **on before migrating** |
| Migration | Google's **Data Migration service**, including OneDrive **Attachments** folders |
| Google Cloud | Project **in the company organization** with **billing**; required services enabled (Cloud Run, BigQuery, Scheduler, IAP…) |
| People and setup | Workspace **super admin** + Google Cloud **Owner**; install with **`./install.sh`** in Cloud Shell (about 15 min) |
| Optional | Read-only Microsoft Entra app to collect older sharing links before SharePoint shutdown |
