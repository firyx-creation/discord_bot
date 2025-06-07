import discord
from discord.ext import commands, tasks
from mcstatus import JavaServer
import socket
import asyncio
import os
from dotenv import load_dotenv  # <-- Import pour charger .env

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")  # <-- Token depuis .env

# --- Configuration ---
COMMAND_PREFIX = "!"
UPDATE_INTERVAL_SECONDS = 30  # Intervalle de mise à jour en secondes

# --- Intents ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


# Dictionnaire pour stocker les messages à mettre à jour en direct
# Format: {channel_id: {"message_id": int, "server_address": str, "message_object": discord.Message}}
live_update_messages = {}

# --- Helper Function: Obtenir le statut et créer l'Embed ---
async def get_minecraft_status_embed(server_address: str, loop: asyncio.AbstractEventLoop):
    """
    Interroge un serveur Minecraft et retourne un Embed avec son statut.
    Retourne None en cas d'erreur majeure.
    """
    try:
        server = await loop.run_in_executor(None, JavaServer.lookup, server_address)
        status = await loop.run_in_executor(None, server.status)

        motd_cleaned = status.description
        if isinstance(status.description, dict):
            motd_text_parts = []
            if 'text' in status.description: motd_text_parts.append(status.description['text'])
            if 'extra' in status.description:
                for part in status.description['extra']: motd_text_parts.append(part.get('text', ''))
            motd_cleaned = "".join(motd_text_parts).strip()
        motd_cleaned = "".join(c for c in motd_cleaned if c.isprintable() or c in '\n\r')
        for i in "0123456789abcdefklmnor": motd_cleaned = motd_cleaned.replace(f"§{i}", "")

        embed = discord.Embed(
            title=f"Statut en direct de {server_address}",
            color=discord.Color.green()
        )
        # Note: Si vous n'avez pas d'emoji personnalisé "online", remplacez par un emoji standard comme "🟢"
        embed.add_field(name="Statut", value="🟢 En ligne", inline=False) 
        embed.add_field(name="Joueurs", value=f"{status.players.online}/{status.players.max}", inline=True)
        embed.add_field(name="Ping", value=f"{status.latency:.2f} ms", inline=True)
        embed.add_field(name="Version", value=status.version.name, inline=False)
        embed.add_field(name="MOTD", value=f"```\n{motd_cleaned}\n```", inline=False)
        embed.set_footer(text=f"Dernière mise à jour") # Timestamp sera ajouté par l'édition du message
        return embed

    except (socket.gaierror, ConnectionRefusedError, socket.timeout, TimeoutError) as e:
        error_description = "Impossible de résoudre l'hôte." if isinstance(e, socket.gaierror) else "Serveur hors ligne ou ne répond pas."
        embed = discord.Embed(
            title=f"Statut en direct de {server_address}",
            description=f"🔴 {error_description}",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Dernière mise à jour")
        return embed
    except Exception as e:
        print(f"Erreur inattendue lors de la récupération du statut de {server_address}: {e}")
        embed = discord.Embed(
            title=f"Statut en direct de {server_address}",
            description="Une erreur est survenue lors de la récupération du statut.",
            color=discord.Color.dark_red()
        )
        embed.set_footer(text=f"Dernière mise à jour")
        return embed

# --- Tâche en arrière-plan pour mettre à jour les messages ---
@tasks.loop(seconds=UPDATE_INTERVAL_SECONDS)
async def update_live_mc_status():
    if not live_update_messages:
        return

    for channel_id, data in list(live_update_messages.items()):
        message_object = data.get("message_object")
        server_address = data["server_address"]

        if not message_object:
            try:
                channel = bot.get_channel(channel_id)
                if channel:
                    message_object = await channel.fetch_message(data["message_id"])
                    live_update_messages[channel_id]["message_object"] = message_object
                else:
                    print(f"Salon {channel_id} non trouvé. Suppression.")
                    del live_update_messages[channel_id]
                    continue
            except discord.NotFound:
                print(f"Message {data['message_id']} non trouvé dans {channel_id}. Suppression.")
                del live_update_messages[channel_id]
                continue
            except discord.Forbidden:
                print(f"Permissions manquantes pour fetch message dans {channel_id}. Suppression.")
                del live_update_messages[channel_id]
                continue
            except Exception as e:
                print(f"Erreur fetch_message {data['message_id']}: {e}")
                continue

        if message_object:
            new_embed = await get_minecraft_status_embed(server_address, bot.loop)
            if new_embed: # S'assurer que l'embed n'est pas None (même si notre fonction retourne toujours un embed)
                try:
                    new_embed.timestamp = discord.utils.utcnow()
                    await message_object.edit(embed=new_embed)
                except discord.NotFound:
                    print(f"Message {message_object.id} non trouvé (edit). Suppression.")
                    del live_update_messages[channel_id]
                except discord.Forbidden:
                    print(f"Permissions manquantes pour éditer {message_object.id}. Suppression.")
                    del live_update_messages[channel_id]
                except Exception as e:
                    print(f"Erreur édition message {message_object.id}: {e}")
        else: # Si message_object est toujours None après la tentative de fetch
            print(f"Objet message non trouvé pour salon {channel_id} après fetch. Suppression.")
            if channel_id in live_update_messages:
                del live_update_messages[channel_id]


@update_live_mc_status.before_loop
async def before_update_loop():
    await bot.wait_until_ready()
    print("La tâche de mise à jour automatique du statut MC est prête.")

# --- Commandes ---
@bot.command(name='startlivemc', help=f"Affiche le statut d'un serveur MC en direct. Usage: {COMMAND_PREFIX}startlivemc <ip_serveur>[:port]")
async def start_live_minecraft_status(ctx, *, server_address: str):
    if ctx.channel.id in live_update_messages:
        await ctx.send("Un suivi en direct est déjà actif dans ce salon. Utilisez `!stoplivemc` d'abord.")
        return

    placeholder_embed = discord.Embed(title=f"Chargement du statut pour {server_address}...", color=discord.Color.blue())
    message = await ctx.send(embed=placeholder_embed)

    live_update_messages[ctx.channel.id] = {
        "message_id": message.id,
        "server_address": server_address,
        "message_object": message
    }

    initial_embed = await get_minecraft_status_embed(server_address, bot.loop)
    if initial_embed:
        initial_embed.timestamp = discord.utils.utcnow()
        await message.edit(embed=initial_embed)

    if not update_live_mc_status.is_running():
        update_live_mc_status.start()
        print("Tâche de mise à jour automatique démarrée.")

    await ctx.send(f"Suivi en direct activé pour `{server_address}`. Le message sera mis à jour toutes les {UPDATE_INTERVAL_SECONDS} secondes.", delete_after=10)

@bot.command(name='stoplivemc', help="Arrête le suivi en direct du statut MC dans ce salon.")
async def stop_live_minecraft_status(ctx):
    if ctx.channel.id in live_update_messages:
        data = live_update_messages.pop(ctx.channel.id)
        message_object = data.get("message_object")

        if message_object:
            try:
                stopped_embed = discord.Embed(title=f"Suivi en direct arrêté pour {data['server_address']}", color=discord.Color.greyple())
                stopped_embed.timestamp = discord.utils.utcnow()
                await message_object.edit(embed=stopped_embed)
            except discord.NotFound:
                await ctx.send("Le message de suivi n'a pas été trouvé, mais le suivi est arrêté.")
            except discord.Forbidden:
                 await ctx.send("Permissions manquantes pour modifier le message de suivi, mais le suivi est arrêté.")
            except Exception as e:
                print(f"Erreur lors de l'édition du message d'arrêt: {e}")
        else:
             await ctx.send("Message de suivi non retrouvé, mais le suivi est arrêté pour ce salon.")

        await ctx.send("Suivi en direct arrêté pour ce salon.")

        if not live_update_messages and update_live_mc_status.is_running():
            update_live_mc_status.cancel()
            print("Tâche de mise à jour automatique arrêtée car plus aucun message n'est suivi.")
    else:
        await ctx.send("Aucun suivi en direct n'est actif dans ce salon.")

@bot.event
async def on_ready(): # MODIFIÉ ICI
    print(f'{bot.user.name} est connecté à Discord !')
    print(f'Préfixe des commandes : {COMMAND_PREFIX}')
    
    # Définir l'activité du bot
    activity_text = f"Cmds: {COMMAND_PREFIX}startlivemc | {COMMAND_PREFIX}stoplivemc"
    activity = discord.Game(name=activity_text)
    await bot.change_presence(activity=activity)
    print(f"Activité du bot définie sur : {activity_text}")

    if live_update_messages and not update_live_mc_status.is_running():
        print("Reprise du suivi des messages existants (si persistance implémentée).")


# --- Lancer le bot ---
if __name__ == "__main__":
    # Votre token est déjà dans la variable TOKEN, donc cette vérification est un peu redondante
    # mais ne fait pas de mal.
    if TOKEN == "VOTRE_TOKEN_DE_BOT_DISCORD_ICI" or not TOKEN : 
        print("ERREUR : Veuillez remplacer 'VOTRE_TOKEN_DE_BOT_DISCORD_ICI' par votre vrai token de bot.")
    else:
        try:
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            print("ERREUR : Token de bot invalide.")
        except Exception as e:
            print(f"Une erreur est survenue au lancement du bot : {e}")