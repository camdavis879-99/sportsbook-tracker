#!/usr/bin/env python3
"""NHL daily games report for Cam.

Prints a compact, chat-ready report for today's NHL slate:
  - each team's season goals-for and goals-against per game
  - expected starting goalies, with a BACKUP flag when the starter is not
    the team's usual No. 1 (leader by games started, gap of 3+ starts)
  - star injuries: injured players who are a top-5 scorer on their team
    or the team's No. 1 goalie, with status

Sources (all free, no API key):
  - api-web.nhle.com: schedule, standings, club stats
  - actionnetwork.com/nhl/injury-report: daily injury table
    (Markerzone list as fallback), matched to stars
  - dailyfaceoff.com/starting-goalies: projected/confirmed starters,
    parsed from the page's embedded data

Nothing is guessed: anything unavailable is labeled as such.
"""

import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PHX = ZoneInfo("America/Phoenix")
UA = {"User-Agent": "Mozilla/5.0 (nhl-daily-report)"}
NHL = "https://api-web.nhle.com/v1"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
DFO = "https://www.dailyfaceoff.com/starting-goalies/"
DEBUG = os.environ.get("NHL_DEBUG")
LAST_ERROR = ""


def dbg(*args):
    if DEBUG:
        print("[debug]", *args, file=sys.stderr)


def fetch_json(url):
    global LAST_ERROR
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        LAST_ERROR = f"{type(e).__name__}: {e}"
        return None


def fetch_text(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def norm(name):
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", name.lower()).strip()


def same_player(a, b):
    """Match two player names: full-name equality, or last name + first initial."""
    na, nb = norm(a), norm(b)
    if not na or not nb or na == nb:
        return na == nb
    pa, pb = na.split(), nb.split()
    return pa[-1] == pb[-1] and pa[0][0] == pb[0][0]


def fmt_time(utc_str):
    try:
        dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt.replace(tzinfo=timezone.utc).astimezone(PHX)
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return utc_str


def main():
    today = datetime.now(PHX).date()
    today_s = today.isoformat()

    sched = fetch_json(f"{NHL}/schedule/{today_s}")
    games = []
    if sched:
        for day in sched.get("gameWeek", []):
            for g in day.get("games", []):
                t = g.get("startTimeUTC", "")
                try:
                    dt = datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
                    dt = dt.replace(tzinfo=timezone.utc).astimezone(PHX)
                except Exception:
                    continue
                if dt.date() == today:
                    games.append((dt, g))
    games.sort(key=lambda x: x[0])
    if not games:
        print("No NHL games today.")
        return 0

    # Debug probe: does Action Network's odds page embed book-level odds?
    if DEBUG:
        _odds_html = fetch_text("https://www.actionnetwork.com/nhl/odds")
        dbg("an odds html len:", len(_odds_html))
        dbg("an odds __NEXT_DATA__:", "__NEXT_DATA__" in _odds_html)
        dbg("an odds fanduel count:", _odds_html.lower().count("fanduel"))
        _idx = _odds_html.lower().find("fanduel")
        if _idx >= 0:
            dbg("an odds fanduel ctx:", _odds_html[max(0, _idx - 200):_idx + 300].replace("\n", " "))
        _m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', _odds_html, re.S)
        if _m:
            try:
                _jd = json.loads(_m.group(1))
                dbg("an odds next_data keys:", list(_jd.keys()))
                _props = _jd.get("props", {}).get("pageProps", {})
                dbg("an odds pageProps keys:", list(_props.keys())[:40])
                dbg("an odds blob len:", len(_m.group(1)), "fanduel in blob:", _m.group(1).lower().count("fanduel"))
                _ot = _props.get("oddsTool", {})
                dbg("an odds oddsTool type:", type(_ot).__name__,
                    "keys:", list(_ot.keys())[:40] if isinstance(_ot, dict) else f"len={len(_ot)}")
                # Find the first list inside oddsTool and show one item's keys.
                def _walk(_o, _depth=0):
                    if _depth > 4:
                        return
                    if isinstance(_o, dict):
                        for _k, _v in _o.items():
                            if isinstance(_v, list) and _v and isinstance(_v[0], dict):
                                dbg("an odds list at key:", _k, "item keys:", list(_v[0].keys())[:30])
                                return
                        for _v in _o.values():
                            _walk(_v, _depth + 1)
                    elif isinstance(_o, list):
                        for _v in _o:
                            _walk(_v, _depth + 1)
                _walk(_ot)
            except Exception as _e:
                dbg("an odds next_data parse failed:", type(_e).__name__, str(_e)[:200])
    averages = {}
    league = []
    standings = fetch_json(f"{NHL}/standings/now")
    if standings:
        for row in standings.get("standings", []):
            abbr = (row.get("teamAbbrev") or {}).get("default", "")
            gp = row.get("gamesPlayed") or 0
            if abbr and gp:
                gf = row.get("goalFor", row.get("goalsFor", 0))
                ga = row.get("goalAgainst", row.get("goalsAgainst", 0))
                averages[abbr] = (gf / gp, ga / gp)
                gf = gf or 0
                ga = ga or 0
                team_name = (row.get("teamName") or {}).get("default", "") or abbr
                league.append({
                    "team": team_name,
                    "abbr": abbr,
                    "gp": gp,
                    "avg_total": round((gf + ga) / gp, 2),
                    "plusminus": gf - ga,
                })
    league.sort(key=lambda t: t["avg_total"], reverse=True)

    # League ranks: GF rank 1 = most goals scored per game; GA rank 1 =
    # fewest goals allowed per game (stingiest defense).
    def ordinal(n):
        if 10 <= n % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    gf_rank = {}
    for i, abbr in enumerate(sorted(averages, key=lambda a: averages[a][0], reverse=True), 1):
        gf_rank[abbr] = ordinal(i)
    ga_rank = {}
    for i, abbr in enumerate(sorted(averages, key=lambda a: averages[a][1]), 1):
        ga_rank[abbr] = ordinal(i)

    # Per-team club stats: top-5 scorers, goalie starts, usual No. 1.
    teams = []
    for _, g in games:
        for side in ("awayTeam", "homeTeam"):
            abbr = g.get(side, {}).get("abbrev", "")
            if abbr and abbr not in teams:
                teams.append(abbr)

    club = {}
    for abbr in teams:
        data = fetch_json(f"{NHL}/club-stats/{abbr}/now")
        skaters = sorted(
            data.get("skaters", []) if data else [],
            key=lambda s: s.get("points", 0),
            reverse=True,
        )
        top5 = []
        for s in skaters[:5]:
            fn = (s.get("firstName") or {}).get("default", "")
            ln = (s.get("lastName") or {}).get("default", "")
            top5.append(f"{fn} {ln}".strip())
        # Last season's top-5 too: a star who missed games this year
        # (or is only now returning) still counts as a star.
        last = fetch_json(f"{NHL}/club-stats/{abbr}/20252026")
        last_skaters = sorted(
            last.get("skaters", []) if last else [],
            key=lambda s: s.get("points", 0),
            reverse=True,
        )
        last5 = []
        for s in last_skaters[:5]:
            fn = (s.get("firstName") or {}).get("default", "")
            ln = (s.get("lastName") or {}).get("default", "")
            last5.append(f"{fn} {ln}".strip())
        goalies = []
        for gl in (data.get("goalies", []) if data else []):
            fn = (gl.get("firstName") or {}).get("default", "")
            ln = (gl.get("lastName") or {}).get("default", "")
            starts = gl.get("gamesStarted", gl.get("gamesPlayed", 0))
            goalies.append({"name": f"{fn} {ln}".strip(), "starts": starts})
        goalies.sort(key=lambda x: x["starts"], reverse=True)
        club[abbr] = {
            "top5": top5,
            "last5": last5,
            "goalies": goalies,
            "no1": goalies[0]["name"] if goalies else "",
        }

    # Injuries, filtered to stars (top-5 scorers this season or last, plus
    # the No. 1 goalie). Primary source: Action Network's daily injury
    # table, complete by team. Fallback: Markerzone's list, which is
    # partial (its fetched page omits known star injuries), so it can only
    # add true positives, never prove a clean bill of health. Nothing is
    # guessed: if neither source yields entries, the report says the
    # injury report is unavailable, and it never claims "none" off a
    # partial source.
    import html as _html

    def _norm(s):
        return re.sub(r"[^a-z0-9]+", "", s.lower())

    # Team-name variants from today's games, for mapping table headers.
    name_to_abbr_pre = {}
    common_to_abbr = {}
    for _, _g in games:
        for _side in ("awayTeam", "homeTeam"):
            _t = _g.get(_side, {})
            _abbr = _t.get("abbrev", "")
            _place = (_t.get("placeName") or {}).get("default", "")
            _common = (_t.get("commonName") or {}).get("default", "")
            if not _abbr:
                continue
            for _variant in (_common, f"{_place} {_common}".strip(), _place):
                if _variant:
                    name_to_abbr_pre[_norm(_variant)] = _abbr
            if _common:
                common_to_abbr[_norm(_common)] = _abbr

    AN_URL = "https://www.actionnetwork.com/nhl/injury-report"
    an_html = fetch_text(AN_URL)
    dbg("actionnetwork html len:", len(an_html))
    if DEBUG:
        for _nm in ("Levi", "Gibson", "Malkin", "Tavares", "Ekholm"):
            dbg("actionnetwork contains", _nm, ":", _nm in an_html)
    an = {}
    if an_html:
        cur = None
        for _row in re.findall(r"<tr[^>]*>(.*?)</tr>", an_html, re.S):
            _cells = []
            for _cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", _row, re.S):
                _t = re.sub(r"<[^>]+>", "", _cell)
                _t = _html.unescape(_t)
                _t = re.sub(r"\s+", " ", _t).strip()
                _cells.append(_t)
            if not _cells:
                continue
            if len(_cells) >= 2 and all(not _c for _c in _cells[1:]) and _cells[0]:
                cur = (name_to_abbr_pre.get(_norm(_cells[0]))
                       or common_to_abbr.get(_norm(_cells[0])))
                continue
            if cur and len(_cells) >= 5:
                _name, _status, _short = _cells[0], _cells[2], _cells[4]
                _sm = re.match(r"^.*? is (?:out|day-to-day|out for season) with (.+)$", _short)
                _inj = _sm.group(1) if _sm else ""
                _text = f"{_status}, {_inj}" if _inj else _status
                an.setdefault(cur, []).append((_name, _text, ""))
        dbg("actionnetwork entries:", sum(len(v) for v in an.values()), "teams:", len(an))
        if DEBUG:
            for _abbr in sorted(an):
                dbg("an", _abbr, [n for n, _, _ in an[_abbr]])

    MZ_URL = "https://www.markerzone.com/hockey/stats/nhl/injuries.php"
    MZ_ALIAS = {"MON": "MTL", "LV": "VGK", "WAS": "WSH", "LA": "LAK",
                "SJ": "SJS", "TB": "TBL", "CLB": "CBJ"}
    NHL_ABBRS = {"ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL",
                 "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD",
                 "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
                 "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"}
    mz_html = fetch_text(MZ_URL)
    dbg("markerzone html len:", len(mz_html))
    mz = {}
    if mz_html:
        import html as _html
        txt = re.sub(r"<script.*?</script>", "", mz_html, flags=re.S)
        txt = re.sub(r"<style.*?</style>", "", txt, flags=re.S)
        txt = re.sub(r"<[^>]+>", "\n", txt)
        flat_lines = []
        for raw in txt.splitlines():
            line = _html.unescape(raw).strip()
            line = re.sub(r"\s+", " ", line)
            if line:
                flat_lines.append(line)
        flat = "\n".join(flat_lines)
        tokens = sorted(set(MZ_ALIAS) | NHL_ABBRS, key=len, reverse=True)
        pat = re.compile(r"\b(" + "|".join(tokens) + r")\b")
        parts = pat.split(flat)
        name_re = re.compile(r"^[A-Z][A-Za-z'’\-.]+(\s+[A-Z][A-Za-z'’\-.]+)+$")
        detail_re = re.compile(r"^(.*?)\s*-\s*Expected to be out until at least\s*(.+)$")
        for j in range(1, len(parts), 2):
            abbr = MZ_ALIAS.get(parts[j], parts[j])
            seg = parts[j + 1] if j + 1 < len(parts) else ""
            seg_lines = []
            for raw_line in seg.split("\n"):
                for part in raw_line.split(","):
                    part = part.strip()
                    if part:
                        seg_lines.append(part)
            for k, line in enumerate(seg_lines):
                dm = detail_re.match(line)
                if dm and k > 0:
                    name = seg_lines[k - 1]
                    if name_re.match(name):
                        mz.setdefault(abbr, []).append(
                            (name, f"{dm.group(1)} (out until at least {dm.group(2)})", ""))
        dbg("markerzone entries:", sum(len(v) for v in mz.values()), "teams:", len(mz))
        if DEBUG:
            for _abbr in sorted(mz):
                dbg("mz", _abbr, [n for n, _, _ in mz[_abbr]])

    src = an if an else mz
    dbg("injury source:", "actionnetwork" if an else "markerzone")
    source_ok = sum(len(v) for v in src.values()) > 0

    injuries = {}
    for abbr in teams:
        found = []
        stars = (club[abbr]["top5"] + club[abbr]["last5"]
                 + ([club[abbr]["no1"]] if club[abbr]["no1"] else []))
        for name, status, detail in src.get(abbr, []):
            if any(same_player(name, s) for s in stars):
                found.append((name, status, detail))
        injuries[abbr] = found

    # Starting goalies from DailyFaceoff's embedded page data.
    # The page embeds per-game goalie news (e.g. "Jarry will start vs.
    # Toronto") with a Confirmed/Projected strength flag. Walk the data
    # and match each entry to a team playing today and a roster goalie.
    starters = {}
    html = fetch_text(DFO)
    blob = ""
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if m:
        blob = m.group(1)
    dbg("dfo html len:", len(html), "| next_data found:", bool(m), "| blob len:", len(blob))

    # Map team name variants from today's games to abbreviations.
    name_to_abbr = {}
    for _, g in games:
        for side in ("awayTeam", "homeTeam"):
            t = g.get(side, {})
            abbr = t.get("abbrev", "")
            place = (t.get("placeName") or {}).get("default", "")
            common = (t.get("commonName") or {}).get("default", "")
            if not abbr:
                continue
            for variant in (common, f"{place} {common}".strip(), place):
                if variant:
                    name_to_abbr[norm(variant)] = abbr

    def walk(obj):
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from walk(v)

    if blob:
        try:
            dfo_data = json.loads(blob)
        except Exception:
            dfo_data = None
        if dfo_data:
            cands = 0
            for d in walk(dfo_data):
                keys = list(d.keys())
                news_keys = [k for k in keys if "news" in k.lower()
                             and ("strengthname" in k.lower() or "details" in k.lower())]
                if not news_keys:
                    continue
                cands += 1
                prefixes = set()
                for k in news_keys:
                    lk = k.lower()
                    prefixes.add(lk[: lk.find("news")])
                for prefix in prefixes:
                    def pval(substr, need_space=False):
                        for k, v in d.items():
                            lk = k.lower()
                            if lk.startswith(prefix) and substr in lk and isinstance(v, str):
                                if need_space and " " not in v.strip():
                                    continue
                                return v.strip()
                        return ""
                    strength = pval("strengthname").lower()
                    details = pval("newsdetails")
                    abbr_v = pval("abbrev")
                    abbr = abbr_v if abbr_v in teams else ""
                    if not abbr:
                        team_v = pval("teamname") or pval("team")
                        abbr = name_to_abbr.get(norm(team_v), "")
                    if not abbr:
                        continue
                    gname = pval("goaliename") or pval("goalie", need_space=True)
                    if not gname and details:
                        mm = re.match(r"\s*([A-Za-z'’-]+)\s+will start", details)
                        if mm:
                            last = mm.group(1)
                            for gl in club[abbr]["goalies"]:
                                if norm(gl["name"]).split()[-1] == norm(last):
                                    gname = gl["name"]
                                    break
                    if not gname:
                        continue
                    if not any(same_player(gname, gl["name"]) for gl in club[abbr]["goalies"]):
                        continue
                    label = strength if strength in ("confirmed", "projected", "likely", "expected") else "reported"
                    rank = {"confirmed": 0, "projected": 1, "likely": 1,
                            "expected": 1, "reported": 2}[label]
                    cur = starters.get(abbr)
                    if cur is None or rank < cur[0]:
                        starters[abbr] = (rank, gname, label)
            dbg("dfo candidate dicts:", cands, "| starters resolved:", len(starters))
            starters = {a: (n, l) for a, (r, n, l) in starters.items()}

    def goalie_line(abbr):
        no1 = club[abbr]["no1"]
        no1_starts = club[abbr]["goalies"][0]["starts"] if club[abbr]["goalies"] else 0
        if abbr in starters:
            name, label = starters[abbr]
            starts = next(
                (g["starts"] for g in club[abbr]["goalies"] if same_player(g["name"], name)),
                None,
            )
            flag = ""
            if starts is not None and no1 and not same_player(name, no1):
                if no1_starts - starts >= 3:
                    flag = ", BACKUP"
            return f"{name} ({label}{flag})"
        if no1:
            return f"unconfirmed (usual: {no1})"
        return "unconfirmed"

    def goalie_injury_note(abbr):
        """Return (no1, status) when a team's No. 1 goalie is confirmed
        out, else None. Name-matched across all injury entries (not the
        team key) because the injury table's team attribution is
        unreliable. Only 'Out' counts as confirmed; 'Day to day' does
        not. The backup starter's name is deliberately left out: the
        DailyFaceoff starter feed misattributes goalies to the wrong
        team (e.g. Ersson to Ottawa, Tarasov to Detroit), so naming a
        backup would repeat that error.
        """
        no1 = club.get(abbr, {}).get("no1", "")
        if not no1:
            return None
        for entries in injuries.values():
            for name, status, detail in entries:
                if same_player(name, no1):
                    if "day to day" in status.lower():
                        return None
                    if "out" in status.lower():
                        return (no1, status)
                    return None
        return None

    # Back-to-back starter detection (NHL data only, no DailyFaceoff).
    # A team that played yesterday almost never starts the same goalie
    # today, so naming yesterday's starter (from the boxscore's starter
    # flag, falling back to the goalie with the most time on ice) and
    # the likely starter today (the roster goalie with the most starts
    # who did not start yesterday) is the best early signal available,
    # since confirmed starters are not announced until morning skate,
    # hours after the 5 AM build.
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    y_sched = fetch_json(f"{NHL}/schedule/{yesterday}")
    y_games = {}
    if y_sched:
        # The date lives on the gameWeek entry, not on each game.
        for wd in y_sched.get("gameWeek", []):
            if wd.get("date") != yesterday:
                continue
            for yg in wd.get("games", []):
                gid = yg.get("id")
                if not gid:
                    continue
                for side in ("awayTeam", "homeTeam"):
                    ab = yg.get(side, {}).get("abbrev", "")
                    if ab:
                        y_games[ab] = gid

    def _starter_from_boxscore(game_id, abbr):
        """The starter's abbreviated name (e.g. 'A. Schmid') for `abbr`
        in a completed game, from the boxscore's starter flag, or None
        when it cannot be determined. Falls back to the goalie with the
        most time on ice when no starter flag is set."""
        bs = fetch_json(f"{NHL}/gamecenter/{game_id}/boxscore")
        if not bs:
            return None
        pbg = bs.get("playerByGameStats", {})
        best = None
        best_secs = 0
        for side in ("awayTeam", "homeTeam"):
            if (bs.get(side, {}) or {}).get("abbrev", "") != abbr:
                continue
            for gl in (pbg.get(side, {}) or {}).get("goalies", []):
                name = (gl.get("name") or {}).get("default", "").strip()
                if not name:
                    continue
                if gl.get("starter"):
                    return name
                toi = gl.get("toi", "00:00") or "00:00"
                try:
                    m, s = str(toi).split(":")
                    secs = int(m) * 60 + int(s)
                except (ValueError, AttributeError):
                    secs = 0
                if secs > best_secs:
                    best_secs = secs
                    best = name
        return best if best_secs > 0 else None

    def back_to_back_note(abbr, full):
        """'Back-to-back — X started yesterday, Y likely starts today.'
        for a team that played yesterday, else None. The boxscore gives
        an abbreviated name, so it is resolved to the roster full name
        before display."""
        gid = y_games.get(abbr)
        if not gid:
            return None
        y_short = _starter_from_boxscore(gid, abbr)
        if not y_short:
            return None
        roster = club.get(abbr, {}).get("goalies", [])
        y_starter = next(
            (g["name"] for g in roster if same_player(g["name"], y_short)),
            y_short,
        )
        cands = [g for g in roster if not same_player(g["name"], y_starter)]
        likely = cands[0]["name"] if cands else ""
        if likely:
            return (f"{full} are on a back-to-back — {y_starter} started "
                    f"yesterday, {likely} likely starts today.")
        return f"{full} are on a back-to-back — {y_starter} started yesterday."

    # FanDuel spreads + totals from Action Network's per-game articles.
    # FanDuel publishes no public API; the articles carry FanDuel tables.
    # Slugs vary ("...-odds-{date}" vs "...-odds-for-{date}"), so try both;
    # a game with no resolvable article simply gets no odds line.
    import html as _html
    date_slug = f"{today.strftime('%A').lower()}-{today.strftime('%B').lower()}-{today.day}"
    odds = {}
    def _sgn(p):
        return p if p.startswith(("+", "-")) else f"+{p}"
    # ParlayAPI first: FanDuel spreads + totals for the whole slate in one call.
    # Falls back to Action Network articles per game if this fails or a game
    # is missing. In Muse the credential lives in the custom.parlay-api
    # connection and the surrogate header is swapped for the real key by the
    # runtime. Outside Muse, set PARLAY_API_KEY and it is sent as an
    # X-API-Key header instead.
    try:
        _parlay_key = os.environ.get("PARLAY_API_KEY")
        if not _parlay_key:
            sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
            from dynamic_credentials import add_surrogate_to_request, read_json_response
        _today_pairs = {
            (_g2.get("awayTeam", {}).get("abbrev"), _g2.get("homeTeam", {}).get("abbrev"))
            for _dt2, _g2 in games
        }
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                raise RuntimeError("redirect refused, verify the endpoint")
        _req = urllib.request.Request(
            "https://parlay-api.com/v1/sports/icehockey_nhl/odds"
            "?regions=us&markets=spreads,totals&bookmakers=fanduel")
        if _parlay_key:
            _req.add_header("X-API-Key", _parlay_key)
            with urllib.request.build_opener(_NoRedirect()).open(_req, timeout=30) as _resp:
                _events = json.load(_resp)
        else:
            add_surrogate_to_request(_req, "custom.parlay-api", allowed_hosts=["parlay-api.com"])
            with urllib.request.build_opener(_NoRedirect()).open(_req, timeout=30) as _resp:
                _events = read_json_response(_resp)
        if isinstance(_events, dict):
            _events = _events.get("data", [])
        for _ev in (_events or []):
            _a = name_to_abbr_pre.get(_norm(_ev.get("away_team", "")))
            _h = name_to_abbr_pre.get(_norm(_ev.get("home_team", "")))
            if not _a or not _h or (_a, _h) not in _today_pairs:
                continue
            _books = _ev.get("bookmakers", []) or []
            _fd = next((b for b in _books if b.get("key") == "fanduel"), None)
            if not _fd:
                continue
            _mk = {m.get("key"): m for m in (_fd.get("markets", []) or [])}
            _sp = _mk.get("spreads")
            _to = _mk.get("totals")
            # Totals alone are usable: the spread market can be absent for
            # the whole slate (2026-11-07: API returned totals only), and
            # total leans still work with the spread blank. A missing spread
            # is stored as "" so the chart/bets layers keep string types;
            # spread candidates safely no-op on it (normalize_price("") is None).
            if not _to:
                continue
            _to_out = {o.get("name"): o for o in (_to.get("outcomes", []) or [])}
            if "Over" not in _to_out or "Under" not in _to_out:
                continue
            _tot = f"{float(_to_out['Over'].get('point')):g}"
            _sp_out = {}
            if _sp:
                _sp_out = {name_to_abbr_pre.get(_norm(o.get("name", ""))): o for o in (_sp.get("outcomes", []) or [])}
                if _a not in _sp_out or _h not in _sp_out:
                    _sp_out = {}
            for _abbr3 in (_a, _h):
                _o = _sp_out.get(_abbr3)
                odds[_abbr3] = {
                    "spread": f"{float(_o.get('point')):+g}" if _o else "",
                    "spread_price": _sgn(str(_o.get("price"))) if _o else "",
                    "total": _tot,
                    "over": _sgn(str(_to_out["Over"].get("price"))),
                    "under": _sgn(str(_to_out["Under"].get("price"))),
                }
        dbg("parlay odds resolved teams:", sorted(odds.keys()))
    except Exception as _e:
        dbg("parlay odds failed:", type(_e).__name__, str(_e)[:200])
    # FanDuel moneylines, in a SEPARATE call from the spreads/totals
    # above, so a failure here can never cost us the spreads. ParlayAPI
    # mirrors The Odds API market keys, where "h2h" is the moneyline
    # market; if that key is wrong or the market is absent, this block
    # just leaves ML unset and the Model edge shows "no line". ML is
    # only recorded for a team that already has a spread entry, so a
    # partial moneyline can never make the article fallback below skip
    # a game.
    try:
        _ml_key = os.environ.get("PARLAY_API_KEY")
        if not _ml_key:
            sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
            from dynamic_credentials import add_surrogate_to_request, read_json_response
        _ml_req = urllib.request.Request(
            "https://parlay-api.com/v1/sports/icehockey_nhl/odds"
            "?regions=us&markets=h2h&bookmakers=fanduel")
        if _ml_key:
            _ml_req.add_header("X-API-Key", _ml_key)
            with urllib.request.build_opener(_NoRedirect()).open(_ml_req, timeout=30) as _resp:
                _ml_events = json.load(_resp)
        else:
            add_surrogate_to_request(_ml_req, "custom.parlay-api", allowed_hosts=["parlay-api.com"])
            with urllib.request.build_opener(_NoRedirect()).open(_ml_req, timeout=30) as _resp:
                _ml_events = read_json_response(_resp)
        if isinstance(_ml_events, dict):
            _ml_events = _ml_events.get("data", [])
        for _ev in (_ml_events or []):
            _a = name_to_abbr_pre.get(_norm(_ev.get("away_team", "")))
            _h = name_to_abbr_pre.get(_norm(_ev.get("home_team", "")))
            if not _a or not _h or (_a, _h) not in _today_pairs:
                continue
            _books = _ev.get("bookmakers", []) or []
            _fd = next((b for b in _books if b.get("key") == "fanduel"), None)
            if not _fd:
                continue
            _mk = {m.get("key"): m for m in (_fd.get("markets", []) or [])}
            _h2h = _mk.get("h2h")
            if not _h2h:
                continue
            _ml_out = {name_to_abbr_pre.get(_norm(o.get("name", ""))): o
                       for o in (_h2h.get("outcomes", []) or [])}
            if _a not in _ml_out or _h not in _ml_out:
                continue
            for _abbr3 in (_a, _h):
                if _abbr3 in odds:
                    odds[_abbr3]["ml"] = _sgn(str(_ml_out[_abbr3].get("price")))
        dbg("parlay ml resolved teams:", sorted(a for a in odds if "ml" in odds[a]))
    except Exception as _e:
        dbg("parlay ml failed:", type(_e).__name__, str(_e)[:200])
    for _dt, _g in games:
        if _g.get("awayTeam", {}).get("abbrev") in odds and _g.get("homeTeam", {}).get("abbrev") in odds:
            continue
        def _slug_of(_t):
            _place = (_t.get("placeName") or {}).get("default", "")
            _common = (_t.get("commonName") or {}).get("default", "")
            return re.sub(r"[^a-z0-9]+", "-", f"{_place} {_common}".lower()).strip("-")
        _a_slug = _slug_of(_g.get("awayTeam", {}))
        _h_slug = _slug_of(_g.get("homeTeam", {}))
        _doc = ""
        for _variant in (f"{_a_slug}-vs-{_h_slug}-prediction-picks-nhl-odds-for-{date_slug}",
                         f"{_a_slug}-vs-{_h_slug}-prediction-picks-nhl-odds-{date_slug}"):
            _try = fetch_text(f"https://www.actionnetwork.com/nhl/{_variant}")
            if _try and ("Spread" in _try or "puck line:" in _try):
                _doc = _try
                break
            if _try and not _doc:
                _doc = _try
        if not _doc:
            continue
        # Bullet-format articles: "Team A vs. Team B puck line: ..." lines.
        _txt = re.sub(r"<[^>]+>", "\n", _doc)
        _txt = _html.unescape(_txt)
        _txt = _txt.replace("\\n", "\n")
        _pl = None
        _ou = None
        for _line in _txt.splitlines():
            _line = re.sub(r"\s+", " ", _line).strip()
            if not _pl:
                _pm = re.search(r"puck line:\s*(.+)", _line)
                if _pm:
                    _pl = _pm.group(1)
            if not _ou:
                _om = re.search(r"over/under:\s*(\d+\.5)\s*\(([+-]?\d+)o\s*/\s*([+-]?\d+)u\)", _line)
                if _om:
                    _ou = _om.groups()
        if _pl and _ou:
            for _part in _pl.split(","):
                _bm = re.match(r"^\s*(.+?)\s+([+-]\d+\.5)\s*\(([+-]?\d+)\)\s*$", _part)
                if _bm:
                    _abbr2 = name_to_abbr_pre.get(_norm(_bm.group(1)))
                    if _abbr2 and _abbr2 not in odds:
                        odds[_abbr2] = {"spread": _bm.group(2), "spread_price": _sgn(_bm.group(3)),
                                        "total": _ou[0], "over": _sgn(_ou[1]), "under": _sgn(_ou[2])}
        dbg("odds article", _a_slug, "vs", _h_slug, "pl:", (_pl or "NONE")[:110], "ou:", _ou)
        for _tbl in re.findall(r"<table.*?</table>", _doc, re.S):
            _rows = []
            for _row in re.findall(r"<tr.*?</tr>", _tbl, re.S):
                _cells = []
                for _c in re.findall(r"<t[dh].*?</t[dh]>", _row, re.S):
                    _t = re.sub(r"<[^>]+>", " ", _c)
                    _t = _html.unescape(_t)
                    _t = re.sub(r"\s+", " ", _t).strip()
                    _cells.append(_t)
                _rows.append(_cells)
            if not _rows or not _rows[0]:
                continue
            _team = _rows[0][0].replace(" Odds", "").strip()
            _abbr = common_to_abbr.get(_norm(_team))
            if not _abbr or _abbr in odds:
                continue
            for _i, _r in enumerate(_rows):
                if any("Spread" in _c for _c in _r) and any("Total" in _c for _c in _r):
                    if _i + 1 < len(_rows) and len(_rows[_i + 1]) >= 2:
                        _sp, _tot = _rows[_i + 1][0], _rows[_i + 1][1]
                        _sm = re.match(r"^([+-]?\d+\.5)\s+([+-]?\d+)$", _sp)
                        _tm = re.match(r"^(\d+\.5)\s+([+-]?\d+)o\s*/\s*([+-]?\d+)u$", _tot)
                        if _sm and _tm:
                            odds[_abbr] = {
                                "spread": _sm.group(1), "spread_price": _sgn(_sm.group(2)),
                                "total": _tm.group(1), "over": _sgn(_tm.group(2)), "under": _sgn(_tm.group(3)),
                            }
                    break
    dbg("odds teams resolved:", sorted(odds.keys()))
    if DEBUG:
        _fd = fetch_text("https://sportsbook.fanduel.com/navigation/nhl")
        dbg("fanduel direct len:", len(_fd), "head:", _fd[:200].replace("\n", " "))

    date_label = today.strftime("%A, %B %-d")
    short_label = today.strftime("%a, %b %-d")
    lines = [f"NHL — {short_label} · {len(games)} games",
             "https://camdavis879-99.github.io/sportsbook-tracker/"]
    chart_games = []
    for dt, g in games:
        away = g.get("awayTeam", {})
        home = g.get("homeTeam", {})
        a_abbr, h_abbr = away.get("abbrev", ""), home.get("abbrev", "")
        a_name = away.get("commonName", {}).get("default", a_abbr)
        h_name = home.get("commonName", {}).get("default", h_abbr)
        a_place = (away.get("placeName") or {}).get("default", "")
        h_place = (home.get("placeName") or {}).get("default", "")
        a_full = f"{a_place} {a_name}".strip() if a_place else a_name
        h_full = f"{h_place} {h_name}".strip() if h_place else h_name
        state = g.get("gameState", "FUT")
        state_txt = "" if state == "FUT" else (" (Final)" if state == "FINAL" else f" ({state})")
        lines.append(f"{a_full} at {h_full} — {fmt_time(g.get('startTimeUTC', ''))}{state_txt}")
        a_avg = averages.get(a_abbr)
        h_avg = averages.get(h_abbr)
        a_odd, h_odd = odds.get(a_abbr), odds.get(h_abbr)
        star_lines = []
        for abbr, side in ((a_abbr, a_abbr), (h_abbr, h_abbr)):
            for name, status, detail in injuries.get(abbr, []):
                extra = f" ({detail})" if detail else ""
                star_lines.append(f"{side}: {name} — {status}{extra}")
        notes = []
        for abbr, full in ((a_abbr, a_full), (h_abbr, h_full)):
            hit = goalie_injury_note(abbr)
            if hit:
                no1, status = hit
                notes.append(f"{full} No. 1 goalie {no1} is out ({status}).")
            b2b = back_to_back_note(abbr, full)
            if b2b:
                notes.append(b2b)
        for n in notes:
            lines.append(f"  Note: {n}")
        chart_games.append({
            "away_abbr": a_abbr, "home_abbr": h_abbr,
            "away_name": a_name, "home_name": h_name,
            "time": fmt_time(g.get("startTimeUTC", "")),
            "away_gf": round(a_avg[0], 2) if a_avg else None,
            "away_ga": round(a_avg[1], 2) if a_avg else None,
            "home_gf": round(h_avg[0], 2) if h_avg else None,
            "home_ga": round(h_avg[1], 2) if h_avg else None,
            "away_gf_rank": gf_rank.get(a_abbr, ""),
            "away_ga_rank": ga_rank.get(a_abbr, ""),
            "home_gf_rank": gf_rank.get(h_abbr, ""),
            "home_ga_rank": ga_rank.get(h_abbr, ""),
            "away_goalie": goalie_line(a_abbr),
            "home_goalie": goalie_line(h_abbr),
            "stars_out": star_lines,
            "goalie_note": "; ".join(notes),
            "odds": {
                "away_spread": a_odd["spread"], "away_spread_price": a_odd["spread_price"],
                "home_spread": h_odd["spread"], "home_spread_price": h_odd["spread_price"],
                "total": a_odd["total"], "over": a_odd["over"], "under": a_odd["under"],
                # Moneylines only as a pair: the edge needs both teams
                # to remove the vig, so a lone ML is dropped, not shown.
                "away_ml": a_odd.get("ml") if (a_odd.get("ml") and h_odd.get("ml")) else None,
                "home_ml": h_odd.get("ml") if (a_odd.get("ml") and h_odd.get("ml")) else None,
            } if (a_odd and h_odd) else None,
        })
    with open(os.path.expanduser(os.environ.get("NHL_JSON_PATH", "~/workspace/nhl-daily/today.json")), "w") as f:
        json.dump({"date": date_label, "games": chart_games, "league": league}, f, indent=2)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
