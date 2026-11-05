import os, time, asyncio, logging, random, json
import discord, wavelink, asyncpg
from discord import app_commands

logging.basicConfig(level=logging.INFO)
TOKEN=os.environ.get('DISCORD_TOKEN')
URI=os.environ.get('LAVALINK_URI','http://ashburn5.kerit.in:9011')
PASSWORD=os.environ.get('LAVALINK_PASSWORD')
DB=os.environ.get('DATABASE_URL')
if not TOKEN or not PASSWORD or not DB: raise RuntimeError('Set DISCORD_TOKEN, LAVALINK_PASSWORD and DATABASE_URL')
# Slash commands only need server and voice state events; no privileged intents.
intents=discord.Intents.none()
intents.guilds=True
intents.voice_states=True
class Vttea(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree=app_commands.CommandTree(self)
        self.started=time.time(); self.db=None; self.original={}; self.history={}; self.filters_by_guild={}
    async def setup_hook(self):
        self.db=await asyncpg.create_pool(DB, min_size=1, max_size=5, ssl='require')
        await self.db.execute('CREATE TABLE IF NOT EXISTS settings (guild_id BIGINT PRIMARY KEY, data JSONB NOT NULL DEFAULT \'{}\')')
        await self.tree.sync()
        self.loop.create_task(self.connect_lavalink())
    async def connect_lavalink(self):
        await self.wait_until_ready()
        try:
            await wavelink.Pool.connect(nodes=[wavelink.Node(uri=URI,password=PASSWORD)],client=self)
        except Exception:
            logging.exception('Failed to connect to Lavalink; check LAVALINK_URI and the node logs.')
    async def close(self):
        if self.db: await self.db.close()
        await super().close()
bot=Vttea()
async def settings(gid):
    value=await bot.db.fetchval('SELECT data FROM settings WHERE guild_id=$1',gid)
    return json.loads(value) if value else {}
async def save(gid,key,value):
    d=await settings(gid); d[key]=value
    await bot.db.execute('INSERT INTO settings(guild_id,data) VALUES($1,$2::jsonb) ON CONFLICT(guild_id) DO UPDATE SET data=EXCLUDED.data',gid,json.dumps(d))
async def respond(i,content,ephemeral=False):
    if i.response.is_done(): await i.followup.send(content,ephemeral=ephemeral)
    else: await i.response.send_message(content,ephemeral=ephemeral)
async def get_player(i,connect=False):
    if not i.guild or not isinstance(i.user,discord.Member): raise ValueError('Use this command in a server.')
    s=await settings(i.guild.id)
    if s.get('botchat') and i.channel_id != s['botchat']: raise ValueError('Use the configured bot channel.')
    if not i.user.voice or not i.user.voice.channel: raise ValueError('Join a voice channel first.')
    p=i.guild.voice_client
    if p and p.channel.id != i.user.voice.channel.id: raise ValueError('Join my voice channel first.')
    dj=s.get('dj')
    if dj and not i.user.guild_permissions.manage_guild and not any(r.id==dj for r in i.user.roles): raise ValueError('You need the configured DJ role or Manage Server permission.')
    if not p and connect:
        p=await i.user.voice.channel.connect(cls=wavelink.Player)
        await p.set_volume(int(s.get('defaultvolume',100)))
        if s.get('247'):
            await save(i.guild.id,'resume',{'channel':p.channel.id,'uri':None})
        default_filter=s.get('defaultfilter')
        if default_filter and default_filter != 'off':
            bot.filters_by_guild[i.guild.id]={default_filter: 0.3 if default_filter=='bassboost' else True}
            await apply_filters(p,i.guild.id)
        p.autoplay=wavelink.AutoPlayMode.enabled if s.get('defaultautoplay') else wavelink.AutoPlayMode.partial
    if not p: raise ValueError('Nothing is playing in this server.')
    return p
async def find_tracks(query):
    result=await wavelink.Playable.search(query)
    if not result: raise ValueError('No matching tracks were found.')
    return list(result.tracks) if isinstance(result,wavelink.Playlist) else [result[0]]
async def play_query(i,query,where='end'):
    await i.response.defer(thinking=True)
    p=await get_player(i,True); tracks=await find_tracks(query)
    if where=='top':
        for t in reversed(tracks): p.queue.put_at(0,t)
    elif where=='skip':
        for t in reversed(tracks): p.queue.put_at(0,t)
        if p.current: await p.skip(force=True)
    else: await p.queue.put_wait(tracks)
    if not p.playing: await p.play(p.queue.get())
    await respond(i,f'Added **{len(tracks)}** track(s): {tracks[0].title}')
@bot.event
async def on_ready():
    logging.info('Connected as %s in %s servers',bot.user,len(bot.guilds))

@bot.event
async def on_wavelink_node_ready(payload):
    logging.info('Lavalink node ready: %s', payload.node.identifier)
    if getattr(bot,'restored',False): return
    bot.restored=True
    for guild in bot.guilds:
        try:
            s=await settings(guild.id); snap=s.get('resume') or {}; channel=guild.get_channel(snap.get('channel')) if snap.get('channel') else None
            if channel and (s.get('autoresume') or s.get('247')) and not guild.voice_client:
                p=await channel.connect(cls=wavelink.Player)
                await p.set_volume(int(s.get('defaultvolume',100)))
                p.autoplay=wavelink.AutoPlayMode.enabled if s.get('defaultautoplay') else wavelink.AutoPlayMode.partial
                if s.get('autoresume') and snap.get('uri'):
                    tracks=await find_tracks(snap['uri']); await p.play(tracks[0])
        except Exception: logging.exception('Unable to restore server %s',guild.id)
@bot.event
async def on_wavelink_track_start(payload):
    if payload.player and payload.track:
        p=payload.player
        bot.history.setdefault(p.guild.id,[]).append(payload.track)
        s=await settings(p.guild.id)
        if s.get('autoresume') and payload.track.uri:
            await save(p.guild.id,'resume',{'channel':p.channel.id,'uri':payload.track.uri})
@bot.tree.error
async def errors(i,error):
    logging.error('Command error',exc_info=(type(error),error,error.__traceback__))
    e=getattr(error,'original',error)
    # An expired or already-acknowledged interaction cannot be safely answered again.
    if isinstance(e, discord.HTTPException) and e.code in (10062, 40060):
        logging.warning('Interaction expired or was already acknowledged; check for multiple bot instances or delayed responses.')
        return
    try:
        await respond(i,f'Could not complete that command: {str(e)[:500]}',True)
    except discord.HTTPException as response_error:
        if response_error.code in (10062, 40060):
            logging.warning('Could not send error response: interaction expired or already acknowledged.')
        else:
            logging.exception('Unable to send command error response')
@bot.tree.command(description='Show all available slash commands')
async def help(i:discord.Interaction):
    # Derive names and descriptions from the registered tree so this stays current.
    embed=discord.Embed(title='Vttea command guide', description='Use the full command shown below. Server settings require Manage Server permission.', color=0x08e2c4)
    standalone=[]
    for command in bot.tree.get_commands():
        children=getattr(command, 'commands', None)
        if children:
            lines=[f'`/{command.name} {child.name}` — {child.description}' for child in children]
            embed.add_field(name=command.name.capitalize(), value='\n'.join(lines), inline=False)
        else:
            standalone.append(f'`/{command.name}` — {command.description}')
    embed.insert_field_at(0,name='Info and music',value='\n'.join(standalone),inline=False)
    await i.response.send_message(embed=embed, ephemeral=True)
@bot.tree.command(description='Show bot latency')
async def ping(i:discord.Interaction): await respond(i,f'Gateway latency: {round(bot.latency*1000)} ms')
@bot.tree.command(description='Show bot uptime')
async def uptime(i:discord.Interaction): await respond(i,f'Online for {int(time.time()-bot.started)//60} minutes.')
@bot.tree.command(description='Show bot statistics')
async def stats(i:discord.Interaction): await respond(i,f'Servers: {len(bot.guilds)} · Voice connections: {len(bot.voice_clients)} · Latency: {round(bot.latency*1000)} ms')
@bot.tree.command(description='Play a search or supported link')
async def play(i:discord.Interaction,query:str): await play_query(i,query)
@bot.tree.command(description='Play a track next and skip the current track')
async def playskip(i:discord.Interaction,query:str): await play_query(i,query,'skip')
@bot.tree.command(description='Add a track to the top of the queue')
async def playtop(i:discord.Interaction,query:str): await play_query(i,query,'top')
@bot.tree.command(description='Skip the current track')
async def skip(i:discord.Interaction):
    p=await get_player(i); await p.skip(force=True); await respond(i,'Skipped.')
@bot.tree.command(description='Stop playback and disconnect')
async def stop(i:discord.Interaction):
    p=await get_player(i); p.queue.clear(); await save(i.guild.id,'resume',None); await p.disconnect(); await respond(i,'Stopped and disconnected.')
@bot.tree.command(description='Shuffle the current queue')
async def mix(i:discord.Interaction):
    p=await get_player(i); bot.original[i.guild.id]=list(p.queue); p.queue.shuffle(); await respond(i,'Queue shuffled.')
@bot.tree.command(description='Enable or disable automatic recommendations')
async def autoplay(i:discord.Interaction,enabled:bool):
    p=await get_player(i); p.autoplay=wavelink.AutoPlayMode.enabled if enabled else wavelink.AutoPlayMode.partial; await respond(i,f'Autoplay: {enabled}')
@bot.tree.command(description='Search for a related track and add it')
async def addrelated(i:discord.Interaction):
    p=await get_player(i)
    if not p.current: raise ValueError('No current track.')
    await play_query(i,f'{p.current.author} {p.current.title} related')
queue=app_commands.Group(name='queue',description='Manage the upcoming tracks')
bot.tree.add_command(queue)
@queue.command(name='list',description='Show the queue')
async def queue_list(i:discord.Interaction):
    p=await get_player(i); items=list(p.queue); await respond(i,'\n'.join(f'{n}. {t.title}' for n,t in enumerate(items[:20],1)) or 'Queue is empty.')
@queue.command(name='clear',description='Clear upcoming tracks')
async def queue_clear(i:discord.Interaction):
    p=await get_player(i); p.queue.clear(); await respond(i,'Queue cleared.')
@queue.command(name='shuffle',description='Shuffle upcoming tracks')
async def queue_shuffle(i:discord.Interaction):
    p=await get_player(i); bot.original[i.guild.id]=list(p.queue); p.queue.shuffle(); await respond(i,'Queue shuffled.')
@queue.command(name='unshuffle',description='Restore queue order after shuffle')
async def queue_unshuffle(i:discord.Interaction):
    p=await get_player(i); saved=bot.original.pop(i.guild.id,None)
    if saved is None: raise ValueError('No saved queue order.')
    current=list(p.queue); remaining=[t for t in saved if t in current]; remaining.extend(t for t in current if t not in remaining); p.queue.clear(); await p.queue.put_wait(remaining); await respond(i,'Queue order restored where possible.')
@queue.command(name='remove',description='Remove a track by position')
async def queue_remove(i:discord.Interaction,position:app_commands.Range[int,1]):
    p=await get_player(i); items=list(p.queue)
    if position>len(items): raise ValueError('Position is outside the queue.')
    t=items.pop(position-1); p.queue.clear(); await p.queue.put_wait(items); await respond(i,f'Removed {t.title}.')
@queue.command(name='move',description='Move a queued track')
async def queue_move(i:discord.Interaction,position:app_commands.Range[int,1],to:app_commands.Range[int,1]):
    p=await get_player(i); items=list(p.queue)
    if max(position,to)>len(items): raise ValueError('Position is outside the queue.')
    items.insert(to-1,items.pop(position-1)); p.queue.clear(); await p.queue.put_wait(items); await respond(i,'Track moved.')
@queue.command(name='jump',description='Jump to a track in the queue')
async def queue_jump(i:discord.Interaction,position:app_commands.Range[int,1]):
    p=await get_player(i)
    if position>len(p.queue): raise ValueError('Position is outside the queue.')
    for _ in range(position-1): p.queue.get()
    await p.skip(force=True); await respond(i,'Jumped to track.')
@queue.command(name='loop',description='Set looping mode')
@app_commands.choices(mode=[app_commands.Choice(name=x,value=x) for x in ['off','track','queue']])
async def queue_loop(i:discord.Interaction,mode:app_commands.Choice[str]):
    p=await get_player(i); p.queue.mode={'off':wavelink.QueueMode.normal,'track':wavelink.QueueMode.loop,'queue':wavelink.QueueMode.loop_all}[mode.value]; await respond(i,f'Loop: {mode.value}')
@queue.command(name='previous',description='Play the previous track')
async def queue_previous(i:discord.Interaction):
    p=await get_player(i); h=bot.history.get(i.guild.id,[])
    if len(h)<2: raise ValueError('No previous track.')
    p.queue.put_at(0,h[-2]); await p.skip(force=True); await respond(i,'Playing previous track.')
@queue.command(name='volume',description='Set volume from 0 to 150')
async def queue_volume(i:discord.Interaction,volume:app_commands.Range[int,0,150]):
    p=await get_player(i); await p.set_volume(volume); await respond(i,f'Volume: {volume}%')
@queue.command(name='status',description='Show playback status')
async def queue_status(i:discord.Interaction):
    p=await get_player(i); await respond(i,f'Playing: {p.current.title if p.current else "Nothing"} · Queue: {len(p.queue)} · Volume: {p.volume}% · Loop: {p.queue.mode.name}')
song=app_commands.Group(name='song',description='Control the current song')
bot.tree.add_command(song)
@song.command(name='nowplaying',description='Show the current track')
async def nowplaying(i:discord.Interaction):
    p=await get_player(i)
    if not p.current: raise ValueError('Nothing is playing.')
    await respond(i,f'**{p.current.title}** — {p.current.author}\n{p.current.uri or ""}')
@song.command(name='seek',description='Seek to a position in seconds')
async def seek(i:discord.Interaction,seconds:app_commands.Range[int,0]):
    p=await get_player(i); await p.seek(seconds*1000); await respond(i,f'Seeked to {seconds}s.')
@song.command(name='rewind',description='Rewind by seconds')
async def rewind(i:discord.Interaction,seconds:app_commands.Range[int,1]=10):
    p=await get_player(i); await p.seek(max(0,p.position-seconds*1000)); await respond(i,f'Rewound {seconds}s.')
@song.command(name='forward',description='Move forward by seconds')
async def forward(i:discord.Interaction,seconds:app_commands.Range[int,1]=10):
    p=await get_player(i); await p.seek(p.position+seconds*1000); await respond(i,f'Forward {seconds}s.')
@song.command(name='replay',description='Restart the current track')
async def replay(i:discord.Interaction):
    p=await get_player(i); await p.seek(0); await respond(i,'Restarted.')
@song.command(name='grab',description='Send the current track link privately')
async def grab(i:discord.Interaction):
    p=await get_player(i)
    if not p.current: raise ValueError('Nothing is playing.')
    await respond(i,f'{p.current.title}: {p.current.uri or "No link available"}',True)
@song.command(name='addend',description='Add the current track to the end of the queue')
async def addend(i:discord.Interaction):
    p=await get_player(i)
    if not p.current: raise ValueError('Nothing is playing.')
    await p.queue.put_wait(p.current); await respond(i,'Added the current track to the end.')
@song.command(name='lyrics',description='Find lyrics for the current track')
async def lyrics(i:discord.Interaction):
    import aiohttp
    p=await get_player(i)
    if not p.current: raise ValueError('Nothing is playing.')
    await i.response.defer(thinking=True)
    async with aiohttp.ClientSession() as session:
        async with session.get('https://lrclib.net/api/search',params={'track_name':p.current.title,'artist_name':p.current.author},timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status(); data=await response.json()
    text=next((x.get('plainLyrics') for x in data if x.get('plainLyrics')),None)
    await respond(i,text[:1900] if text else 'No lyrics found.')
filters=app_commands.Group(name='filter',description='Manage audio filters')
bot.tree.add_command(filters)
async def apply_filters(p,gid):
    active=bot.filters_by_guild.get(gid,{})
    f=wavelink.Filters()
    if 'bassboost' in active:
        f.equalizer.set(bands=[{'band':n,'gain':active['bassboost'] if n<3 else 0.0} for n in range(15)])
    if 'speed' in active: f.timescale.set(speed=active['speed'])
    if 'nightcore' in active: f.timescale.set(speed=1.2,pitch=1.2,rate=1.0)
    if 'vaporwave' in active: f.timescale.set(speed=0.8,pitch=0.8,rate=1.0)
    await p.set_filters(f)
@filters.command(name='add',description='Add a filter: bassboost, nightcore or vaporwave')
async def filter_add(i:discord.Interaction,name:str):
    p=await get_player(i); name=name.lower()
    if name not in ('bassboost','nightcore','vaporwave'): raise ValueError('Choose bassboost, nightcore or vaporwave.')
    bot.filters_by_guild.setdefault(i.guild.id,{})[name]=0.3 if name=='bassboost' else True
    await apply_filters(p,i.guild.id); await respond(i,f'Enabled {name}.')
@filters.command(name='remove',description='Remove a filter')
async def filter_remove(i:discord.Interaction,name:str):
    p=await get_player(i); bot.filters_by_guild.setdefault(i.guild.id,{}).pop(name.lower(),None); await apply_filters(p,i.guild.id); await respond(i,'Filter removed.')
@filters.command(name='clear',description='Clear all filters')
async def filter_clear(i:discord.Interaction):
    p=await get_player(i); bot.filters_by_guild[i.guild.id]={}; await p.set_filters(); await respond(i,'Filters cleared.')
@filters.command(name='list',description='List active filters')
async def filter_list(i:discord.Interaction): await respond(i,', '.join(bot.filters_by_guild.get(i.guild.id,{})) or 'No active filters.')
@filters.command(name='custombassboost',description='Set bass gain from 0 to 1')
async def custombassboost(i:discord.Interaction,gain:app_commands.Range[float,0.0,1.0]):
    p=await get_player(i); bot.filters_by_guild.setdefault(i.guild.id,{})['bassboost']=gain; await apply_filters(p,i.guild.id); await respond(i,f'Bass gain: {gain}')
@filters.command(name='customspeed',description='Set playback speed')
async def customspeed(i:discord.Interaction,speed:app_commands.Range[float,0.25,3.0]):
    p=await get_player(i); bot.filters_by_guild.setdefault(i.guild.id,{})['speed']=speed; await apply_filters(p,i.guild.id); await respond(i,f'Speed: {speed}')
setting=app_commands.Group(name='settings',description='Server administrator settings',default_permissions=discord.Permissions(manage_guild=True))
bot.tree.add_command(setting)
async def set_value(i,key,value):
    if not i.guild or not i.user.guild_permissions.manage_guild: raise ValueError('Manage Server permission required.')
    await save(i.guild.id,key,value); await respond(i,f'{key} updated.',True)
@setting.command(name='botchat',description='Restrict music commands to a channel; omit to disable')
async def botchat(i:discord.Interaction,channel:discord.TextChannel=None): await set_value(i,'botchat',channel.id if channel else None)
@setting.command(name='dj',description='Set the DJ role; omit to disable')
async def dj(i:discord.Interaction,role:discord.Role=None): await set_value(i,'dj',role.id if role else None)
@setting.command(name='defaultvolume',description='Set default volume')
async def defaultvolume(i:discord.Interaction,volume:app_commands.Range[int,0,150]): await set_value(i,'defaultvolume',volume)
@setting.command(name='defaultautoplay',description='Set default automatic recommendations')
async def defaultautoplay(i:discord.Interaction,enabled:bool): await set_value(i,'defaultautoplay',enabled)

@setting.command(name='defaultfilter',description='Set the default filter: off, bassboost, nightcore, or vaporwave')
async def defaultfilter(i:discord.Interaction,name:str):
    name=name.lower()
    if name not in ('off','bassboost','nightcore','vaporwave'): raise ValueError('Choose off, bassboost, nightcore or vaporwave.')
    await set_value(i,'defaultfilter',name)
@setting.command(name='autoresume',description='Resume the most recent track after a restart')
async def autoresume(i:discord.Interaction,enabled:bool):
    await set_value(i,'autoresume',enabled)
    if enabled and i.guild.voice_client and i.guild.voice_client.current:
        p=i.guild.voice_client
        await save(i.guild.id,'resume',{'channel':p.channel.id,'uri':p.current.uri})
@setting.command(name='247',description='Reconnect to the last voice channel after a restart')
async def twentyfourseven(i:discord.Interaction,enabled:bool):
    await set_value(i,'247',enabled)
    if enabled and i.guild.voice_client:
        p=i.guild.voice_client
        await save(i.guild.id,'resume',{'channel':p.channel.id,'uri':p.current.uri if p.current else None})

bot.run(TOKEN,log_handler=None)


