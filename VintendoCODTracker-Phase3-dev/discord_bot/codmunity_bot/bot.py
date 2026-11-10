from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import discord
from dotenv import load_dotenv
from discord import app_commands
from discord.ext import commands, tasks

from .card import make_card
from .client import CODMunityClient, CODMunityError
from .local_data import load_local
from .stats import leaderboard_rows, match_summary, matches, number, profile_modes, stat_lines
from .store import Store
from .validation import valid_uid, valid_username

load_dotenv(Path(__file__).resolve().parents[1] / '.env')


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


class VintendoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.none()
        super().__init__(command_prefix='!', intents=intents, application_id=_env_int('DISCORD_APPLICATION_ID') or None)
        self.store = Store(os.environ.get('BOT_DATABASE', str(Path(__file__).resolve().parents[1] / 'data' / 'bot.sqlite3')))
        self.codmunity = CODMunityClient()
        self.guild_id = _env_int('DISCORD_GUILD_ID')
        self.channel_id = _env_int('DISCORD_CHANNEL_ID')
        self.auto_post = os.environ.get('AUTO_POST_MATCHES', 'false').casefold() in ('1', 'true', 'yes', 'on')
        self.interval = max(300, _env_int('CODMUNITY_POLL_INTERVAL', 900))

    async def setup_hook(self):
        if not self.guild_id:
            raise RuntimeError('DISCORD_GUILD_ID must be configured before starting the bot')
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        if self.auto_post:
            self.monitor_matches.change_interval(seconds=self.interval)
            self.monitor_matches.start()

    async def profile(self, uid: str) -> dict:
        local = load_local(uid)
        if local is not None:
            self.store.save_snapshot(uid, local)
            return local
        data = await self.codmunity.get_player(uid)
        self.store.save_snapshot(uid, data)
        return data

    @tasks.loop(seconds=900)
    async def monitor_matches(self):
        channel = self.get_channel(self.channel_id) if self.channel_id else None
        for player in self.store.registrations():
            try:
                data = await self.profile(player['uid'])
                fresh = self.store.record_matches(player['uid'], matches(data))
                if self.auto_post and channel:
                    for match in fresh:
                        await channel.send(f"New match for **{player['username']}**: {match_summary(match)}")
            except (CODMunityError, OSError, ValueError):
                # A failed fetch is not an empty history and cannot advance baseline.
                continue
            await asyncio.sleep(2)

    @monitor_matches.before_loop
    async def before_monitor(self):
        await self.wait_until_ready()


bot = VintendoBot()


def _registration(discord_id: int):
    return bot.store.registration(str(discord_id))


@bot.tree.command(name='register', description='Register your CODMunity username and optional manual UID')
@app_commands.describe(username='Your self-declared CODMunity username', uid='Manual CODMunity UID if resolution is unavailable')
async def register(interaction: discord.Interaction, username: str, uid: str | None = None):
    username = username.strip()
    if not valid_username(username):
        await interaction.response.send_message('Username must be 1–64 letters, numbers, spaces, dots, #, _ or -.', ephemeral=True)
        return
    if not uid:
        await interaction.response.send_message('Automatic public username lookup is unavailable. Re-run `/register` with the profile UID as the optional `uid` value. Registration is self-declared and does not verify account ownership.', ephemeral=True)
        return
    uid = uid.strip()
    if not valid_uid(uid):
        await interaction.response.send_message('That UID format is invalid. Enter the CODMunity profile UID (8–128 letters, numbers, _ or -).', ephemeral=True)
        return
    bot.store.register(str(interaction.user.id), username, uid)
    await interaction.response.send_message(f'Registered **{discord.utils.escape_markdown(username)}**. This is self-declared, not account ownership verification.', ephemeral=True)


@bot.tree.command(name='stats', description='Show available statistics for your registered profile')
@app_commands.describe(mode='Multiplayer, Battle Royale, Resurgence, or all available')
@app_commands.choices(mode=[app_commands.Choice(name='All available', value='all'), app_commands.Choice(name='Multiplayer', value='Multiplayer'), app_commands.Choice(name='Battle Royale', value='Battle Royale'), app_commands.Choice(name='Resurgence', value='Resurgence')])
async def stats_command(interaction: discord.Interaction, mode: str = 'all'):
    await interaction.response.defer(thinking=True)
    reg = _registration(interaction.user.id)
    if not reg:
        await interaction.followup.send('Use `/register` first.')
        return
    try:
        data = await bot.profile(reg['uid'])
    except CODMunityError as exc:
        await interaction.followup.send(f'CODMunity data is unavailable right now ({exc}). No stats were changed.')
        return
    modes = profile_modes(data)
    wanted = [(name, value) for name, value in modes.items() if mode.casefold() == 'all' or name.casefold() == mode.casefold()]
    if not wanted:
        await interaction.followup.send('No statistics for that mode were present in the available profile data.')
        return
    embed = discord.Embed(title=f"{reg['username']} · COD Stats", color=0xA82735)
    for name, value in wanted[:8]:
        lines = stat_lines(value)
        embed.add_field(name=name, value='\n'.join(lines) if lines else 'No supported statistics were returned.', inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name='recent', description='Show the latest available matches')
async def recent_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    reg = _registration(interaction.user.id)
    if not reg:
        await interaction.followup.send('Use `/register` first.')
        return
    try:
        data = await bot.profile(reg['uid'])
    except CODMunityError as exc:
        await interaction.followup.send(f'CODMunity data is unavailable right now ({exc}).')
        return
    rows = matches(data)[:10]
    await interaction.followup.send('\n'.join(f'• {match_summary(item)}' for item in rows) if rows else 'No match records were present in the available profile data.')


@bot.tree.command(name='leaderboard', description='Compare registered players on a shared statistic')
@app_commands.choices(category=[app_commands.Choice(name='Kills', value='kills'), app_commands.Choice(name='Wins', value='wins'), app_commands.Choice(name='K/D', value='kd')], mode=[app_commands.Choice(name='Multiplayer', value='Multiplayer'), app_commands.Choice(name='Battle Royale', value='Battle Royale'), app_commands.Choice(name='Resurgence', value='Resurgence')])
async def leaderboard_command(interaction: discord.Interaction, category: str = 'kills', mode: str = 'Multiplayer'):
    await interaction.response.defer(thinking=True)
    profiles = {}
    for reg in bot.store.registrations():
        try:
            profiles[reg['uid']] = await bot.profile(reg['uid'])
        except CODMunityError:
            continue
    rows = leaderboard_rows(bot.store.registrations(), profiles, mode, category)
    await interaction.followup.send(f"**{mode} · {category.upper()}**\n" + ('\n'.join(f'{i}. **{discord.utils.escape_markdown(n)}** — {v:g}' for i, (v, n) in enumerate(rows[:20], 1)) if rows else 'No players had this statistic available in the selected mode.'))


@bot.tree.command(name='compare', description='Compare two registered Discord members')
async def compare_command(interaction: discord.Interaction, player1: discord.Member, player2: discord.Member, mode: str = 'Multiplayer'):
    await interaction.response.defer(thinking=True)
    regs = [_registration(player1.id), _registration(player2.id)]
    if not all(regs):
        await interaction.followup.send('Both Discord members must register first.')
        return
    try:
        profiles = [await bot.profile(r['uid']) for r in regs]
    except CODMunityError as exc:
        await interaction.followup.send(f'CODMunity data is unavailable right now ({exc}).')
        return
    scopes = [profile_modes(p).get(mode) for p in profiles]
    if not all(scopes):
        await interaction.followup.send(f'Both profiles need available {mode} stats to compare.')
        return
    keys = [('Kills', ('kills', 'eliminations')), ('Deaths', ('deaths',)), ('K/D', ('kd', 'kdRatio', 'kdr')), ('Wins', ('wins',)), ('Losses', ('losses',))]
    lines = [f'**{mode}**']
    for label, aliases in keys:
        vals = [number(s, *aliases) for s in scopes]
        if all(v is not None for v in vals):
            lines.append(f'{label}: **{regs[0]["username"]} {vals[0]:g}** · **{regs[1]["username"]} {vals[1]:g}**')
    await interaction.followup.send('\n'.join(lines) if len(lines) > 1 else 'No shared statistics were available for this mode.')


@bot.tree.command(name='profile', description='Generate a Vintendo COD player card')
async def profile_command(interaction: discord.Interaction, mode: str = 'Multiplayer'):
    await interaction.response.defer(thinking=True)
    reg = _registration(interaction.user.id)
    if not reg:
        await interaction.followup.send('Use `/register` first.')
        return
    try:
        data = await bot.profile(reg['uid'])
    except CODMunityError as exc:
        await interaction.followup.send(f'CODMunity data is unavailable right now ({exc}).')
        return
    scoped = profile_modes(data).get(mode, {})
    latest = matches(data)
    summary = match_summary(latest[0]) if latest else 'No recent match data'
    path = make_card(reg['username'], mode, scoped, summary, Path(os.environ.get('BOT_CARD_DIR', 'data/cards')) / f'{interaction.user.id}.png')
    await interaction.followup.send(file=discord.File(path, filename='vintendo-player-card.png'))


def main():
    token = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
    if not token:
        raise SystemExit('Set DISCORD_BOT_TOKEN in .env before starting.')
    bot.run(token, log_handler=None)
