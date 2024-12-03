from flask import Flask
from mwrogue.esports_client import EsportsClient
from datetime import datetime, timezone, timedelta
import json

from flask_cors import CORS

app = Flask(__name__)
CORS(app)
site = EsportsClient("lol")


@app.route('/')
def home():
    return 'Hello folks! Welcome to lpl statistics API.'


@app.route('/<player>')
def player_all(player):
    return [lpl_stat(player), world_stat(player), match_schedule(player)]


@app.route('/match-schedule/<player>')
def match_schedule(player):
    team_list = site.cargo_client.query(
        limit=1,
        tables="Players=P",
        fields="P.Team",
        where='P.ID="%s"' % player,
    )

    if len(team_list) == 0:
        return None

    team = team_list[0]['Team']

    datetime_week_later = datetime.now() + timedelta(days=3)

    response = site.cargo_client.query(
        limit=3,
        tables="MatchSchedule=MS, Tournaments=T",
        fields="MS.Team1, MS.Team2, MS.DateTime_UTC, MS.Team1Score, MS.Team2Score, MS.BestOf, T.StandardName, MS.Stream",
        where='(MS.Team1="%s" OR MS.Team2="%s") AND MS.DateTime_UTC<"%s"' % (team, team, datetime_week_later),
        join_on="MS.OverviewPage=T.OverviewPage",
        order_by="MS.DateTime_UTC DESC"
    )

    for res in response:
        cst_date = datetime.strptime(res["DateTime UTC"],  "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
        res["DateTime CST"] = datetime.strftime(cst_date, "%Y-%m-%d %H:%M:%S")
        res["Day of Week"] = datetime.strftime(cst_date, "%a")

    return response


@app.route('/all-match-info/<player>')
def all_match_info(player):
    response = []
    now = datetime.now(timezone.utc)
    prev = datetime.now(timezone.utc) - timedelta(days=365)
    res = site.cargo_client.query(
        limit=500,
        tables="ScoreboardPlayers=SP",
        fields="SP.OverviewPage, SP.Team, SP.TeamVs, SP.DateTime_UTC, SP.PlayerWin, SP.MatchId, "
               "SP.Champion, SP.Kills, SP.Deaths, SP.Assists",
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
    return data_process(response)


@app.route('/lpl-stat/<player>')
def lpl_stat(player):
    response = lpl_match_info(player)
    return data_process(response)


def data_process(response):
    champions_meta = []
    champions_dict = {}

    if len(response) == 0:
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
         "win_rate": round(match_wins / match_total, 2),
         "kills": match_kills, "deaths": match_deaths, "assists": match_assists},
        champions_meta]
