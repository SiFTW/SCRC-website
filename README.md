# Sussex Cancer Research Centre Website (`sussexcancer.org`)

Static Astro rebuild of [sussexcancer.org](https://sussexcancer.org), designed for **£0/month hosting on Cloudflare Pages** with all images, documents, news posts, events, lived experience stories, publications, and member profiles stored cleanly inside this repository.

---

## Editing Content (3 Easy Ways)

### Option 1: Browser-based Visual CMS via Pages CMS *(Recommended for non-technical editors)*
This repo includes a ready-to-use [`.pages.yml`](./.pages.yml) configuration for **[Pages CMS](https://app.pagescms.org)** (100% free, open-source, no backend server needed):
1. Visit **[app.pagescms.org](https://app.pagescms.org)** and sign in with GitHub.
2. Select **`SiFTW/SCRC-website`**.
3. Visually add or edit **News Articles**, **Events**, **Lived Experience Stories**, **People Directory**, **Jobs**, and **Site Settings** (including the top announcement bar and form links), with image uploads.
4. Click **Save** — it commits directly to GitHub and Cloudflare Pages automatically deploys the update in ~30 seconds.

### Option 2: GitHub Issue Forms + Automated Pull Requests
1. Go to the **Issues** tab in GitHub and click **New Issue**.
2. Choose **Add News Article** or **Add Event** and fill out the form.
3. A maintainer adds the **`publish`** label to the issue.
4. The [`.github/workflows/publish-from-issue.yml`](./.github/workflows/publish-from-issue.yml) workflow automatically generates the Markdown file and opens a Pull Request (with a live Cloudflare Pages preview link). Merge to publish!

### Option 3: Edit Markdown / JSON Directly on GitHub
All content lives in intuitive files you can edit directly via GitHub's web editor (pencil icon):
* **News articles:** `src/content/news/*.md`
* **Events:** `src/content/events/*.md`
* **Lived Experience Stories:** `src/content/stories/*.md`
* **People Directory (212 members):** `src/content/people/*.md`
* **Publications (80 papers):** `src/data/publications.json`
* **Research Themes:** `src/data/themes.json`
* **Funding Call & Funded Projects:** `src/data/funding.json`
* **Jobs & Vacancies:** `src/data/jobs.json`
* **Shared Resources & Equipment:** `src/data/resources.json`
* **Site Settings, Announcement Bar & External Forms:** `src/data/site.json`

---

## Connecting Institutional Forms (GDPR Best Practice)

The **Join the SCRC**, **Experts by Experience**, and **Mentorship** buttons read their URLs from [`src/data/site.json`](./src/data/site.json) (`externalLinks`).
* Currently they default to pre-filled `mailto:Hello@SussexCancer.org` links so nothing breaks.
* When ready, create free **University of Sussex / BSMS Microsoft Forms** (or Qualtrics/REDCap forms) and paste their URLs into `src/data/site.json` (or via Pages CMS → **Site Settings & Links**).

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
   * Point your domain's DNS (recommended: move DNS to Cloudflare Free, or add the CNAME records at your current registrar).
   * Keep your existing MX/TXT records untouched so `@sussexcancer.org` email keeps working without interruption.
   * Once verified and HTTPS is active, cancel your Squarespace website plan (keeping only domain registration, or transferring the domain to Cloudflare Registrar at cost).

---

## Local Development

```bash
npm install
npm run dev       # Local dev server at http://localhost:4321
npm run build     # Production static build to ./dist
npm run preview   # Preview production build locally
```