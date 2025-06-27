import discord
from discord.ext import commands
from discord import app_commands,ui
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp as youtube_dl
import asyncio
import os
from dotenv import load_dotenv
import re

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

#spotify api
sp= spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                    client_id= SPOTIFY_CLIENT_ID,
                    client_secret= SPOTIFY_CLIENT_SECRET
))

#ytdl and ffmpeg
ytdl_format_options= {
    'format':'bestaudio/best',
    'outtmpl':'%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames':True,
    'noplaylist':True,
    'nocheckcertificate':True,
    'ignoreerrors':False,
    'logtostderr':False,
    'quiet':True,
    'nowarnings':True,
    'default_search':'auto',
    'source_address':'0.0.0.0'
}

ffmpeg_options = {
    'options':'-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


#bot setup
intents = discord.Intents.default()
intents.message_content = True
bot= commands.Bot(command_prefix=".",intents=intents)

#global variables
queues = {}
now_playing_msgs = {}
curr_players = {}

#YTDLsource class
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self,source,*,data, volume = 0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url')
        self.uploader = data.get('uploader')
        self.thumbnail = data.get('thumbnail')
        self.duration = data.get('duration')

    @classmethod
    async def from_url(cls,url,*,loop=None,stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda:ytdl.extract_info(url,download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            return cls(discord.FFmpegPCMAudio(filename,**ffmpeg_options),data=data)
        

#music control class
class MusicControlView(discord.ui.View):
    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="⏭️",style=discord.ButtonStyle.primary)
    async def skip(self,interaction:discord.Interaction,button:discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("Skipped!",ephemeral=True)
        else:
            await interaction.response.send_message("ayin onnum ilelo myre",ephemeral=True)

    @discord.ui.button(label="⏹️",style=discord.ButtonStyle.danger)
    async def stop(self,interaction:discord.Interaction,button:discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
            queues.pop(self.guild_id,None)
            msg = now_playing_msgs.pop(self.guild_id,None)
            if msg:
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass
            await interaction.response.send_message("ok bei, njan erngi ponn",ephemeral=True)
        else:
            await interaction.response.send_message("ayin njan ilelo myre",ephemeral=True)

    @discord.ui.button(label="▶️/⏸️",style=discord.ButtonStyle.secondary)
    async def pause_res(self,interaction:discord.Interaction,button:discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_playing():
                vc.pause()
                await interaction.response.send_message("paused",ephemeral=True)
            elif vc.is_paused():
                vc.resume()
                await interaction.response.send_message("resuming",ephemeral=True)
            else:
                await interaction.response.send_message("ayin onnum ilelo punde",ephemeral=True)
        else:
            await interaction.response.send_message("njan athil ilelo monne",ephemeral=True)
            

#update now playing embed
async def update_nowplaying(guild:discord.Guild,player:YTDLSource):
    embed = discord.Embed(
        title="Now Playing",
        description=f"[{player.title}]({player.url})",
        color=discord.Colour.brand_red()
    )
    if player.uploader:
        embed.add_field(name="Channel",value=player.uploader,inline=True)
    if player.duration:
        m,s = divmod(player.duration,60)
        embed.add_field(name="Duration",value=f"{m}:{s:02d}",inline=True)
    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)
    embed.set_footer(text="use buttons to control playback")


    view = MusicControlView(bot,guild.id)
    msg = now_playing_msgs.get(guild.id)
    if msg:
        try:
            await msg.edit(embed=embed,view=view)
        except discord.NotFound:
            None
    if not msg:         #to send in the 1st channel bot sent msgs
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                msg = await channel.send(embed=embed,view=view)
                now_playing_msgs[guild.id] = msg
                break

    
#autoplay next song
def play_next(guild:discord.Guild):
    vc = guild.voice_client
    guild_id = guild.id
    if queues.get(guild_id):
        next_player = queues[guild_id].pop(0)
        vc.play(next_player, after=lambda e: asyncio.run_coroutine_threadsafe(after_song(guild,next_player),bot.loop))
        asyncio.run_coroutine_threadsafe(update_nowplaying(guild,next_player),bot.loop)
    else:
        msg = now_playing_msgs.pop(guild_id,None)
        if msg:
            asyncio.run_coroutine_threadsafe(msg.delete(),bot.loop)


#after one song finishes
async def after_song(guild:discord.Guild,player:YTDLSource):
    play_next(guild)


#bot events and commands
@bot.event
async def on_ready():
    print(f'logged in as {bot.user}')

    try:
        synced = await bot.tree.sync()
        print(f"synced {len(synced)} slash cmds")
    except Exception as e:
        print(f"failed to sync cmds: {e}")

def check_queue(ctx,id):
    if queues.get(id):
        player = queues[id].pop(0)
        ctx.voice_client.play(player,after=lambda x=None: check_queue(ctx,id))

@bot.tree.command(name="play",description="plays a song")
@app_commands.describe(query="url/name of song")
async def play(interaction:discord.Interaction,query:str):
    """plays music from yt or spotify"""
    await interaction.response.defer()
    guild = interaction.guild
    guild_id = guild.id

    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.followup.send("VC kerr punde adyam")
    
    voice_client = guild.voice_client
    if not voice_client:
        channel = interaction.user.voice.channel
        await channel.connect()
        voice_client = interaction.guild.voice_client

    
    #for spotify url
    if "open.spotify.com/track" in query:
        track_id = query.split("/")[-1].split("?")[0]
        track = sp.track(track_id)
        query = f"{track['name']} {track['artists'][0]['name']}"

    #for yt url
    youtube_regex = r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+"
    if re.match(youtube_regex,query):
        search_term=query
    else:
        search_term = f"ytsearch:{query}"

    try:
        player = await YTDLSource.from_url(query,loop=bot.loop,stream=True)
    except Exception as e:
        await interaction.followup.send(f"error fetching audio: {e}")
        return
    
    
    if voice_client.is_playing():
        guild_id = interaction.guild.id
        if guild_id not in queues:
            queues[guild_id]=[]
        queues[guild_id].append(player)
        return await interaction.followup.send(f"Added to queue: **{player.title}**")
    
    voice_client.play(player, after=lambda e:asyncio.run_coroutine_threadsafe(after_song(guild,player),bot.loop))
    await update_nowplaying(guild,player)
    await interaction.followup.send(f"Now playing: **{player.title}**")

 
@bot.tree.command(name="queue",description="shows the present queue")
async def queue(interaction:discord.Interaction):
    """shows queue"""
    
    guild_id = interaction.guild.id
    if guild_id not in queues or not queues[guild_id]:
        return await interaction.followup.send("onnuila myre ayin",ephemeral=True)
    queue_list = [f"{idx+1}. {song.title}" for idx,song in enumerate(queues[guild_id])]
    queue_txt = "\n".join(queue_list)
    await interaction.followup.send(f"**current queue:**\n{queue_txt}")
    

bot.run(DISCORD_TOKEN)
