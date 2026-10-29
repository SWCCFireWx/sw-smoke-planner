# SW Smoke Dispersion & Burn-Window Planner

A free web page, hosted on GitHub Pages, for smoke dispersion planning in the
**Southwest GACC (Arizona & New Mexico)** with the edges of the neighboring
GACCs (Southern California, Great Basin, Rocky Mountain, Southern Area).

It shows, hour by hour for the next 4 days:

- **Ventilation rate** (mixing height × transport wind, knot-feet) rated on the New Mexico
  smoke-management scale: Poor < 40k · Fair 40–60k · Good 60–100k · Very Good 100–150k · Excellent ≥ 150k
- **Mixing height** and **transport wind** (with arrows pointing where smoke will go)
- **Chance of precipitation**, **24-hour precip totals**, **10-m wind**, **gusts**, **RH**
- **Burn windows**: hours that meet *your* criteria (ventilation, precip chance, wind, gusts, RH, time of day)
- Current **wildfire and prescribed-fire perimeters** (NIFC WFIGS) and **GACC boundaries**
- All times in **both New Mexico (Mountain) time and Arizona time** — DST is handled automatically

You never have to touch ArcGIS. A GitHub Action downloads everything on a schedule
and saves it into this repository; the page just reads those saved files.

---

## What's in this repository

| File | What it does |
|---|---|
| `index.html` | The web page (map, charts, burn windows) |
| `fetch_data.py` | Downloads perimeters, GACC boundaries and the forecast into the `data/` folder |
| `.github/workflows/update-data.yml` | Tells GitHub to run `fetch_data.py` every 2 hours and save the results |
| `data/` | Created automatically by the first run — don't create it yourself |

---

## Step-by-step setup (about 15 minutes, all in a web browser)

### Step 1 — Create a GitHub account
1. Go to **https://github.com** and click **Sign up**.
2. Pick a username — it becomes part of your web address
   (`https://YOUR-USERNAME.github.io/...`), so choose something you're happy sharing.
3. Verify your email.

### Step 2 — Create a new repository
1. Click the **+** in the top-right corner → **New repository**.
2. **Repository name:** `sw-smoke-planner` (any name works; it becomes part of the URL).
3. Set it to **Public**. *(Free GitHub Pages requires a public repo.)*
4. Check **Add a README file**.
5. Click **Create repository**.

### Step 3 — Upload the page and the script
1. In your new repository, click **Add file → Upload files**.
2. Drag in **`index.html`**, **`fetch_data.py`** and **`README.md`** (replace the README GitHub made).
3. Scroll down and click **Commit changes**.

### Step 4 — Add the scheduled workflow
Hidden folders like `.github` often don't upload by drag-and-drop, so create this one by hand:

1. Click **Add file → Create new file**.
2. In the file-name box type exactly:
   `.github/workflows/update-data.yml`
   (each `/` you type creates a folder — that's expected).
3. Open `update-data.yml` from the zip in Notepad/TextEdit, copy **all** of it, and paste it into the big edit box.
4. Click **Commit changes…** → **Commit changes**.

### Step 5 — Let the workflow save data
1. Go to **Settings** (tab at the top of the repo) → **Actions** → **General**.
2. Under **Workflow permissions**, choose **Read and write permissions**.
3. Click **Save**.

### Step 6 — Turn on GitHub Pages
1. **Settings** → **Pages** (left sidebar).
2. Under **Build and deployment → Source**, choose **Deploy from a branch**.
3. **Branch:** `main`, folder **`/ (root)`** → **Save**.

### Step 7 — Run the first data download
1. Click the **Actions** tab.
2. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
3. Click **Update data** on the left → **Run workflow** (right side) → green **Run workflow**.
4. Wait for the green check mark (about 3–6 minutes; the forecast for ~350 grid points is
   downloaded in polite batches).
5. A `data/` folder now appears in the repository.

### Step 8 — Open your page
Go back to **Settings → Pages**. After a minute or two it shows:
**"Your site is live at `https://YOUR-USERNAME.github.io/sw-smoke-planner/`"**.
Bookmark it and share it with your crew.

From now on it updates by itself: perimeters every 2 hours, forecast about 4 times a day.

---

## Using the page

- **Map layer** (left): start with *Ventilation rate* or *Burn-window hours (selected day)*.
- **Forecast hour slider** / **Jump to 1 PM on** buttons / ◀ ▶ keys move through time.
- The two clocks at the top always show the selected hour in **New Mexico** and **Arizona** time.
  From early November to mid-March they match; in summer Arizona is 1 hour behind.
  The Navajo Nation observes daylight time — use the NM clock there.
- **NM / Arizona** buttons choose which time zone the day buttons, charts and tables use.
- **Burn-window criteria** are saved in your browser. Change them to match your burn plan prescription.
- **Click any grid square** for charts, a list of potential burn windows (in both time zones) and a daily summary.
- Click a fire perimeter for name, type (wildfire / prescribed), acres, containment and dates.

## Limitations — please read
- Forecast is model guidance (NOAA GFS + HRRR via Open-Meteo) on a ~0.75° (~45 mile) grid.
  It does **not** resolve valley inversions, drainage winds or terrain channeling — overnight
  smoke in valleys will be worse than shown. **Always request a NWS spot forecast** and follow
  your burn plan and smoke permit (NMED, ADEQ, tribal or local air agency).
- Perimeters only exist for incidents that have one published in WFIGS; small or new fires may not show.
- Open-Meteo's free API is for non-commercial use.

## Troubleshooting
| Problem | Fix |
|---|---|
| Page says "No forecast data yet" | Run Step 7, wait for the green check, reload |
| Red ✗ on the Actions run | Click it → click the failed step to read the error. Most common: Step 5 not done (push permission denied) |
| Header shows "⚠ … h old" | Check the Actions tab; re-run **Update data** manually |
| Scheduled runs stopped | GitHub pauses schedules after 60 days with no repository activity. Open **Actions → Update data → Enable workflow** |
| Page didn't change after an edit | Wait 1–2 minutes, then hard-refresh (Ctrl+Shift+R / Cmd+Shift+R) |

## Changing the map area or grid
Edit the `BBOX` (perimeters/boundaries) and `GRID` (forecast points) values near the top of
`fetch_data.py`. A finer `step` means more points: keep the total under ~500 so the free
forecast API's daily limit isn't exceeded.

## Data sources
- NIFC WFIGS Current Interagency Fire Perimeters (ArcGIS REST)
- NIFC National GACC Boundaries (ArcGIS REST)
- Open-Meteo GFS API (NOAA GFS and HRRR), CC BY 4.0
- Ventilation categories: New Mexico Smoke Management Program
