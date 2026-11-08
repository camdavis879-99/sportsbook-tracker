#!/usr/bin/env python3
"""Render today.json (from nhl_daily.py) into a self-contained index.html.

Usage: python3 render.py [today.json] [dist/index.html] [nfl.json] [season_stats.json]
Defaults: today.json in the current directory, dist/index.html, and
nfl.json / season_stats.json in the current directory when present
(NFL section omitted when nfl.json is absent; Season Stats tab omitted
when season_stats.json is absent). NHL FanDuel odds show lines only, never prices.
The Model block shows vig-free implied probabilities and edges derived
from moneylines, never the raw moneyline prices themselves.
A Picks section appears only when a picks.json dated today is present:
it names suggested bets (never bankroll or stakes, and never bets
placed) with their morning prices and model probabilities. Picks for
any other date are ignored, so a stale file can never render.
The SOG watch block shows each team's top two shooters by shots on goal
per game and the shots per game the opposing team allows.
The League totals section lists all 32 teams with games played, average
combined goals per game, and goal differential, season to date, sorted by
avg total descending and sortable by clicking a column header.
A Season Stats tab (second page tab, Daily is the default) shows all 32
teams across four switchable views — Overview, Offense, Defense & Goalies,
Special Teams — with sortable columns, a team search box, percentile
shading on the stat columns, a leaders strip, and a glossary. It reads
season_stats.json (built by season_stats.py) when present; when the file
is absent the tab is omitted and the page is the daily view only.
"""
import html
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


def fmt(v):
    return f"{float(v):.1f}" if v is not None else "—"


def _rank_num(rank_str):
    """Numeric league rank from an ordinal string like '5th' (1 = best)."""
    m = re.match(r"(\d+)", str(rank_str or ""))
    return int(m.group(1)) if m else 16


def _percentile(rank_str):
    """Percentile (0-100) from a league rank: rank 1 = 100, rank 32 = 0.

    Rank 1 is best for both metrics (most goals scored for offense,
    fewest allowed for defense), so a longer bar is always better and
    the 50% tick is league average.
    """
    rank = _rank_num(rank_str)
    return max(0.0, min(100.0, (32 - rank) / 31.0 * 100.0))


def pct_bar(label, value, rank, kind):
    """One percentile bar: full-width bordered track, 50% average tick.

    The track is a visible bordered box, so 100% (the right edge) is
    obvious, and the tick in the middle is league average. Length is
    the team's percentile, so past the tick is above average (good).
    `kind` is 'off' (amber) or 'def' (blue): color names the metric,
    length names how good it is.
    """
    pct = _percentile(rank)
    return (
        f'<div class="pct-row"><span class="pct-label">{html.escape(label)}</span>'
        f'<div class="pct-track"><div class="pct-fill {kind}" style="width:{pct:.0f}%"></div>'
        f'<div class="pct-tick"></div></div>'
        f'<span class="pct-value">{fmt(value)} &middot; {html.escape(str(rank))}</span></div>'
    )


def goalie(g):
    badge = ""
    if "BACKUP" in g:
        badge = ' <span class="backup">BACKUP</span>'
        g = g.replace(", BACKUP", "")
    return f"{html.escape(g)}{badge}"


def goalie_stat(g, stats):
    base = goalie(g)
    if stats:
        return f"{base} <span class=\"gstat\">{html.escape(stats)}</span>"
    return base


def card(g):
    a, h = g["away_abbr"], g["home_abbr"]
    parts = ["<section class=\"card\">"]
    parts.append(
        f'<div class="matchup"><h2>{html.escape(g["away_name"])} at {html.escape(g["home_name"])}</h2>'
        f'<span class="time">{html.escape(g["time"])}</span></div>'
    )
    parts.append('<div class="pair-label">Away offense vs home defense</div>')
    parts.append(pct_bar(f"{a} offense", g["away_gf"], g["away_gf_rank"], "off"))
    parts.append(pct_bar(f"{h} defense", g["home_ga"], g["home_ga_rank"], "def"))
    parts.append('<div class="pair-label">Home offense vs away defense</div>')
    parts.append(pct_bar(f"{h} offense", g["home_gf"], g["home_gf_rank"], "off"))
    parts.append(pct_bar(f"{a} defense", g["away_ga"], g["away_ga_rank"], "def"))
    note = g.get("goalie_note") or ""
    if note:
        parts.append(f'<div class="goalie-note">Note: {html.escape(note)}</div>')
    analytics = []
    if g.get("away_last10"):
        gp = g.get("away_last10_gp") or 10
        line = f'{g["away_name"]} — Last {gp}: {g["away_last10"]}'
        if g.get("away_away_gf") is not None:
            line += f' · Away: {g["away_away_gf"]:.1f} GF / {g["away_away_ga"]:.1f} GA'
        analytics.append(line)
    if g.get("home_last10"):
        gp = g.get("home_last10_gp") or 10
        line = f'{g["home_name"]} — Last {gp}: {g["home_last10"]}'
        if g.get("home_home_gf") is not None:
            line += f' · Home: {g["home_home_gf"]:.1f} GF / {g["home_home_ga"]:.1f} GA'
        analytics.append(line)
    if analytics:
        items = "".join(f"<div>{html.escape(s)}</div>" for s in analytics)
        parts.append(f'<div class="analytics">{items}</div>')
    o = g.get("odds")
    if o:
        parts.append(
            f'<div class="odds">FanDuel: {html.escape(a)} {html.escape(o["away_spread"])} &middot; '
            f'{html.escape(h)} {html.escape(o["home_spread"])} &middot; '
            f'O/U {html.escape(o["total"])}</div>'
        )
    extra = []
    mb = model_block(g)
    if mb:
        extra.append(mb)
    sb = sog_block(g)
    if sb:
        extra.append(sb)
    if extra:
        parts.append('<details class="card-details"><summary>More details</summary>')
        parts.extend(extra)
        parts.append("</details>")
    parts.append("</section>")
    return "\n".join(parts)


def model_block(g):
    """Render the Model block for one NHL game card, or "" when absent.

    Every metric shows "n/a" when its source failed, never a guess.
    Market edge shows "no line" when no FanDuel moneyline pair was
    available. Full team names throughout, never city-only.
    """
    m = g.get("model")
    if not m:
        return ""

    def num(v, fmt):
        return fmt.format(v) if v is not None else "n/a"

    def team_line(name, xgf, xga, pp, pk, rest, edge):
        edge_txt = f"{edge:+.1f} pts" if edge is not None else "n/a"
        return (
            f"{html.escape(name)} — xGF {num(xgf, '{:.2f}')} / xGA {num(xga, '{:.2f}')}"
            f" · PP {num(pp, '{:.1%}')} · PK {num(pk, '{:.1%}')}"
            f" · {html.escape(rest) if rest else 'n/a'}"
            f" · Goalie edge {edge_txt}"
        )

    lines = ['<div class="model"><div class="model-title">Model</div>']
    lines.append(
        f"<div>{team_line(g['away_name'], m['away_xgf'], m['away_xga'], m['away_pp'], m['away_pk'], m['away_rest'], m['away_goalie_edge'])}</div>"
    )
    lines.append(
        f"<div>{team_line(g['home_name'], m['home_xgf'], m['home_xga'], m['home_pp'], m['home_pk'], m['home_rest'], m['home_goalie_edge'])}</div>"
    )
    if m.get("away_prob") is not None and m.get("home_prob") is not None:
        win = (
            f"Win: {html.escape(g['away_name'])} {m['away_prob']:.1%}"
            f" ({num(m.get('away_lam'), '{:.1f}')} exp. goals)"
            f" · {html.escape(g['home_name'])} {m['home_prob']:.1%}"
            f" ({num(m.get('home_lam'), '{:.1f}')} exp. goals)"
        )
        if m.get("away_market") is not None and m.get("away_edge") is not None:
            win += (
                f" · Market: {html.escape(g['away_name'])} {m['away_market']:.1%}"
                f" (edge {m['away_edge']:+.1f} pts)"
                f" · {html.escape(g['home_name'])} {m['home_market']:.1%}"
                f" (edge {m['home_edge']:+.1f} pts)"
            )
        else:
            win += " · Market edge: no line"
        lines.append(f"<div>{win}</div>")
    else:
        lines.append("<div>Model win % unavailable (missing goals data)</div>")
    lines.append("</div>")
    return "\n".join(lines)


def sog_block(g):
    """Render the SOG watch block for one NHL game card, or "" when absent.

    Each side lists the team's top two shooters by shots on goal per
    game, plus the shots per game the opposing team allows. A side
    with no shooter data shows "n/a", never a guess. Full team names
    throughout, never city-only.
    """
    away = g.get("away_sog") or []
    home = g.get("home_sog") or []
    if not away and not home:
        return ""

    def side_line(name, shooters, opp_sa):
        if shooters:
            bits = " · ".join(
                f"{html.escape(s['name'])} {s['sog_gp']:.1f}/GP" for s in shooters
            )
        else:
            bits = "n/a"
        sa = f" (opp allows {opp_sa:.1f} SA/GP)" if opp_sa is not None else ""
        return f"{html.escape(name)}: {bits}{sa}"

    lines = ['<div class="sog"><div class="sog-title">SOG watch</div>']
    lines.append(f"<div>{side_line(g['away_name'], away, g.get('away_opp_sa_gp'))}</div>")
    lines.append(f"<div>{side_line(g['home_name'], home, g.get('home_opp_sa_gp'))}</div>")
    lines.append("</div>")
    return "\n".join(lines)


def nfl_card(g):
    parts = ["<section class=\"card nfl-card\">"]
    parts.append(
        f'<div class="matchup"><h2>{html.escape(g["away_name"])} at {html.escape(g["home_name"])}</h2>'
        f'<span class="time">{html.escape(g.get("time", ""))}</span></div>'
    )
    recs = []
    if g.get("away_record"):
        recs.append(f'{html.escape(g["away_name"])} {html.escape(g["away_record"])}')
    if g.get("home_record"):
        recs.append(f'{html.escape(g["home_name"])} {html.escape(g["home_record"])}')
    if recs:
        parts.append(f'<div class="nfl-records">{" · ".join(recs)}</div>')
    if g.get("state") == "post" and g.get("away_score") is not None:
        parts.append(
            f'<div class="nfl-score">Final: {html.escape(str(g["away_score"]))}–{html.escape(str(g["home_score"]))}</div>'
        )
    o = g.get("odds")
    if o:
        bits = []
        if o.get("details"):
            bits.append(html.escape(o["details"]))
        if o.get("total") is not None:
            bits.append(f'O/U {html.escape(str(o["total"]))}')
        if o.get("away_ml") and o.get("home_ml"):
            bits.append(
                f'ML: {html.escape(g["away_abbr"])} {html.escape(o["away_ml"])} / '
                f'{html.escape(g["home_abbr"])} {html.escape(o["home_ml"])}'
            )
        provider = html.escape(o.get("provider", "ESPN"))
        parts.append(f'<div class="odds">Odds via ESPN ({provider}): {" · ".join(bits)}</div>')
    else:
        parts.append('<div class="odds-note">Odds not posted yet</div>')
    parts.append("</section>")
    return "\n".join(parts)


def nfl_section(nfl):
    if not nfl or not nfl.get("games"):
        return ""
    cards = "\n".join(nfl_card(g) for g in nfl["games"])
    week = f'Week {nfl["week"]}' if nfl.get("week") else "This week"
    bye = ""
    if nfl.get("bye"):
        bye = f'<p class="nfl-bye">On bye: {html.escape(", ".join(nfl["bye"]))}</p>'
    return (
        f'<h2 class="section-title">NFL — {html.escape(str(week))}</h2>'
        '<p class="section-note">Full team names throughout. Odds come from ESPN\'s listed '
        "provider, not FanDuel, and are the morning snapshot, not live moves.</p>"
        f"{bye}"
        f'<div class="grid">\n{cards}\n</div>'
    )


def fmt_odds(v):
    v = int(v)
    return f"+{v}" if v > 0 else str(v)


def picks_section():
    """Render tonight's picks from picks.json, or "" when absent or stale.

    picks.json lives beside this script (repo root in the workflow) and
    carries a Phoenix date. Only a file dated today renders, so
    yesterday's picks can never appear on today's page. The section
    names suggestions only: no bankroll, no stakes, nothing placed.
    """
    picks = None
    for path in ("picks.json", os.path.join(os.path.dirname(__file__), "picks.json")):
        try:
            with open(path) as f:
                picks = json.load(f)
            break
        except Exception:
            picks = None
    if not picks:
        return ""
    try:
        today = datetime.now(ZoneInfo("America/Phoenix")).date().isoformat()
    except Exception:
        return ""
    if picks.get("date") != today:
        return ""
    straights = picks.get("straights") or []
    p2 = picks.get("parlay2") or {}
    p3 = picks.get("parlay3") or {}
    if not straights and not p2.get("legs") and not p3.get("legs"):
        return ""
    parts = [
        '<h2 class="section-title">Tonight\'s picks</h2>',
        '<p class="section-note">Suggestions identified from the model and the morning lines, not bets placed. Lines move during the day; check the book before staking. No bankroll or stakes are shown here.</p>',
        '<div class="picks">',
    ]
    for s in straights:
        prob = f" · model {s['model_prob']:.0%}" if s.get("model_prob") is not None else ""
        parts.append(
            f'<div class="pick"><span class="pick-kind">Straight</span> '
            f'<span class="pick-label">{html.escape(s["label"])} {fmt_odds(s["odds"])}</span>{prob} '
            f'<span class="pick-reason">{html.escape(s.get("reason", ""))}</span></div>'
        )
    for kind, p in (("2-leg parlay", p2), ("3-leg parlay", p3)):
        legs = p.get("legs") or []
        if not legs:
            continue
        leg_txt = " + ".join(
            f'{html.escape(l["label"])} {fmt_odds(l["odds"])}' for l in legs
        )
        prob = f" · model {p['model_prob']:.0%}" if p.get("model_prob") is not None else ""
        parts.append(
            f'<div class="pick"><span class="pick-kind">{kind}</span> '
            f'<span class="pick-label">{leg_txt} = {fmt_odds(p["odds"])}</span>{prob} '
            f'<span class="pick-reason">{html.escape(p.get("reason", ""))}</span></div>'
        )
    if picks.get("as_of"):
        parts.append(f'<div class="pick-asof">{html.escape(picks["as_of"])}</div>')
    parts.append("</div>")
    return "\n".join(parts)


def league_section(data):
    """Render the League totals table from today.json's league array.

    One row per team, season to date: games played, average combined
    goals per game (goals for + goals against, divided by games played),
    and goal differential. Rows arrive sorted by avg total descending;
    the avg-total and +/- headers are clickable to re-sort. Returns ""
    when no league data is present, so an older today.json still renders.
    """
    league = data.get("league") or []
    if not league:
        return ""
    rows = []
    for t in league:
        rows.append(
            f'<tr><td>{html.escape(t["team"])}</td>'
            f'<td data-v="{t["gp"]}">{t["gp"]}</td>'
            f'<td data-v="{t["avg_total"]:.2f}">{t["avg_total"]:.2f}</td>'
            f'<td data-v="{t["plusminus"]}">{t["plusminus"]:+d}</td></tr>'
        )
    return (
        '<h2 class="section-title">League totals</h2>'
        '<p class="section-note">Season to date, all 32 teams. Avg total is the average combined goals '
        'per game in that team\'s games (goals for + goals against, divided by games played) — '
        'the number that matters for totals. +/- is goals for minus goals against. '
        'Click a column header to sort.</p>'
        '<div class="league-wrap"><table class="league-table sortable" id="league-table">'
        '<thead><tr><th data-sort="text">Team</th><th data-sort="num">GP</th>'
        '<th data-sort="num" class="sorted-desc">Avg total goals/game</th>'
        '<th data-sort="num">+/-</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _pct(v):
    return f"{v:.1%}" if v is not None else "—"


def _f2(v):
    return f"{v:.2f}" if v is not None else "—"


def _int(v):
    return str(int(v)) if v is not None else "—"


def _signed(v):
    return f"{int(v):+d}" if v is not None else "—"


def _txt(v):
    return html.escape(str(v)) if v is not None else "—"


def _sv(v):
    return f".{round(v * 1000):03d}" if v is not None else "—"


# Season Stats views. Each column: (key, label, kind, format fn,
# lower-is-better, percentile-shaded). kind "num" sorts numerically on
# the raw value in data-v; "text" sorts on the cell text.
SEASON_VIEWS = [
    ("overview", "Overview", "rank", False, [
        ("rank", "Rk", "num", _int, False, False),
        ("team", "Team", "text", _txt, False, False),
        ("gp", "GP", "num", _int, False, False),
        ("record", "Record", "text", _txt, False, False),
        ("pts", "Pts", "num", _int, False, True),
        ("pts_pct", "Pts%", "num", _pct, False, True),
        ("gf_gp", "GF/GP", "num", _f2, False, True),
        ("ga_gp", "GA/GP", "num", _f2, True, True),
        ("diff", "+/-", "num", _signed, False, True),
        ("streak", "Streak", "text", _txt, False, False),
        ("l10_record", "L10", "text", _txt, False, False),
        ("home_record", "Home", "text", _txt, False, False),
        ("road_record", "Away", "text", _txt, False, False),
    ]),
    ("offense", "Offense", "gf_gp", True, [
        ("team", "Team", "text", _txt, False, False),
        ("gp", "GP", "num", _int, False, False),
        ("gf_gp", "GF/GP", "num", _f2, False, True),
        ("sf_gp", "SF/GP", "num", _f2, False, True),
        ("sh_pct", "Sh%", "num", _pct, False, True),
        ("xgf_gp", "xGF/GP", "num", _f2, False, True),
        ("faceoff", "FO%", "num", _pct, False, True),
    ]),
    ("defense", "Defense & Goalies", "ga_gp", False, [
        ("team", "Team", "text", _txt, False, False),
        ("gp", "GP", "num", _int, False, False),
        ("ga_gp", "GA/GP", "num", _f2, True, True),
        ("sa_gp", "SA/GP", "num", _f2, True, True),
        ("sv_pct", "Sv%", "num", _sv, False, True),
        ("xga_gp", "xGA/GP", "num", _f2, True, True),
        ("shutouts", "SO", "num", _int, False, True),
    ]),
    ("special", "Special Teams", "pp", True, [
        ("team", "Team", "text", _txt, False, False),
        ("gp", "GP", "num", _int, False, False),
        ("pp", "PP%", "num", _pct, False, True),
        ("pk", "PK%", "num", _pct, False, True),
        ("pp_net", "PP net%", "num", _pct, False, True),
        ("pk_net", "PK net%", "num", _pct, False, True),
    ]),
]


def _tiers(teams, key, lower_better):
    """Map abbr -> percentile tier class for one column.

    Top fifth t5, next fifth t4, middle fifth unshaded, next fifth t2,
    bottom fifth t1. lower_better inverts the direction (fewest goals
    against is the good end). Needs >= 8 non-null values, else {}.
    """
    vals = [(t["abbr"], t.get(key)) for t in teams if t.get(key) is not None]
    if len(vals) < 8:
        return {}
    vals.sort(key=lambda x: x[1])
    n = len(vals)
    out = {}
    for i, (abbr, _v) in enumerate(vals):
        frac = i / (n - 1) if n > 1 else 0.5
        good = 1.0 - frac if lower_better else frac
        if good >= 0.8:
            out[abbr] = "t5"
        elif good >= 0.6:
            out[abbr] = "t4"
        elif good <= 0.2:
            out[abbr] = "t1"
        elif good <= 0.4:
            out[abbr] = "t2"
    return out


def _season_row(t):
    """Flatten one team's nested splits into the keys the views use."""
    r = dict(t)
    r["l10_record"] = (t.get("l10") or {}).get("record")
    r["home_record"] = (t.get("home") or {}).get("record")
    r["road_record"] = (t.get("road") or {}).get("record")
    return r


def _season_table(view_id, label, default_key, default_desc, columns, teams):
    """One switchable Season Stats table. Returns (html, is_default)."""
    rows = [_season_row(t) for t in teams]
    tiers = {}
    for key, _lbl, _kind, _fn, lower, shade in columns:
        if shade:
            tiers[key] = _tiers(rows, key, lower)

    def sort_val(t):
        v = t.get(default_key)
        if v is None:
            return (1, 0)
        if isinstance(v, (int, float)):
            return (0, -v if default_desc else v)
        return (0, v)

    rows.sort(key=sort_val)
    ths = []
    for key, lbl, kind, _fn, _lower, _shade in columns:
        classes = []
        if kind == "num":
            classes.append("num")
        if key == default_key:
            classes.append("sorted-desc" if default_desc else "sorted-asc")
        cls = f' class="{" ".join(classes)}"' if classes else ""
        ths.append(f'<th data-sort="{kind}"{cls}>{html.escape(lbl)}</th>')
    trs = []
    for t in rows:
        cells = []
        for key, _lbl, kind, fn, _lower, _shade in columns:
            # Season tab shows team abbreviations (CBJ, DET, TOR) to keep
            # rows short on mobile; the full name stays in data-team so
            # the search box still matches either form.
            v = (t.get("abbr") or t.get("team")) if key == "team" else t.get(key)
            disp = fn(v)
            if kind == "num":
                dv = f' data-v="{v}"' if isinstance(v, (int, float)) else ""
                tier = tiers.get(key, {}).get(t["abbr"], "") if _shade else ""
                cls = f' class="num {tier}"' if tier else ' class="num"'
                cells.append(f"<td{dv}{cls}>{disp}</td>")
            else:
                cells.append(f"<td>{disp}</td>")
        trs.append(
            f'<tr data-team="{html.escape((t["team"] + " " + (t.get("abbr") or "")).lower().strip())}">{"".join(cells)}</tr>'
        )
    return (
        f'<div class="season-view" data-view-panel="{view_id}"'
        f'{" hidden" if view_id != "overview" else ""}>'
        f'<div class="stats-wrap"><table class="stats-table sortable">'
        f'<thead><tr>{"".join(ths)}</tr></thead>'
        f'<tbody>{"".join(trs)}</tbody></table></div></div>'
    )


def _leader_list(items, fmt, start=1):
    """<ol> of (label, value) with rank numbers, starting at `start`."""
    lis = []
    for i, (label, v) in enumerate(items):
        lis.append(
            f'<li><span class="lr">{start + i}</span> '
            f'<span class="lname">{html.escape(label)}</span> '
            f'<b>{html.escape(fmt(v))}</b></li>'
        )
    start_attr = f' start="{start}"' if start != 1 else ""
    return f'<ol class="leader-list"{start_attr}>{"".join(lis)}</ol>'


def _leader_cat(title, items, fmt, show_bottom=True):
    """One leaderboard category. items is a list of (label, value)
    sorted best-first. Shows ranks 1-6, then the bottom 6 (ranks
    27-32 when there are 32) when show_bottom and there are at
    least 12 items."""
    parts = [f'<div class="leader-cat"><h4>{html.escape(title)}</h4>']
    parts.append(_leader_list(items[:6], fmt, start=1))
    if show_bottom and len(items) >= 12:
        parts.append('<div class="leader-bottom">Bottom 6</div>')
        parts.append(_leader_list(items[-6:], fmt, start=len(items) - 5))
    parts.append("</div>")
    return "".join(parts)


def _leader_chip(label, team, value_txt):
    return (
        f'<span class="leader-chip">{html.escape(label)}: '
        f'<b>{html.escape(team)} {html.escape(value_txt)}</b></span>'
    )


def _best(teams, key, lower=False):
    vals = [(t, t.get(key)) for t in teams if t.get(key) is not None]
    if not vals:
        return None
    return (min if lower else max)(vals, key=lambda x: x[1])


def season_section(season):
    """Render the Season Stats tab content, or "" when no season data.

    season is the season_stats.json dict: as_of, teams (32), leaders.
    """
    teams = (season or {}).get("teams") or []
    if not teams:
        return ""
    as_of = season.get("as_of", "")

    # Leaders board: one ranked category per stat. Team and goalie
    # stats show top 6 + bottom 6 (ranks 27-32); skater scoring shows
    # top 6 only, since bottom scoring is not useful for gambling.
    cats = []
    for key, label, fmt, lower in (
        ("pp", "Power play %", _pct, False),
        ("pk", "Penalty kill %", _pct, False),
        ("gf_gp", "Goals for / game", _f2, False),
        ("ga_gp", "Goals against / game", _f2, True),
        ("diff", "Goal differential", _signed, False),
        ("sv_pct", "Team save %", _sv, False),
        ("sh_pct", "Shooting %", _pct, False),
        ("faceoff", "Faceoff %", _pct, False),
    ):
        vals = [(t["abbr"], t.get(key)) for t in teams if t.get(key) is not None]
        vals.sort(key=lambda x: x[1], reverse=not lower)
        cats.append(_leader_cat(label, vals, fmt, show_bottom=True))
    ld = season.get("leaders") or {}
    goalies = ld.get("goalies") or []
    if goalies:
        sv_ranked = [(f"{g['name']} ({g['team']})", g["sv"])
                     for g in sorted(goalies, key=lambda g: -g["sv"])]
        cats.append(_leader_cat("Save % — starting goalies", sv_ranked, _sv,
                                show_bottom=True))
        gaa_ranked = [(f"{g['name']} ({g['team']})", g["gaa"])
                      for g in sorted(goalies, key=lambda g: g["gaa"])]
        cats.append(_leader_cat("GAA — starting goalies", gaa_ranked, _f2,
                                show_bottom=True))
    for key, label, fmt in (
        ("points_top", "Points", _int),
        ("goals_top", "Goals", _int),
        ("sog_top", "Shots on goal / game", _f2),
    ):
        top = ld.get(key) or []
        items = [(f"{p['name']} ({p['team']})", p["value"]) for p in top]
        if items:
            cats.append(_leader_cat(label, items, fmt, show_bottom=False))
    leaders_board = (
        '<div class="leaders-board"><h3 class="leaders-board-title">Leaders</h3>'
        f'<div class="leaders-grid">{"".join(cats)}</div></div>'
    )

    view_btns = "".join(
        f'<button class="view-btn{" active" if vid == "overview" else ""}" data-view="{vid}">'
        f"{html.escape(label)}</button>"
        for vid, label, _dk, _dd, _cols in SEASON_VIEWS
    )
    tables = "".join(
        _season_table(vid, label, dk, dd, cols, teams)
        for vid, label, dk, dd, cols in SEASON_VIEWS
    )
    return (
        '<h2 class="section-title">Season stats</h2>'
        f'<p class="section-note">Season to date through {html.escape(str(as_of))} &middot; '
        "all 32 teams &middot; NHL stats API + MoneyPuck. Click a column header to sort. "
        "Shading marks the top and bottom fifths of the league in that stat.</p>"
        f"{leaders_board}"
        '<input id="season-search" class="season-search" type="search" '
        'placeholder="Search teams…" autocomplete="off">'
        f'<div class="view-tabs">{view_btns}</div>'
        f"{tables}"
        '<p class="glossary">GF/GP goals for per game &middot; GA/GP goals against per game &middot; '
        "Sh% team shooting percentage, goals divided by shots (computed) &middot; Sv% team save "
        "percentage, computed from shots against &middot; xGF/xGA MoneyPuck expected goals per game "
        "&middot; Pts% points percentage &middot; L10 record over the last 10 games &middot; PP/PK "
        "season power-play / penalty-kill percentage &middot; FO% faceoff win percentage &middot; "
        "net PP/PK subtract shorthanded goals for/against. Percentile shading: the darkest green "
        "cells are the top fifth of the league in that stat, the darkest red the bottom fifth. "
        "Early-season numbers are noisy; ranks settle as games accumulate.</p>"
    )


PAGE_JS = """<script>
(function () {
  // Generic sortable tables: any table.sortable with th[data-sort].
  // Missing values (no data-v) always sink to the bottom.
  document.querySelectorAll("table.sortable").forEach(function (tbl) {
    var ths = tbl.querySelectorAll("th[data-sort]");
    ths.forEach(function (th) {
      th.addEventListener("click", function () {
        var tbody = tbl.querySelector("tbody");
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        var wasDesc = th.classList.contains("sorted-desc");
        ths.forEach(function (h) { h.classList.remove("sorted-desc", "sorted-asc"); });
        th.classList.add(wasDesc ? "sorted-asc" : "sorted-desc");
        var col = th.cellIndex;
        var numeric = th.getAttribute("data-sort") === "num";
        rows.sort(function (a, b) {
          if (numeric) {
            var av = parseFloat(a.cells[col].getAttribute("data-v"));
            var bv = parseFloat(b.cells[col].getAttribute("data-v"));
            if (isNaN(av) && isNaN(bv)) return 0;
            if (isNaN(av)) return 1;
            if (isNaN(bv)) return -1;
            return wasDesc ? av - bv : bv - av;
          }
          var at = a.cells[col].textContent, bt = b.cells[col].textContent;
          return wasDesc ? bt.localeCompare(at) : at.localeCompare(bt);
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  });

  // Page tabs: Daily | Season Stats.
  var tabBtns = document.querySelectorAll("[data-tab]");
  tabBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tabBtns.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      document.getElementById("tab-daily").hidden = btn.getAttribute("data-tab") !== "daily";
      var season = document.getElementById("tab-season");
      if (season) season.hidden = btn.getAttribute("data-tab") !== "season";
    });
  });

  // Season Stats view tabs.
  var viewBtns = document.querySelectorAll("[data-view]");
  viewBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var v = btn.getAttribute("data-view");
      viewBtns.forEach(function (b) { b.classList.toggle("active", b === btn); });
      document.querySelectorAll(".season-view").forEach(function (el) {
        el.hidden = el.getAttribute("data-view-panel") !== v;
      });
    });
  });

  // Team search: filters every Season Stats table by team name.
  var search = document.getElementById("season-search");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      document.querySelectorAll("#tab-season tbody tr").forEach(function (tr) {
        var name = tr.getAttribute("data-team") || "";
        tr.style.display = (!q || name.indexOf(q) !== -1) ? "" : "none";
      });
    });
  }
})();
</script>"""


BET_TYPES = """<h2 class="section-title">Which bets pay the book the most?</h2>
<div class="bet-types">
<p>Sportsbooks keep a cut of every dollar wagered, called the hold. On straight bets (one team, one total, one spread) the hold runs roughly 4 to 6 percent. On parlays it runs roughly 18 to 30 percent. Parlays are about a third of the money wagered but roughly 60 to 65 percent of sportsbook revenue.</p>
<p>State regulator reports make the gap concrete. Illinois in 2023: parlays held 18.2 percent versus 4.9 percent for straight bets. New Jersey in September 2024: parlays held 24.2 percent versus 4.4 percent for everything else.</p>
<p>Why the gap? A parlay multiplies the book's margin across every leg, and parlay payouts are set below the true odds of all legs hitting. Each added leg costs more than it pays.</p>
<p class="honest">Honest limit: no public source splits hold by sport AND bet type. These figures are all sports by bet type; state reports split by sport separately, so there is no verified "NHL parlay hold" or "NFL straight hold" number to show here.</p>
</div>"""


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Sportsbook Tracker">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#fafafa">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0a0a0a">
<title>NHL + NFL Matchup Chart — {date}</title>
<style>
:root {{ --bg: #fafafa; --card: #ffffff; --text: #171717; --muted: #737373; --border: #e5e5e5; --accent: #2f5fd0; --defense: #64748b; --offense: #e8912d; --defbar: #2f5fd0; --chip: #f0f0f0; --chip-text: #404040; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #0a0a0a; --card: #171717; --text: #f5f5f5; --muted: #a3a3a3; --border: #262626; --accent: #7aa2ff; --defense: #8b98a9; --offense: #f0a83c; --defbar: #7aa2ff; --chip: #262626; --chip-text: #e5e5e5; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
main {{ max-width: 1100px; margin: 0 auto; padding: 20px 16px 40px; }}
h1 {{ margin: 0 0 4px; font-size: 24px; letter-spacing: -0.01em; }}
.subtitle {{ color: var(--muted); margin: 0 0 16px; font-size: 14px; }}
.tabs {{ display: flex; gap: 8px; margin: 0 0 20px; }}
.tab-btn {{ padding: 8px 16px; border-radius: 999px; border: 1px solid var(--border); background: var(--card); color: var(--muted); font-size: 14px; font-weight: 600; cursor: pointer; }}
.tab-btn.active {{ background: var(--text); color: var(--bg); border-color: var(--text); }}
.guide {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin-bottom: 20px; line-height: 1.5; }}
.guide h2 {{ margin: 0 0 6px; font-size: 15px; }}
.guide p {{ margin: 4px 0; color: var(--muted); font-size: 14px; }}
.grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; }}
@media (min-width: 900px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }}
.matchup {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 10px; }}
.matchup h2 {{ margin: 0; font-size: 17px; letter-spacing: -0.01em; }}
.time {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
.pair-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin: 10px 0 4px; }}
.pct-row {{ display: flex; align-items: center; gap: 10px; margin: 5px 0; }}
.pct-label {{ width: 96px; flex-shrink: 0; font-size: 13px; }}
.pct-track {{ flex: 1; height: 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 7px; overflow: hidden; position: relative; }}
.pct-fill {{ height: 100%; border-radius: 6px; }}
.pct-fill.off {{ background: var(--offense); }}
.pct-fill.def {{ background: var(--defbar); }}
.pct-tick {{ position: absolute; left: calc(50% - 1px); top: 0; bottom: 0; width: 2px; background: var(--text); opacity: 0.55; }}
.pct-value {{ width: 84px; flex-shrink: 0; text-align: right; font-size: 13px; font-weight: 600; }}
.goalie-note {{ margin-top: 12px; font-size: 13px; font-weight: 600; }}
.gstat {{ color: var(--muted); font-size: 12px; }}
.card-details {{ margin-top: 10px; }}
.card-details summary {{ cursor: pointer; font-size: 13px; font-weight: 600; color: var(--muted); }}
.card-details summary:hover {{ color: var(--text); }}
.analytics {{ margin-top: 10px; font-size: 13px; color: var(--muted); line-height: 1.6; }}
.section-title {{ margin: 24px 0 6px; font-size: 19px; letter-spacing: -0.01em; }}
.section-note {{ color: var(--muted); margin: 0 0 14px; line-height: 1.5; font-size: 14px; }}
.nfl-records {{ font-size: 13px; color: var(--muted); margin: 6px 0; }}
.nfl-score {{ margin-top: 8px; font-size: 13px; font-weight: 600; }}
.odds-note {{ margin-top: 8px; font-size: 13px; color: var(--muted); }}
.nfl-bye {{ color: var(--muted); margin: 0 0 14px; font-size: 14px; }}
.bet-types {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; line-height: 1.6; }}
.bet-types p {{ margin: 6px 0; color: var(--muted); font-size: 14px; }}
.bet-types p.honest {{ color: var(--text); }}
.backup {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; background: var(--chip); color: var(--chip-text); margin-left: 4px; }}
.odds {{ margin-top: 8px; display: inline-block; font-size: 13px; font-weight: 600; padding: 5px 10px; border-radius: 999px; border: 1px solid var(--border); }}
.model {{ margin-top: 10px; font-size: 13px; color: var(--muted); line-height: 1.6; }}
.model-title {{ font-weight: 600; color: var(--muted); }}
.sog {{ margin-top: 10px; font-size: 13px; color: var(--muted); line-height: 1.6; }}
.sog-title {{ font-weight: 600; color: var(--muted); }}
.picks {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; line-height: 1.6; margin-bottom: 20px; }}
.pick {{ margin: 8px 0; font-size: 14px; }}
.pick-kind {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; background: var(--chip); color: var(--chip-text); margin-right: 6px; }}
.pick-label {{ font-weight: 600; }}
.pick-reason {{ color: var(--muted); display: block; font-size: 13px; }}
.pick-asof {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
.league-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 6px 16px 12px; overflow-x: auto; }}
.league-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.league-table th, .league-table td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border); }}
.league-table th:nth-child(2), .league-table th:nth-child(3), .league-table th:nth-child(4), .league-table td:nth-child(2), .league-table td:nth-child(3), .league-table td:nth-child(4) {{ text-align: right; }}
.league-table th[data-sort] {{ cursor: pointer; white-space: nowrap; }}
.league-table th.sorted-desc::after {{ content: " ▼"; font-size: 10px; }}
.league-table th.sorted-asc::after {{ content: " ▲"; font-size: 10px; }}
.league-table tr:last-child td {{ border-bottom: none; }}
.view-tabs {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 12px; }}
.view-btn {{ padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--card); color: var(--muted); font-size: 13px; font-weight: 600; cursor: pointer; }}
.view-btn.active {{ background: var(--text); color: var(--bg); border-color: var(--text); }}
.season-search {{ width: 100%; max-width: 320px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); color: var(--text); font-size: 14px; margin-bottom: 12px; }}
.leaders {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; align-items: center; }}
.leaders-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-right: 4px; }}
.leader-chip {{ background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 4px 10px; font-size: 13px; }}
.leaders-board {{ margin-bottom: 16px; }}
.leaders-board-title {{ margin: 0 0 10px; font-size: 15px; }}
.leaders-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
@media (max-width: 700px) {{ .leaders-grid {{ grid-template-columns: 1fr; }} }}
.leader-cat {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; }}
.leader-cat h4 {{ margin: 0 0 8px; font-size: 14px; }}
.leader-list {{ list-style: none; margin: 0; padding: 0; font-size: 13px; line-height: 1.7; }}
.leader-list li {{ display: flex; gap: 8px; align-items: baseline; }}
.leader-list .lr {{ color: var(--muted); width: 18px; text-align: right; flex-shrink: 0; }}
.leader-list .lname {{ flex: 1; min-width: 0; }}
.leader-list b {{ flex-shrink: 0; }}
.leader-bottom {{ margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--border); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }}
.stats-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 6px 16px 12px; overflow-x: auto; }}
.stats-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.stats-table th, .stats-table td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }}
.stats-table th.num, .stats-table td.num {{ text-align: right; }}
.stats-table th[data-sort] {{ cursor: pointer; }}
.stats-table th.sorted-desc::after {{ content: " ▼"; font-size: 10px; }}
.stats-table th.sorted-asc::after {{ content: " ▲"; font-size: 10px; }}
.stats-table tr:last-child td {{ border-bottom: none; }}
.stats-table td.t5 {{ background: rgba(22, 163, 74, 0.16); }}
.stats-table td.t4 {{ background: rgba(22, 163, 74, 0.08); }}
.stats-table td.t2 {{ background: rgba(220, 38, 38, 0.07); }}
.stats-table td.t1 {{ background: rgba(220, 38, 38, 0.14); }}
@media (prefers-color-scheme: dark) {{
  .stats-table td.t5 {{ background: rgba(74, 222, 128, 0.18); }}
  .stats-table td.t4 {{ background: rgba(74, 222, 128, 0.09); }}
  .stats-table td.t2 {{ background: rgba(248, 113, 113, 0.08); }}
  .stats-table td.t1 {{ background: rgba(248, 113, 113, 0.16); }}
}}
.glossary {{ color: var(--muted); font-size: 13px; line-height: 1.6; margin-top: 16px; }}
footer {{ margin-top: 24px; color: var(--muted); font-size: 13px; line-height: 1.5; }}
@media (max-width: 520px) {{
  .pct-label {{ width: 84px; font-size: 12px; }}
  .pct-value {{ width: 76px; font-size: 12px; }}
}}
.topnav {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }}
.brand {{ font-size: 17px; font-weight: 700; letter-spacing: -0.01em; color: var(--text); text-decoration: none; }}
.brand span {{ color: var(--accent); }}
.nav-links {{ display: flex; gap: 6px; }}
.nav-link {{ padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--card); color: var(--muted); font-size: 13px; font-weight: 600; text-decoration: none; }}
.nav-link.active {{ background: var(--text); color: var(--bg); border-color: var(--text); }}
</style>
</head>
<body>
<main>
<nav class="topnav" aria-label="Sports">
  <a class="brand" href="index.html">Sportsbook <span>Tracker</span></a>
  <div class="nav-links">
    <a class="nav-link active" href="index.html" aria-current="page">NHL</a>
    <a class="nav-link" href="cbb/index.html">CBB</a>
  </div>
</nav>
<h1>NHL + NFL Matchup Chart</h1>
<p class="subtitle">{date} &middot; NHL today, NFL this week &middot; rebuilt every morning</p>
{tabs}
<div id="tab-daily">
{picks_section}
<div class="grid">
{cards}
</div>
{league_section}
{nfl_section}
{bet_types}
<div class="guide">
<h2>How to read it</h2>
<p>Each card pairs offense against the opposing defense. Every bar is the team's percentile (0-100) on a bordered track: the right edge is 100%, and the tick in the middle is league average (50th percentile). Longer is always better, for offense and defense alike, so a bar past the tick is above average. Amber is goals scored per game, blue is goals allowed per game, with each team's raw number and league rank next to its bar.</p>
<p>GF rank 1st means the most goals scored per game. GA rank 1st means the fewest goals allowed per game.</p>
<p>A note appears on a card only when a team's No. 1 goalie is confirmed out.</p>
<p>The FanDuel row on each card shows the spread, the puck line, and O/U, the total goals line. Lines only, no prices.</p>
<p>Last 10 is the regular-season record over the last 10 finals. Away and Home splits are season-to-date goals per game in that venue.</p>
<p>Model: xGF/xGA are MoneyPuck all-situations expected goals per game; PP/PK are season-to-date; rest is days since the last game. Win % is a Poisson model on venue-split goals with ties split, and edge is the model minus the vig-free FanDuel moneyline probability. Goalie edge is the starter's save % minus the opposing starter's, shown for context and not folded into the model. "No line" means no FanDuel moneyline was available.</p>
<p>SOG watch lists each team's top two shooters by shots on goal per game (minimum 5 games played) and the shots per game the opposing team allows.</p>
<p>League totals lists every team's average combined goals per game and goal differential for the season, sortable by either column.</p>
</div>
</div>
{season_container}
<footer>
<p>Snapshot of today's slate, rebuilt every morning with that day's games. Ranks: GF 1st is the most goals scored per game, GA 1st is the fewest goals allowed per game.</p>
</footer>
</main>
{page_script}
</body>
</html>
"""


def tabs_nav(has_season):
    """Daily | Season Stats buttons, or "" when no season data exists."""
    if not has_season:
        return ""
    return (
        '<div class="tabs"><button class="tab-btn active" data-tab="daily">Daily</button>'
        '<button class="tab-btn" data-tab="season">Season Stats</button></div>'
    )


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "today.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "dist/index.html"
    nfl_src = sys.argv[3] if len(sys.argv) > 3 else "nfl.json"
    season_src = sys.argv[4] if len(sys.argv) > 4 else "season_stats.json"
    with open(src) as f:
        data = json.load(f)
    cards = "\n".join(card(g) for g in data["games"])
    nfl = None
    if os.path.exists(nfl_src):
        try:
            with open(nfl_src) as f:
                nfl = json.load(f)
        except Exception:
            nfl = None
    season = None
    if os.path.exists(season_src):
        try:
            with open(season_src) as f:
                season = json.load(f)
        except Exception:
            season = None
    if not (season or {}).get("teams"):
        season = None
    season_html = season_section(season)
    season_container = (
        f'<div id="tab-season" hidden>\n{season_html}\n</div>' if season_html else ""
    )
    out = PAGE.format(
        date=html.escape(data["date"]),
        cards=cards,
        picks_section=picks_section(),
        league_section=league_section(data),
        page_script=PAGE_JS,
        nfl_section=nfl_section(nfl),
        bet_types=BET_TYPES,
        tabs=tabs_nav(bool(season)),
        season_container=season_container,
    )
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w") as f:
        f.write(out)
    print(f"Wrote {dst} with {len(data['games'])} NHL games"
          f" and {len(nfl['games']) if nfl else 0} NFL games"
          f" (season stats: {len(season['teams']) if season else 0} teams).")


if __name__ == "__main__":
    main()
