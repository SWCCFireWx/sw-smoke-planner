# SW Smoke Dispersion & Burn-Window Planner

A free web page, hosted on GitHub Pages, for smoke dispersion planning in the
**Southwest GACC (Arizona & New Mexico)** plus the edges of the neighboring
GACCs (Southern California, Great Basin, Rocky Mountain, Southern Area).

Live site (once set up): **https://swccfirewx.github.io/sw-smoke-planner/**

## What it shows
All forecast data come from the **NOAA National Blend of Models (NBM)**, the
2.5-km CONUS blend that NWS forecasters start from. It runs hourly; the site
picks up the newest run every 3 hours. The map is a smooth, interpolated
surface (0.1° / ~10 km grid built from the 2.5-km NBM).

- **Ventilation rate** = NBM mixing height (ft AGL) × NBM transport wind (kt), rated on the
  New Mexico smoke-management scale: Poor < 40k · Fair 40–60k · Good 60–100k · Very Good 100–150k · Excellent ≥ 150k knot-feet
- **Mixing height**, **transport wind** (arrows point where smoke goes)
- **Chance of precipitation** (NBM 1-hr PoP; 3-hr after 48 h), **24-hour precip totals**
- **10-m wind**, **gusts**, **RH**, **temperature**
- **Burn windows**: hours meeting *your* criteria (ventilation, precip chance, wind, gusts, RH, time of day)
- Current **wildfire and prescribed-fire perimeters** (NIFC WFIGS) and **GACC boundaries**
- All times in **both New Mexico (Mountain) time and Arizona time**, DST handled automatically

Forecast length: hourly through 48 hours, then every 3 hours out to 96 hours.

## How it works
A GitHub Action (`.github/workflows/update-data.yml`) runs every 3 hours. It:
1. downloads only the needed NBM fields from NOAA's public data bucket on AWS,
2. downloads current perimeters and GACC boundaries from NIFC,
3. packages them with `index.html` and publishes the site to GitHub Pages.

Nothing is committed to the repository, so it stays small. You never touch ArcGIS,
NOAA servers or API keys.

| File | What it does |
|---|---|
| `index.html` | The web page (map, charts, burn windows) |
| `fetch_data.py` | Downloads NBM, perimeters and boundaries and builds the `data/` folder |
| `.github/workflows/update-data.yml` | Runs the script every 3 hours and publishes the site |

---

## Step-by-step setup (about 15 minutes, all in a web browser)

> **Already set up the earlier (Open-Meteo) version?** Skip to
> [Updating from the earlier version](#updating-from-the-earlier-version).

### Step 1: Create a GitHub account
Go to **https://github.com**, click **Sign up**, and verify your email.
(This project uses the username **SWCCFireWx**.)

### Step 2: Create a new repository
1. Click **+** (top right) → **New repository**.
2. **Repository name:** `sw-smoke-planner`
3. Choose **Public** (free GitHub Pages requires a public repo).
4. Check **Add a README file** → **Create repository**.

### Step 3: Upload the page and the script
1. In the repo, click **Add file → Upload files**.
2. Drag in **`index.html`**, **`fetch_data.py`** and **`README.md`**.
3. Click **Commit changes**.

### Step 4: Add the workflow file
Hidden folders like `.github` often don't drag-and-drop, so create it by hand:
1. **Add file → Create new file**.
2. File name, typed exactly: `.github/workflows/update-data.yml`
   (typing each `/` makes a folder; that's expected).
3. Open `update-data.yml` in Notepad (Windows) or TextEdit (Mac), copy all of it, and paste it into the edit box.
4. **Commit changes…** → **Commit changes**.

### Step 5: Turn on GitHub Pages, set to "GitHub Actions"
1. **Settings** (top of the repo) → **Pages** (left sidebar).
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
   (Not "Deploy from a branch".) There's nothing else to save.

### Step 6: Run it the first time
1. Click the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Update data** (left) → **Run workflow** → green **Run workflow**.
3. Wait for both jobs (**build** and **deploy**) to show green checks, about 5–10 minutes.

### Step 7: Open your site
**https://swccfirewx.github.io/sw-smoke-planner/**

(Also shown under **Settings → Pages**, and as a link on the finished workflow run.)
From now on it updates itself every 3 hours.

---

## Updating from the earlier version
If you already set up the Open-Meteo version:
1. Upload the new **`index.html`**, **`fetch_data.py`** and **`README.md`** with **Add file → Upload files**
   (they replace the old ones) → **Commit changes**.
2. Open `.github/workflows/update-data.yml` in the repo, click the **pencil** (Edit), select everything,
   paste in the new version, and **Commit changes**.
3. **Settings → Pages → Source:** change to **GitHub Actions**.
4. **Actions → Update data → Run workflow.**
5. Optional: the old `data/` folder in the repo is no longer used. To delete it, open it,
   then use the **…** menu → **Delete directory** and commit.

---

## Using the page
- **Map layer** (left): start with *Ventilation rate* or *Burn-window hours (selected day)*.
- **Hover** anywhere for values; **click** for charts, burn windows (in both time zones) and a daily table.
- **Forecast hour slider**, the **Jump to 1 PM** day buttons, ◀ ▶ arrow keys, or **Play** to animate.
- The two clocks always show the selected time in **New Mexico** and **Arizona** time.
  November to mid-March they match; in summer Arizona is 1 hour behind.
  The Navajo Nation observes daylight time, so use the NM clock there.
- **NM / Arizona** buttons choose the time zone for day buttons, charts and tables.
- **Burn-window criteria** are saved in your browser; set them to your prescription.
- Basemaps (topo, imagery, light gray, OSM) are in the layer control at top right.

## Limitations
- NBM is the best starting point NWS forecasters use, but it is still guidance. Even at 2.5 km it
  under-resolves valley inversions and drainage flow, so overnight smoke in valleys can be worse than shown.
  **Always get a NWS spot forecast** and follow your burn plan and smoke permit (NMED, ADEQ, tribal or local air agency).
- Perimeters exist only for incidents with a published WFIGS perimeter.
- NBM covers CONUS; the far southern edge of the map (Mexico) may be blank.

## Troubleshooting
| Problem | Fix |
|---|---|
| Site shows 404 | Step 5: Pages source must be **GitHub Actions**, then rerun Step 6 |
| "No forecast data yet" | Run Step 6 and wait for both green checks, then reload |
| Red ✗ on a run | Click the run → click the failed step to read the error. If NBM was briefly unavailable, the previous site stays up; the next run usually fixes it |
| Header shows "⚠ … h old" | Check the Actions tab; rerun **Update data** |
| Scheduled runs stopped | GitHub pauses schedules after 60 days with no repo activity: **Actions → Update data → Enable workflow** |
| Page didn't change after an edit | Wait for the run triggered by your commit, then hard-refresh (Ctrl+Shift+R / Cmd+Shift+R) |

## Changing the area or detail
Edit `BBOX` (map area) and `STEP` (grid spacing) near the top of `fetch_data.py`.
A smaller `STEP` (e.g. 0.05) gives more detail but ~4× larger files and slower loading.

## Data sources
- NOAA National Blend of Models, CONUS core (NOAA Open Data on AWS: `noaa-nbm-grib2-pds`)
- NIFC WFIGS Current Interagency Fire Perimeters
- NIFC National GACC Boundaries
- Ventilation categories: New Mexico Smoke Management Program
