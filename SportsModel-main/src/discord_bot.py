from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

import discord
from discord import app_commands

from .agent import resolve_analyzer
from .data.odds_scraper import OddsScraper
from .data.team_registry import supported_sports
from .edge.detector import EdgeResult


@dataclass
class EdgeSummary:
    sport: str
    game_id: str
    matchup: str
    edge: EdgeResult

    def as_line(self) -> str:
        ev_pct = self.edge.expected_value * 100
        kelly_pct = self.edge.kelly_fraction * 100
        return (
            f"{self.matchup} → {self.edge.recommendation} {self.edge.line:.1f} "
            f"(EV {ev_pct:.1f}%, Kelly {kelly_pct:.1f}%)"
        )


def _fetch_edges_for_sport(sport: str, min_ev: float, limit: int | None = None) -> List[EdgeSummary]:
    analyzer = resolve_analyzer(sport)
    scraper = OddsScraper()
    games = scraper.fetch_odds_api(sport=sport, max_games=limit)

    results: List[EdgeSummary] = []
    for game in games:
        payload = {**game, "injuries": []}
        # Soccer analyzer needs the sport code for league-level context.
        if sport.startswith("soccer_"):
            payload["sport"] = sport
        edge = analyzer.analyze_game(**payload)
        if edge and edge.expected_value >= min_ev:
            matchup = f"{game['away_team']} @ {game['home_team']}"
            results.append(EdgeSummary(sport=sport, game_id=game["game_id"], matchup=matchup, edge=edge))

    return sorted(results, key=lambda e: e.edge.expected_value, reverse=True)


class BettingBot(discord.Client):
    def __init__(self, *, guild_id: int | None = None, premium_role: str | None = None):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.guild_id = guild_id
        self.premium_role = premium_role or "premium"

    async def setup_hook(self):
        if self.guild_id:
            guild = discord.Object(self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    def user_is_premium(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.name.lower() == self.premium_role.lower() for role in interaction.user.roles)

    async def on_ready(self):
        print(f"Bot connected as {self.user} (premium role: {self.premium_role})")


# Slash commands
bot = BettingBot(
    guild_id=int(os.environ.get("DISCORD_GUILD_ID", "0")) or None,
    premium_role=os.environ.get("DISCORD_PREMIUM_ROLE", "premium"),
)


@bot.tree.command(name="scan", description="Free tier: top 5 edges across all sports")
async def scan_all(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    edges: List[EdgeSummary] = []
    for sport in supported_sports():
        edges.extend(_fetch_edges_for_sport(sport, min_ev=0.03, limit=None))

    top_edges = edges[:5]
    if not top_edges:
        await interaction.followup.send("No edges found right now. Try again soon!")
        return

    lines = [f"**Top {len(top_edges)} edges (free tier):**"]
    for idx, edge in enumerate(top_edges, start=1):
        lines.append(f"{idx}. [{edge.sport}] {edge.as_line()}")

    await interaction.followup.send("\n".join(lines))


@bot.tree.command(name="scan_sport", description="Premium: full edge list for a single sport (EV ≥ 4%)")
@app_commands.describe(sport="Sport code (e.g., basketball_nba, football_nfl, soccer_epl)")
async def scan_sport(interaction: discord.Interaction, sport: str):
    if sport not in supported_sports():
        await interaction.response.send_message(
            f"Unknown sport '{sport}'. Supported: {', '.join(supported_sports())}", ephemeral=True
        )
        return

    if not bot.user_is_premium(interaction):
        await interaction.response.send_message(
            "Premium required for sport-specific scans. Please upgrade to unlock full coverage.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    edges = _fetch_edges_for_sport(sport, min_ev=0.04, limit=None)
    if not edges:
        await interaction.followup.send(f"No qualifying edges (EV ≥ 4%) for {sport} right now.")
        return

    lines: List[str] = [f"**{sport} edges (premium, EV ≥ 4%):**"]
    for idx, edge in enumerate(edges, start=1):
        ev_pct = edge.edge.expected_value * 100
        lines.append(f"{idx}. {edge.as_line()} — true p={edge.edge.true_prob:.3f}, market p={edge.edge.market_prob:.3f}")

    await interaction.followup.send("\n".join(lines))


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN not set. Provide your bot token as an environment variable.")
    bot.run(token)


if __name__ == "__main__":
    main()
