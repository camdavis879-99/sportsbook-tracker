# NHL + NFL Matchup Chart (auto-refreshing)

A daily matchup chart that rebuilds itself every morning and
publishes to GitHub Pages. NHL cards, one per game in puck-drop
order: offense vs. defense bar pairs with league ranks, starting
goalies with BACKUP flags and season save % + record, stars-out
lines, FanDuel spreads and totals (lines only, no prices), last-10
form, home/away scoring splits, and a SOG watch line per game (each
team's top two shooters by shots on goal per game, plus the shots per
game the opposing team allows). Below the game cards, a League totals
table lists all 32 teams with average combined goals per game and goal
differential, season to date, sortable by clicking the avg-total or +/-
header (default: avg total, highest first). Below that, the current NFL
week with records and odds, plus a short section on which bet types
pay sportsbooks the most.

Team names are always full names on every card, never city-only:
several cities have both NHL and NFL teams (Dallas, Pittsburgh,
Detroit, Chicago, Nashville, Miami, Minnesota), so "Dallas" alone
would be ambiguous.

## How it updates

The `daily` workflow runs every day at 12:00 UTC (5:00 AM Phoenix
time, before the day's games). It runs `nhl_daily.py` to fetch that
day's slate, `enrich.py` to add full team names, last-10 form,
home/away splits, and goalie season stats, `nfl.py` to fetch the
current NFL week from ESPN, then renders `index.html` with
`render.py`, and deploys it to GitHub Pages. The published link
then shows the new slate with no manual step. Scheduled GitHub runs
can lag a little, and the workflow can also be run by hand from the
Actions tab. The enrich and NFL steps never fail the build: if a
fetch fails, the page renders with whatever data is available.

## Files

- `nhl_daily.py`: fetches the day's games, team scoring averages and
  ranks, starting goalies, star injuries, and FanDuel lines, then
  prints the text report and writes `today.json`. Python standard
  library only. This is a copy of the script used for the daily
  chat report; keep the two copies in sync when either changes.
  This copy is intentionally NOT modified by the analytics work;
  enrichment lives in `enrich.py` so the core script stays in sync.
- `enrich.py`: adds full team names, last-10 form, home/away
  scoring splits, and starting-goalie season save % + record to
  `today.json`, from public api-web.nhle.com endpoints. Stdlib
  only. Never fails the build.
- `nfl.py`: fetches the current NFL week (schedule, records, odds)
  from ESPN's public scoreboard API and writes `nfl.json`. Stdlib
  only. NFL odds are ESPN's listed provider (currently DraftKings),
  NOT FanDuel, and the page labels them as such. If ESPN posts no
  odds for a game, the page says so instead of guessing. No NCAA
  content.
- `render.py`: turns `today.json` (plus `nfl.json` when present)
  into a self-contained `index.html`.
- `.github/workflows/daily.yml`: the schedule, build, and deploy.

## One-time setup

1. Create a GitHub repository and upload these files to it, keeping
   the `.github/workflows/daily.yml` path exactly.
2. In the repository, go to Settings, then Secrets and variables,
   then Actions, and add a repository secret named
   `PARLAY_API_KEY` holding your ParlayAPI key. The key is only
   ever used by the workflow run; it never appears in the page.
   Without it, the script falls back to Action Network articles
   for the games they cover, so some games would show no lines.
3. In Settings, then Pages, set Source to GitHub Actions.
4. Open the Actions tab, run the `daily` workflow once by hand,
   and open the Pages link it reports. Share that link, and pin
   it to a phone home screen from Safari with Share, then Add to
   Home Screen.

## Notes

- The page is public to anyone with the link, same as any
  published page. That is the point: it is meant to be shared.
- Lines move during the day. The page shows the morning lines
  from the scheduled run, not live moves.
- If a morning looks wrong, re-run the workflow from the
  Actions tab and check its log.
