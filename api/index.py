from flask import Flask
from mwrogue.esports_client import EsportsClient

app = Flask(__name__)

site = EsportsClient("lol")


@app.route('/')
def home():
    return 'Hello folks! Welcome to JackeyLove\'s statics API.'


@app.route('/sp')
def scoreboard_players():
    player = "JackeyLove"
    response = site.cargo_client.query(
        limit=500,
        tables="ScoreboardPlayers=SP",
        fields="SP.OverviewPage, SP.Champion, SP.Kills, SP.Deaths, SP.Assists, SP.Team, SP.TeamVs, SP.DateTime_UTC, "
               "SP.GameId",
        where='SP.Link="%s"' % player,
        order_by="SP.DateTime_UTC DESC"
    )
    return response


@app.route('/ms')
def match_schedule():
    player = "JackeyLove"
    response = site.cargo_client.query(
        limit=50,
        tables="ScoreboardPlayers=SP, MatchSchedule=MS",
        fields="SP.OverviewPage, SP.Team, SP.TeamVs, SP.DateTime_UTC, SP.PlayerWin, SP.MatchId",
        join_on="SP.MatchId=MS.MatchId",
        where='SP.Link="%s"' % player,
        order_by="SP.DateTime_UTC DESC"
    )
    return response
