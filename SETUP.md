# Villa Intelligence Dashboard — Live Data Setup

The dashboard (`index.html`) reads its data from `data.json`. A scheduled
GitHub Action (`.github/workflows/update.yml`) runs `update.py` every 6 hours,
which scrapes fan/news sources, asks Claude to rewrite the dashboard data, and
commits an updated `data.json`. GitHub Pages then serves the fresh file.

If `data.json` can't be loaded (e.g. you open `index.html` straight from your
hard drive), the page silently falls back to the baked-in data inside the HTML,
so it always renders something.

---

## One-time setup

### 1. Push this folder to GitHub
Create a repo and push everything (including `data.json`, `update.py`,
`requirements.txt`, and the `.github/` folder).

### 2. Turn on GitHub Pages
Repo **Settings → Pages → Build and deployment**:
- Source: **Deploy from a branch**
- Branch: **main**, folder: **/ (root)** → Save

Your site appears at `https://<your-username>.github.io/<repo-name>/`.

### 3. Add your Claude API key (required)
1. Get a key at <https://console.anthropic.com> → API Keys.
2. Repo **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `ANTHROPIC_API_KEY`
   - Value: your key

### 4. Add Reddit app credentials — needed for fan-opinion data
Reddit now **blocks** anonymous JSON requests from most IPs (you'll see a `403
Blocked`), so without these the script runs on Google News alone. A free "script"
app unlocks the fan discussion:
1. Go to <https://www.reddit.com/prefs/apps> → **create another app…**
2. Type: **script**. Name it anything. Redirect URI: `http://localhost`.
3. Add two repo secrets:
   - `REDDIT_CLIENT_ID`  — the string just under the app name
   - `REDDIT_CLIENT_SECRET` — the "secret" field

The pipeline still works without these — it just relies on news/recaps only and
skips Reddit fan sentiment.

### 5. Allow the Action to commit
Repo **Settings → Actions → General → Workflow permissions** →
**Read and write permissions** → Save. (The workflow also requests this itself.)

### 6. Generate the first live `data.json`
Repo **Actions** tab → **Update villa data** → **Run workflow**. After ~1 minute
it commits a refreshed `data.json` and your Pages site updates automatically.

---

## How it stays accurate

- **Factual anchors are locked.** Names, ages, jobs, hometowns, photos, colours,
  sex and entry day live in an `ANCHORS` table inside `update.py` and are
  re-applied after every Claude run — the model can't corrupt them.
- **Editorial fields are live.** Couples, statuses, drama, storylines, episode
  log, history and the 0–100 compatibility/sentiment scores are rewritten each
  run from what fans and recaps are saying.
- **Fail-safe.** If scraping returns nothing, or Claude returns invalid JSON, or
  validation fails, the run exits without touching `data.json` — the site keeps
  showing the last good version.

## Changing the cadence
Edit the `cron` line in `.github/workflows/update.yml`. Examples:
- Every 3 hours: `0 */3 * * *`
- Hourly during finale week: `0 * * * *`
You can always hit **Run workflow** for an instant refresh.

## Next season
Replace the cast in the `ANCHORS` table in `update.py` and reset `data.json`
to the new roster. Update `WIKI_ARTICLE` in `index.html` for the Wikipedia
status sync.

## Rough cost
Claude **Haiku** at ~16k output tokens/run, 4 runs/day ≈ a few cents per day —
on the order of **$1–3 for an entire season**. Reddit and Google News are free.
