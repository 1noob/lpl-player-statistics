import json
import os
import logging

from flask import Flask, jsonify
from mwrogue.esports_client import EsportsClient
from datetime import datetime as d, timezone, timedelta
from flask_cors import CORS
from upstash_redis import Redis
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
site = EsportsClient("lol")


def _make_redis():
    """Redis is optional: missing credentials must not take the service down."""
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        logger.warning("UPSTASH_REDIS_REST_URL/TOKEN not set - running without cache")
        return None
    try:
        return Redis(url=url, token=token)
    except Exception:
        logger.exception("failed to init Redis - running without cache")
        return None


redis = _make_redis()


def cache_get(key):
    """A cache read failure degrades to a miss; it must never kill the request."""
    if redis is None:
        return None
    try:
        return redis.get(key)
    except Exception:
        logger.exception("cache GET failed for key=%s", key)
        return None


def cache_set(key, value, seconds):
    if redis is None:
        return
    try:
        redis.setex(key=key, value=value, seconds=seconds)
    except Exception:
        logger.exception("cache SET failed for key=%s", key)


@app.route('/')
def home():
    return 'Hello folks! Welcome to lpl statistics API.'


@app.route('/health')
def health():
    """Real health check: reports whether the upstream data source is usable.

    Note: the catch-all `/<player>` route cannot serve this purpose because it
    runs the full fetch pipeline.
    """
    try:
        site.cargo_client.query(
            limit=1,
            tables="ScoreboardPlayers=SP",
            fields="SP.MatchId",
        )
        return jsonify({"ok": True, "upstream": "reachable"})
    except Exception as e:
        logger.exception("health check failed")
        return jsonify({"ok": False, "upstream": "unreachable", "error": str(e)}), 503


@app.route('/<player>')
def player_all(player):
    cache_schedule_key = player + '_schedule'
    cache_match_key = player + '_match'

    cache_schedule_data = cache_get(cache_schedule_key)
    cache_match_data = cache_get(cache_match_key)

    upstream_errors = []

    if cache_schedule_data:
        schedule_data = json.loads(cache_schedule_data)
    else:
        try:
            schedule_data = match_schedule(player)
        except Exception as e:
            logger.exception("match_schedule failed for player=%s", player)
            upstream_errors.append("schedule: %s" % e)
            schedule_data = None
        else:
            cache_set(cache_schedule_key, json.dumps(schedule_data), 3600)

    if cache_match_data:
        match_data = json.loads(cache_match_data)
    else:
        try:
            match_data = all_match_info(player)
        except Exception as e:
            logger.exception("all_match_info failed for player=%s", player)
            upstream_errors.append("match: %s" % e)
            match_data = None
        else:
            cache_set(cache_match_key, json.dumps(match_data), 3600)

    lpl_data = lpl_stats(match_data)
    world_data = world_stats(match_data)
    all_data = all_stats(match_data)

    payload = [lpl_data, world_data, all_data, schedule_data]

    # Keep the historical contract with the front end (200 + 4-element array)
    # but surface the upstream failure explicitly, so the client can tell
    # "really zero" apart from "fetch failed".
    # Use the Flask JSON provider so the response bytes stay identical to before.
    if upstream_errors:
        resp = app.json.response(payload)
        resp.headers["X-Upstream-Status"] = "degraded"
        resp.headers["X-Upstream-Errors"] = "; ".join(upstream_errors)[:900]
        return resp

    return payload


def match_schedule(player):
    """Upcoming 3 matches for the player's team.

    This is an internal helper and must NOT carry @app.route: it used to be a
    Flask view as well, and returning None on upstream failure made Flask raise
    `TypeError: The view function ... did not return a valid response.` -> HTTP 500.
    Exceptions now propagate and the caller decides how to degrade.
    """
    team_list = site.cargo_client.query(
        limit=1,
        tables="Players=P",
        fields="P.Team",
        where='P.ID="%s"' % player,
    )
    if not team_list:
        raise LookupError('no team found for player "%s"' % player)

    team = team_list[0]['Team']
    datetime_week_later = d.now(timezone.utc) + timedelta(days=3)
    response = site.cargo_client.query(
        limit=3,
        tables="MatchSchedule=MS, Tournaments=T",
        fields="MS.Team1, MS.Team2, MS.DateTime_UTC, MS.Team1Score, MS.Team2Score, MS.BestOf, T.StandardName, "
               "MS.Stream",
        where='(MS.Team1="%s" OR MS.Team2="%s") AND MS.DateTime_UTC<"%s"' % (team, team, datetime_week_later),
        join_on="MS.OverviewPage=T.OverviewPage",
        order_by="MS.DateTime_UTC DESC"
    )
    for res in response:
        cst_date = d.strptime(res["DateTime UTC"], "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
        res["DateTime CST"] = d.strftime(cst_date, "%Y-%m-%d %H:%M:%S")
        res["Day of Week"] = d.strftime(cst_date, "%a")
    return response


def all_match_info(player):
    """Page through the player's full match history, one year at a time.

    Internal helper - must NOT carry @app.route (same reason as match_schedule).
    """
    response = []
    now = d.now(timezone.utc)
    prev = d.now(timezone.utc) - timedelta(days=365)

    res = site.cargo_client.query(
        limit=500,
        tables="ScoreboardPlayers=SP",
        fields="SP.OverviewPage, SP.Team, SP.TeamVs, SP.DateTime_UTC, SP.PlayerWin, SP.MatchId, "
               "SP.Champion, SP.Kills, SP.Deaths, SP.Assists",
        where='SP.Link="%s" AND SP.DateTime_UTC >= "%s" AND SP.DateTime_UTC <= "%s" ' % (player, prev, now),
        order_by="SP.DateTime_UTC DESC"
    )
    # Guard: the loop must terminate even if upstream keeps returning rows.
    guard = 0
    while res:
        guard += 1
        if guard > 50:
            logger.warning("pagination guard tripped for player=%s", player)
            break
        response += res
        now = prev
        prev -= timedelta(days=365)
        res = site.cargo_client.query(
            limit=500,
            tables="ScoreboardPlayers=SP, MatchSchedule=MS",
            fields="SP.OverviewPage, SP.Team, SP.TeamVs, SP.DateTime_UTC, SP.PlayerWin, SP.MatchId, "
                   "SP.Champion, SP.Kills, SP.Deaths, SP.Assists",
            join_on="SP.MatchId=MS.MatchId",
            where='SP.Link="%s" AND SP.DateTime_UTC >= "%s" AND SP.DateTime_UTC <= "%s" ' % (player, prev, now),
            order_by="SP.DateTime_UTC DESC"
        )
    return response


def lpl_match_info(response):
    lpl_res = []
    if response:
        for res in response:
            match_id = res["MatchId"]
            if match_id.find("LPL") != -1 and match_id.find("All-Star") == -1 and match_id.find(
                    "LCK") == -1 and match_id.find("Regional") == -1:
                lpl_res.append(res)
    return lpl_res


def world_match_info(response):
    world_res = []
    if response:
        for res in response:
            match_id = res["MatchId"]
            if match_id.find("World") != -1 or match_id.find("Mid-Season") != -1 or match_id.find("Rift Rivals") != -1:
                world_res.append(res)
    return world_res


def world_stats(response):
    return data_process(world_match_info(response))


def lpl_stats(response):
    return data_process(lpl_match_info(response))


def all_stats(response):
    return data_process(response)


def data_process(response):
    champions_meta = []
    champions_dict = {}

    if response is None:
        return []

    match_total = 0
    match_wins = 0
    match_kills = 0
    match_deaths = 0
    match_assists = 0

    index = 0
    for res in response:
        match_total += 1
        match_kills += int(res["Kills"])
        match_deaths += int(res["Deaths"])
        match_assists += int(res["Assists"])

        champion_name = res["Champion"]
        if champion_name not in champions_dict.keys():
            champions_dict[champion_name] = index
            champions_meta.append({
                "name": champion_name,
                "games": 1,
                "wins": 0,
                "kills": int(res["Kills"]),
                "deaths": int(res["Deaths"]),
                "assists": int(res["Assists"]),
            })
            index += 1
        else:
            champion_index = champions_dict[champion_name]
            champions_meta[champion_index]["games"] += 1
            champions_meta[champion_index]["kills"] += int(res["Kills"])
            champions_meta[champion_index]["deaths"] += int(res["Deaths"])
            champions_meta[champion_index]["assists"] += int(res["Assists"])

        if res["PlayerWin"] == "Yes":
            match_wins += 1
            champions_meta[champions_dict[champion_name]]["wins"] += 1

    champions = champions_dict.keys()

    for champion in champions:
        champion_index = champions_dict[champion]
        champions_meta[champion_index]["win_rate"] = round(champions_meta[champion_index]["wins"] / champions_meta[champion_index]["games"], 2)

    champions_meta = sorted(champions_meta, key=lambda i: (i['games'], i['win_rate']), reverse=True)

    return [
        {"total": match_total,
         "wins": match_wins,
         "win_rate": round(match_wins / max(match_total, 1), 2),
         "kills": match_kills, "deaths": match_deaths, "assists": match_assists},
        champions_meta]
