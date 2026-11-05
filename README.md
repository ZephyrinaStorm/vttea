# Vttea Discord music bot

A Discord slash-command bot using discord.py, Wavelink 3 and a Lavalink v4 node. It includes PostgreSQL-backed server settings. The website is a separate Site.

## Before you start

- Create a Discord application and bot at https://discord.com/developers/applications. Reset/copy the bot token and keep it private.
- Invite the bot using the OAuth2 URL generator with `bot` and `applications.commands` scopes. Give it View Channels, Send Messages, Connect and Speak permissions.
- Create an empty GitHub repository and upload **the contents of this folder** (including `render.yaml`) to the repository root. Do not upload a token or password.

## Deploy on Render

1. In Render, create a new Blueprint and connect your GitHub repository. Select the repository and branch containing `render.yaml`.
2. Enter `DISCORD_TOKEN` and `LAVALINK_PASSWORD` when prompted. Do not put these in GitHub. The node URI defaults to `https://ashburn5.kerit.in:9011`; change `LAVALINK_URI` if needed.
3. Render provisions a paid background worker and paid PostgreSQL database as described in the Blueprint. Check the prices in Render before confirming.
4. Deploy and watch the worker logs. A successful connection logs the bot account and server count. Slash commands are globally synced on startup; Discord may take time to show them.

The node must be reachable from Render and support Lavalink v4 with a valid HTTPS certificate. Source availability and playlist resolution depend on that node and its plugins. Search text uses the node's YouTube search; paste source-specific URLs for other services. Playback of catalog services may depend on matching playable tracks from another source.

## Commands

`/help`, `/stats`, `/ping`, `/uptime`; `/play`, `/playskip`, `/playtop`, `/skip`, `/stop`, `/mix`, `/autoplay`, `/addrelated`; `/queue clear`, `jump`, `list`, `loop`, `move`, `previous`, `shuffle`, `unshuffle`, `remove`, `volume`, `status`; `/filter add`, `remove`, `clear`, `list`, `custombassboost`, `customspeed`; `/settings autoresume`, `botchat`, `defaultautoplay`, `defaultfilter`, `defaultvolume`, `dj`, `247`; `/song seek`, `rewind`, `replay`, `grab`, `lyrics`, `forward`, `addend`, `nowplaying`.

Discord requires unique command names. Commands with duplicate names are grouped under `/queue`, `/filter`, `/settings`, and `/song`. Server settings require Manage Server; the DJ role restricts playback commands. Users must be in the same voice channel as the bot. `/lyrics` queries LRCLIB. `/addrelated` searches for a related result rather than using a guaranteed recommendation API.

The 24/7 setting reconnects to the last saved voice channel after a process restart. Autoresume restores the last saved track from its beginning, not the exact timestamp or queue. A deliberate `/stop` clears the saved session. Queue and filter state are in memory and reset on restart. Default filter, autoplay, volume and other server settings persist in PostgreSQL.

## Troubleshooting

If startup fails, copy the error from Render logs, removing tokens, passwords, database URLs and other secrets before sharing. `401` from Lavalink usually indicates a bad password; TLS/DNS errors indicate the URI or node configuration needs checking. If commands are missing, check that the application was invited with `applications.commands` and allow time for global sync. If voice connects without audio, check Discord permissions, node connectivity and source plugin configuration.
