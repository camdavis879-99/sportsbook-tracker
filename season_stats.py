#!/usr/bin/env python3
"""Fetch season-to-date team stats for all 32 NHL teams and write season_stats.json.

Usage: python3 season_stats.py [season_stats.json] [YYYY-MM-DD]
Defaults: season_stats.json in the current directory, today's date in
Phoenix (America/Phoenix is UTC-7 year-round, so a fixed offset is safe).

Sources (all public, keyless, stdlib only):
- https://api-web.nhle.com/v1/standings/now — record, points, points %,
  goals for/against, goal differential, regulation wins, shootout
  record, streak, and full home/road/last-10 splits for all 32 teams.
- https://api.nhle.com/stats/rest/en/team/summary — power play %,
  penalty kill % (and net variants), shots for/against per game,
  faceoff win %, shutouts.
- https://moneypuck.com/.../teams.csv — expected goals for/against
  per game ("all" situations rows only), same source enrich.py uses.
- https://api-web.nhle.com/v1/club-stats/{ABBR}/now — per-team skaters
  and goalies, used only for a small league-leaders block (points,
  goals, shots per game, save %, GAA leaders).

Derived stats (computed from the fetched fields, never guessed):
- Sh% (team shooting %) = goals for / total shots for.
- Sv% (team save %) = (total shots against - goals against) / total
  shots against.

Never fails the build: every fetch is guarded, missing fields render
as null (the page shows a blank, never a guessed number), and the
script always exits 0. If standings fail entirely, the file still
writes with an empty teams list and render.py omits the Season Stats
tab.
"""
import csv
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone

NHL = "https://api-web.nhle.com/v1"
PHOENIX = timezone(timedelta(hours=-7))


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception as e:
        print(f"season_stats: fetch failed {url}: {type(e).__name__}", file=sys.stderr)
        return None


def norm(name):
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def season_id(date_s):
    """NHL season id for a date: Oct-Dec belongs to the season starting
    that year, Jan-Jun to the season started the previous year."""
    try:
        y, m, _d = (int(x) for x in date_s.split("-"))
    except (TypeError, ValueError, AttributeError):
        return None, None
    if m >= 10:
        return y * 10000 + (y + 1), y
    return (y - 1) * 10000 + y, y - 1


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _rec(w, l, otl):
    if w is None or l is None or otl is None:
        return None
    return f"{int(w)}-{int(l)}-{int(otl)}"


def _splits(prefix, row):
    """Home/road/L10 split dict from a standings row (prefix home/road/l10)."""
    gp = _num(row.get(f"{prefix}GamesPlayed"))
    gf = _num(row.get(f"{prefix}GoalsFor"))
    ga = _num(row.get(f"{prefix}GoalsAgainst"))
    return {
        "gp": gp,
        "w": _num(row.get(f"{prefix}Wins")),
        "l": _num(row.get(f"{prefix}Losses")),
        "otl": _num(row.get(f"{prefix}OtLosses")),
        "pts": _num(row.get(f"{prefix}Points")),
        "gf": gf,
        "ga": ga,
        "diff": _num(row.get(f"{prefix}GoalDifferential")),
        "record": _rec(row.get(f"{prefix}Wins"), row.get(f"{prefix}Losses"),
                       row.get(f"{prefix}OtLosses")),
        "gf_gp": round(gf / gp, 2) if gp and gf is not None else None,
        "ga_gp": round(ga / gp, 2) if gp and ga is not None else None,
    }


def team_xg(mp_year):
    """{abbr: (xgf_per_game, xga_per_game)} from MoneyPuck's public
    team CSV, "all" situations rows only. Guarded: {} on any failure."""
    out = {}
    if not mp_year:
        return out
    req = urllib.request.Request(
        f"https://moneypuck.com/moneypuck/playerData/seasonSummary/{mp_year}/regular/teams.csv",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"season_stats: moneypuck fetch failed: {type(e).__name__}", file=sys.stderr)
        return out
    try:
        for row in csv.DictReader(io.StringIO(text)):
            if row.get("situation") != "all":
                continue
            abbr = (row.get("team") or "").strip()
            try:
                gp = float(row.get("games_played") or 0)
                xgf = float(row.get("xGoalsFor") or 0)
                xga = float(row.get("xGoalsAgainst") or 0)
            except (TypeError, ValueError):
                continue
            if abbr and gp:
                out[abbr] = (round(xgf / gp, 2), round(xga / gp, 2))
    except Exception as e:
        print(f"season_stats: moneypuck parse failed: {type(e).__name__}", file=sys.stderr)
        return out
    return out


def team_summary(nhl_season):
    """{abbr: summary dict} from the keyless NHL stats API team summary.
    Rows carry teamFullName but no abbreviation, so they are matched
    through the standings names. A failed fetch yields {}."""
    out = {}
    if not nhl_season:
        return out
    data = fetch_json(
        "https://api.nhle.com/stats/rest/en/team/summary"
        f"?cayenneExp=seasonId={nhl_season}"
    )
    if not data:
        return out
    return data.get("data", []) or []


def club_leaders(abbrs):
    """League-wide individual leaderboards from club-stats.

    Fetches each team's skaters/goalies once and returns ranked lists:
    top 6 skaters by points, goals, and shots per game (gp >= 5), plus
    one starting goalie per team (most games played, gp >= 5) with
    save % and GAA, so the page can rank all 32 starters. Any team
    that fails to fetch is skipped; returns None when nothing usable
    was fetched.
    """
    skaters = []
    starter_by_team = {}
    fetched = 0
    for abbr in abbrs:
        data = fetch_json(f"{NHL}/club-stats/{abbr}/now", timeout=15)
        if not data:
            continue
        fetched += 1
        for s in data.get("skaters", []):
            fn = (s.get("firstName") or {}).get("default", "")
            ln = (s.get("lastName") or {}).get("default", "")
            name = f"{fn} {ln}".strip()
            gp = _num(s.get("gamesPlayed"))
            pts = _num(s.get("points"))
            goals = _num(s.get("goals"))
            shots = _num(s.get("shots"))
            if not name or gp is None or gp <= 0:
                continue
            skaters.append({
                "name": name, "team": abbr, "gp": gp,
                "pts": pts, "goals": goals,
                "sog_gp": round(shots / gp, 2) if shots is not None else None,
                "detail": f"{goals or 0}G {s.get('assists') or 0}A",
            })
        for gl in data.get("goalies", []):
            fn = (gl.get("firstName") or {}).get("default", "")
            ln = (gl.get("lastName") or {}).get("default", "")
            name = f"{fn} {ln}".strip()
            gp = _num(gl.get("gamesPlayed"))
            sv = _num(gl.get("savePercentage"))
            gaa = _num(gl.get("goalsAgainstAverage"))
            if not name or gp is None or gp < 5 or sv is None or gaa is None:
                continue
            cur = starter_by_team.get(abbr)
            if cur is None or gp > cur["gp"]:
                starter_by_team[abbr] = {
                    "name": name, "team": abbr, "gp": gp, "sv": sv, "gaa": gaa,
                }
    if fetched == 0:
        return None

    def top6(rows, key):
        vals = [r for r in rows if r.get(key) is not None]
        vals.sort(key=lambda r: -r[key])
        return vals[:6]

    points_top = top6(skaters, "pts")
    goals_top = top6(skaters, "goals")
    sog_top = top6([s for s in skaters if s["gp"] >= 5], "sog_gp")
    goalies = sorted(starter_by_team.values(), key=lambda g: -g["gp"])
    best_sv = max(goalies, key=lambda g: g["sv"]) if goalies else None
    best_gaa = min(goalies, key=lambda g: g["gaa"]) if goalies else None
    return {
        "points": ({"name": points_top[0]["name"], "team": points_top[0]["team"],
                    "value": points_top[0]["pts"], "detail": points_top[0]["detail"]}
                   if points_top else None),
        "goals": ({"name": goals_top[0]["name"], "team": goals_top[0]["team"],
                   "value": goals_top[0]["goals"], "detail": None}
                  if goals_top else None),
        "sog": ({"name": sog_top[0]["name"], "team": sog_top[0]["team"],
                 "value": sog_top[0]["sog_gp"], "detail": None}
                if sog_top else None),
        "sv": ({"name": best_sv["name"], "team": best_sv["team"],
                "value": best_sv["sv"], "detail": None} if best_sv else None),
        "gaa": ({"name": best_gaa["name"], "team": best_gaa["team"],
                 "value": best_gaa["gaa"], "detail": None} if best_gaa else None),
        "points_top": [{"name": s["name"], "team": s["team"],
                        "value": s["pts"], "detail": s["detail"]} for s in points_top],
        "goals_top": [{"name": s["name"], "team": s["team"],
                       "value": s["goals"], "detail": None} for s in goals_top],
        "sog_top": [{"name": s["name"], "team": s["team"],
                     "value": s["sog_gp"], "detail": None} for s in sog_top],
        "goalies": goalies,
        "teams_fetched": fetched,
    }


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "season_stats.json"
    if len(sys.argv) > 2:
        date_s = sys.argv[2]
    else:
        date_s = datetime.now(PHOENIX).date().isoformat()
    nhl_season, mp_year = season_id(date_s)

    standings = fetch_json(f"{NHL}/standings/now")
    rows = (standings or {}).get("standings", []) if standings else []
    summary_rows = team_summary(nhl_season)

    # Match team-summary rows to abbreviations via normalized full names.
    name_to_abbr = {}
    for row in rows:
        team_name = (row.get("teamName") or {}).get("default", "")
        abbr = (row.get("teamAbbrev") or {}).get("default", "")
        if team_name and abbr:
            name_to_abbr[norm(team_name)] = abbr
    summary = {}
    for row in summary_rows:
        abbr = name_to_abbr.get(norm(row.get("teamFullName", "")))
        if not abbr:
            continue
        summary[abbr] = row

    xg = team_xg(mp_year)

    teams = []
    for row in rows:
        abbr = (row.get("teamAbbrev") or {}).get("default", "")
        if not abbr:
            continue
        gp = _num(row.get("gamesPlayed"))
        gf = _num(row.get("goalFor"))
        ga = _num(row.get("goalAgainst"))
        s = summary.get(abbr, {})
        sf_gp = _num(s.get("shotsForPerGame"))
        sa_gp = _num(s.get("shotsAgainstPerGame"))
        sf_total = sf_gp * gp if sf_gp is not None and gp else None
        sa_total = sa_gp * gp if sa_gp is not None and gp else None
        sh_pct = round(gf / sf_total, 3) if gf is not None and sf_total else None
        sv_pct = round((sa_total - ga) / sa_total, 3) if ga is not None and sa_total else None
        streak_code = row.get("streakCode")
        streak_count = _num(row.get("streakCount"))
        streak = None
        if streak_code and streak_count is not None:
            streak = f"{streak_code}{int(streak_count)}"
        x = xg.get(abbr)
        teams.append({
            "abbr": abbr,
            "team": (row.get("teamName") or {}).get("default", abbr),
            "division": row.get("divisionName", ""),
            "conference": row.get("conferenceName", ""),
            "rank": _num(row.get("leagueSequence")),
            "gp": gp,
            "w": _num(row.get("wins")),
            "l": _num(row.get("losses")),
            "otl": _num(row.get("otLosses")),
            "record": _rec(row.get("wins"), row.get("losses"), row.get("otLosses")),
            "pts": _num(row.get("points")),
            "pts_pct": _num(row.get("pointPctg")),
            "win_pct": _num(row.get("winPctg")),
            "gf": gf,
            "ga": ga,
            "diff": _num(row.get("goalDifferential")),
            "gf_gp": _num(row.get("goalsForPctg")),
            "ga_gp": round(ga / gp, 2) if ga is not None and gp else None,
            "reg_wins": _num(row.get("regulationWins")),
            "reg_ot_wins": _num(row.get("regulationPlusOtWins")),
            "so_w": _num(row.get("shootoutWins")),
            "so_l": _num(row.get("shootoutLosses")),
            "streak": streak,
            "home": _splits("home", row),
            "road": _splits("road", row),
            "l10": _splits("l10", row),
            "pp": _num(s.get("powerPlayPct")),
            "pk": _num(s.get("penaltyKillPct")),
            "pp_net": _num(s.get("powerPlayNetPct")),
            "pk_net": _num(s.get("penaltyKillNetPct")),
            "sf_gp": sf_gp,
            "sa_gp": sa_gp,
            "faceoff": _num(s.get("faceoffWinPct")),
            "shutouts": _num(s.get("teamShutouts")),
            "sh_pct": sh_pct,
            "sv_pct": sv_pct,
            "xgf_gp": x[0] if x else None,
            "xga_gp": x[1] if x else None,
        })
    teams.sort(key=lambda t: (t["rank"] is None, t["rank"] or 0))

    leaders = None
    if teams:
        try:
            leaders = club_leaders([t["abbr"] for t in teams])
        except Exception as e:
            print(f"season_stats: club leaders failed: {e}", file=sys.stderr)
            leaders = None

    out = {
        "as_of": date_s,
        "season_id": nhl_season,
        "teams": teams,
        "leaders": leaders,
    }
    tmp = dst + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, dst)
    print(f"Wrote {dst} with {len(teams)} teams"
          f" (leaders: {'yes' if leaders else 'no'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
