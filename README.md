# Sussex Cancer Research Centre Website (`sussexcancer.org`)

Static Astro rebuild of [sussexcancer.org](https://sussexcancer.org) (incorporating the streamlined `homev2draft` homepage and `/research` hub), designed for **£0/month hosting on Cloudflare Pages** with all images, documents, news posts, events, lived experience stories, publications, and member profiles stored cleanly inside this repository.

---

## Recreating All 7 Squarespace Forms as Google Forms (1-Click Setup)

All 7 original Squarespace forms (Get Involved, Events Sign-Up, Experts by Experience, Mentorship Scheme, Contact Us, LyMIT 2026 Interest, and Symposium Poster Abstracts) have been captured with every exact question, checkbox, multiple-choice option, help description, and required flag in **[`scripts/create-google-forms.gs`](./scripts/create-google-forms.gs)**.

To generate all 7 live Google Forms in your Google Drive in ~10 seconds:
1. Open **[script.new](https://script.new)** while signed into the Google account that should own the forms.
2. Paste the contents of [`scripts/create-google-forms.gs`](./scripts/create-google-forms.gs) and click **Run** (`createAllSCRCForms`).
3. Approve permissions — it will create a **`SCRC Website Forms`** folder in your Google Drive with all 7 Google Forms + linked response Google Sheets, and print a ready-to-paste JSON block in the Execution Log.
4. Paste those 7 URLs into [`src/data/site.json`](./src/data/site.json) under `externalLinks` (or via **Pages CMS → Site Settings & Links**). Every form CTA button across the website updates automatically!
5. **Enable Email Notifications (Optional):** Paste [`scripts/setup-form-email-notifications.gs`](./scripts/setup-form-email-notifications.gs) into [script.new](https://script.new) and run `installFormEmailTriggers` once. Every submission across all 7 forms will automatically email the configured SCRC recipients with all submitted answers, a direct Reply-To to the sender, and a button linking straight to the responses Google Sheet.

---

## Editing Content (3 Easy Ways)

### Option 1: Browser-based Visual CMS via Pages CMS *(Recommended for non-technical editors)*
This repo includes a ready-to-use [`.pages.yml`](./.pages.yml) configuration for **[Pages CMS](https://app.pagescms.org)** (100% free, open-source, no backend server needed):
1. Visit **[app.pagescms.org](https://app.pagescms.org)** and sign in with GitHub.
2. Select **`SiFTW/SCRC-website`**.
3. Visually add or edit **News Articles**, **Events**, **Lived Experience Stories**, **People Directory**, **Jobs**, and **Site Settings** (including the top announcement bar and Google Form URLs), with image uploads.
4. Click **Save** — it commits directly to GitHub and Cloudflare Pages automatically deploys the update in ~30 seconds.

### Option 2: GitHub Issue Forms + Automated Pull Requests
1. Go to the **Issues** tab in GitHub and click **New Issue**.
2. Choose **Add News Article** or **Add Event** and fill out the form.
3. A maintainer adds the **`publish`** label to the issue.
4. The [`.github/workflows/publish-from-issue.yml`](./.github/workflows/publish-from-issue.yml) workflow automatically generates the Markdown file and opens a Pull Request (with a live Cloudflare Pages preview link). Merge to publish!

### Option 3: Edit Markdown / JSON Directly on GitHub
* **News articles:** `src/content/news/*.md`
* **Events:** `src/content/events/*.md`
* **Lived Experience Stories:** `src/content/stories/*.md`
* **People Directory (212 members):** `src/content/people/*.md`
* **Publications (80 papers):** `src/data/publications.json`
* **Research Themes:** `src/data/themes.json`
* **Funding Call & Funded Projects:** `src/data/funding.json`
* **Jobs & Vacancies:** `src/data/jobs.json`
* **Shared Resources & Equipment:** `src/data/resources.json`
* **Site Settings, Announcement Bar & Form URLs:** `src/data/site.json`

---

## Automated Publications & Sussex Researcher Discovery

A weekly GitHub Action ([`.github/workflows/sync-publications.yml`](./.github/workflows/sync-publications.yml)) runs every **Monday at 07:00 UTC** (and can be triggered manually anytime from the **Actions** tab):

1. Queries the free **Europe PMC** and **ORCID** APIs for recent papers authored by:
   * Any SCRC member with an `orcid` in `src/content/people/*.md`
   * Any researcher affiliated with **University of Sussex**, **BSMS**, **University of Brighton**, or **Sussex NHS Trusts**
2. Filters for cancer relevance — including indirect foundational & clinical links (DNA damage/repair, genome stability, replication stress, drug discovery/PROTACs/Hsp90, cancer-related fatigue, palliative care, EORTC).
   * **Optional AI Triage:** Add a free `GEMINI_API_KEY` secret in **Repo Settings → Secrets and variables → Actions** to enable Gemini 2.5 Flash triage on indirect/borderline papers.
3. Opens a **Pull Request** (`auto/sync-publications`) listing every proposed new publication and any newly discovered Sussex/BSMS/Brighton/NHS cancer researchers.
4. Review the PR and click **Merge** — Cloudflare Pages updates `/publications` and `/people` automatically in ~30 seconds.
5. To permanently exclude a paper or author from future runs, add their DOI to [`src/data/publications-ignored.json`](./src/data/publications-ignored.json) or their name/ORCID to [`src/data/members-ignored.json`](./src/data/members-ignored.json).

---

## Deploying to Cloudflare Pages (£0/month)

1. Sign in to the **[Cloudflare Dashboard](https://dash.cloudflare.com)** → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**.
2. Select the **`SiFTW/SCRC-website`** repository.
3. Use these build settings:
   * **Framework preset:** `Astro`
   * **Build command:** `npm run build`
   * **Build output directory:** `dist`
4. Click **Save and Deploy** — you'll get a free `*.pages.dev` preview URL immediately.
5. **Attach `sussexcancer.org`:**
   * In your Cloudflare Pages project → **Custom domains** → add `sussexcancer.org` and `www.sussexcancer.org`.
   * Point your domain's DNS while keeping your existing MX/TXT records untouched so `@sussexcancer.org` email keeps working without interruption.
   * Once verified and HTTPS is active, cancel your Squarespace website plan.

---

## Local Development

```bash
npm install
npm run dev       # Local dev server at http://localhost:4321
npm run build     # Production static build to ./dist
npm run preview   # Preview production build locally
```