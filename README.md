# Scholarship Tracker

A dashboard for tracking master's scholarships and the seminars that go with them.
It focuses on full-ride scholarships for master's study in Scandinavia.

## What's in this folder

| File | What it is |
| --- | --- |
| `index.html` | The dashboard. Open it in any browser. |
| `data/scholarships.json` | All scholarship entries, in the schema format. |
| `data/seminars.json` | The seminar list as of 1 October 2026. |
| `scraper/seminar_scraper.py` | The script that collects seminar details from websites. |
| `scraper/scraper_sources.json` | The pages the scraper checks. |
| `scraper/requirements.txt` | The Python packages the scraper needs. |
| `schema/scholarship_schema.json` | The original tracker specification. |
| `.github/workflows/scrape-seminars.yml` | Optional. Runs the scraper every week on GitHub. |

## Using the dashboard

Double-click `index.html`. It opens in your browser. No install is needed.

Your changes are saved in that browser. They stay when you close and reopen the file.
They do not move to another browser or computer by themselves.

To back up your data, click **Export JSON**. To load data, click **Import JSON**.
Import accepts scholarship files and seminar files.

The online version of the dashboard on claude.ai saves changes into the page itself.
The local file and the online page do not sync. Use Export and Import to move data between them.

## Using the scraper

You need Python 3.9 or newer.

1. Open a terminal in the `scraper` folder.
2. Install the packages once:
   `pip install -r requirements.txt`
3. Run the scraper:
   `python seminar_scraper.py --sources scraper_sources.json --out seminars.json`
4. In the dashboard, open the **Seminars** tab.
5. Click **Import scraped seminars** and choose `seminars.json`.

Seminars already on your list are skipped.

To check more pages, add scholarships in the dashboard first.
Then click **Download scraper sources** and use that file instead.
You can also add any event page to `scraper_sources.json` by hand.

Useful options:

- `--verbose` shows how many events each page gave.
- `--days 90` keeps only events in the next 90 days.
- `--include-past` keeps events that already happened.

### How the scraper finds events

It tries three methods, from most to least reliable.

1. Event data that the website publishes in a standard format.
2. Calendar files (`.ics`) linked from the page.
3. Text on the page that mentions an event word and a date.

Events found from page text get a note that says to check them.
Always check the date and time on the event page before you register.

### Pages it cannot read

Some sites block automated visitors. si.se (the Swedish Institute) is one of them.
These pages appear in the `errors` list inside `seminars.json`. Check them by hand.

The scraper respects each site's `robots.txt` rules. It waits two seconds between sites.

### Running it every week (optional)

Put this folder in a GitHub repository.
The workflow in `.github/workflows` runs the scraper every Monday.
It saves the result as `data/seminars_scraped.json`. Import that file into the dashboard.

## About the data

Each entry follows the schema in `schema/scholarship_schema.json`.
Unverified values are left empty and show as "Unspecified".
Each entry's notes say what is confirmed and what is projected.

Some 2027 dates are projected from the 2026 cycle. Confirm them on the official pages.

The Erasmus Mundus entries are full rides only if you win the Erasmus Mundus scholarship.
Self-funded places are not full rides.

The BI-Luiss entry covers full tuition only. It is not a full ride.
Use the **Full ride only** filter to hide it.

The dashboard adds two things the schema doesn't have:

- `_progress.docs_ready` records which documents you've marked as ready.
- A separate seminar list, with title, date, time, organizer, link and related scholarships.
