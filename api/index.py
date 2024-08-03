from flask import Flask
from mwrogue.esports_client import EsportsClient
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

site = EsportsClient("lol")
champions_match_total = {}
champions_match_win = {}
champions_win_rate = {}


@app.route('/')
def home():
    return 'Hello folks! Welcome to LOL\'s Pro statics API.'


@app.route('/all-match-info/<player>')
def all_match_info(player):
    response = []
    now = datetime.now(timezone.utc)
    prev = datetime.now(timezone.utc) - timedelta(days=365)
    res = site.cargo_client.query(
        limit=500,
        tables="ScoreboardPlayers=SP, MatchSchedule=MS",
        fields="SP.OverviewPage, SP.Team, SP.TeamVs, SP.DateTime_UTC, SP.PlayerWin, SP.MatchId, "
               "SP.Champion, SP.Kills, SP.Deaths, SP.Assists",
        join_on="SP.MatchId=MS.MatchId",
        where='SP.Link="%s" AND SP.DateTime_UTC >= "%s" AND SP.DateTime_UTC <= "%s" ' % (player, prev, now),
        order_by="SP.DateTime_UTC DESC"
    )
    while res:
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


@app.route('/lpl-match-info/<player>')
def lpl_match_info(player):
    response = all_match_info(player)
    if len(response) == 0:
        return []

    lpl_res = []
    for res in response:
        match_id = res["MatchId"]
        if match_id.find("LPL") != -1 and match_id.find("All-Star") == -1 and match_id.find(
                "LCK") == -1 and match_id.find("Regional") == -1:
            lpl_res.append(res)
    return lpl_res


@app.route('/world-match-info/<player>')
def world_match_info(player):
    response = all_match_info(player)
    if len(response) == 0:
        return []

    world_res = []
    for res in response:
        match_id = res["MatchId"]
        if match_id.find("World") != -1 or match_id.find("Mid-Season") != -1 or match_id.find("Rift Rivals") != -1:
            world_res.append(res)
    return world_res


@app.route('/world-stat/<player>')
def world_stat(player):
    response = world_match_info(player)
    if len(response) == 0:
        return []

    match_total = 0
    match_win_total = 0
    match_kills = 0
    match_deaths = 0
    match_assists = 0

    for res in response:
        match_total += 1
        match_kills += int(res["Kills"])
        match_deaths += int(res["Deaths"])
        match_assists += int(res["Assists"])
        champion = res["Champion"]
        if champion not in champions_match_total:
            champions_match_total[champion] = 1
            champions_match_win[champion] = 0
        else:
            champions_match_total[champion] += 1
        if res["PlayerWin"] == "Yes":
            match_win_total += 1
            champions_match_win[champion] += 1

    champions = champions_match_total.keys()
    for champion in champions:
        champions_win_rate[champion] = round(champions_match_win[champion] / champions_match_total[champion], 2)
    return [
        {"world_total": match_total,
         "world_win_total": match_win_total,
         "world_win_rate": round(match_win_total / match_total, 2),
         "world_kills": match_kills, "world_deaths": match_deaths, "world_assists": match_assists},
        champions_win_rate, champions_match_total]


@app.route('/lpl-stat/<player>')
def lpl_stat(player):
    response = lpl_match_info(player)
    if len(response) == 0:
        return []

    match_total = 0
    match_win_total = 0
    match_kills = 0
    match_deaths = 0
    match_assists = 0

    for res in response:
        match_total += 1
        match_kills += int(res["Kills"])
        match_deaths += int(res["Deaths"])
        match_assists += int(res["Assists"])
        champion = res["Champion"]
        if champion not in champions_match_total:
            champions_match_total[champion] = 1
            champions_match_win[champion] = 0
        else:
            champions_match_total[champion] += 1
        if res["PlayerWin"] == "Yes":
            match_win_total += 1
            champions_match_win[champion] += 1

    champions = champions_match_total.keys()
    for champion in champions:
        champions_win_rate[champion] = round(champions_match_win[champion] / champions_match_total[champion], 2)
    return [
        {"lpl_total": match_total,
         "lpl_win_total": match_win_total,
         "lpl_win_rate": round(match_win_total / match_total, 2),
         "lpl_kills": match_kills, "lpl_deaths": match_deaths, "lpl_assists": match_assists},
        champions_win_rate, champions_match_total]
