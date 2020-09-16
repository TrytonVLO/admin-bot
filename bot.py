import os
import re
import json
import asyncio
import discord
import logging
from discord.ext import commands
from dotenv import load_dotenv

from mm import MortalManager
from tasker import Tasker

# Config should be readonly!
def getConfig():
    with open ("conf.json","r") as f:
        return json.loads(f.read())

#def saveConfig():
#    with open ("conf.json","w") as f:
#        f.write(json.dumps(config))

# TODO: Add real database support
EMPTY_DB = {"discords": {}, "mortals": []}

def getDb():
    if not os.path.isfile("db.json"):
        logging.info("New database!")
        return EMPTY_DB
    with open ("db.json","r") as f:
        return json.loads(f.read())

def saveDb():
    with open ("db.json","w") as f:
        f.write(json.dumps(db))

def isGod(uid):
    return (str(uid) in config["userapi"]["admins"])

def recovery(discord_id):
    user=db["discords"][str(discord_id)]
    return serverManager.password_reset(user)

def getMentionedUsers(ctx):
    # return all users mentioned individually and from ranks
    users = []
    users.extend(ctx.message.mentions)
    for rank in ctx.message.role_mentions:
        users.extend(rank.members)

    return users

# --------------- Initial setup ---------------

# Logs
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, filename="/var/log/adminbot.log", filemode="w+")
logging.info("Starting new session...")

# Queue
mainQueue = Tasker()    # for tasks that change users data (register, kill, password, etc..)
secondQueue = Tasker()  # for reading-only tasks (whois, etc..)

# Discord bot
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
bot = commands.Bot(command_prefix='/')

# Other
config = getConfig()
db = getDb()
serverManager = MortalManager.from_save(config, db)

# --------------- Bot commands ---------------

@commands.cooldown(1,10)
@bot.command(help="Tworzy nowe konto użytkownika")
async def register(ctx):
    """ Create account """
    if not isGod(ctx.author.id):
        await ctx.send("Nie dla psa! Dla Adminów to!")
        return
    
    logging.info("register called")
    await mainQueue.addJob(registerCoro(ctx))

async def registerCoro(ctx):
    for user in getMentionedUsers(ctx):
        # check if user already exists
        if str(user.id) in db["discords"]:
            await ctx.send(f"Ten użytkownik ma już konto: {config['discords'][str(user.id)]}")
            continue

        out = None
        try:
            out = serverManager.create_mortal()
        except Exception as e:
            logging.exception(f"Exception while creating user: {e}")
            out = None
            pass    # TODO: Add exception handling

        if out:
            # Update db
            db["mortals"] = list(serverManager.mortals)
            db["discords"][str(user.id)] = out
            saveDb()

            # Message success
            logging.info(f"Created user: {out}")
            await ctx.send(f"Utworzono użytkownika: {out}")   
            newdata = recovery(user.id)
            await user.send(f"Utworzono dla Ciebie konto na serwerze Tryton!\nWięcej informacji: https://tryton.vlo.gda.pl/\nLogin: `{out}`\nHasło do przesyłania plików: `{newdata[0]}`\nHasło do bazy danych: `{newdata[1]}`")
        else:
            await ctx.send(f"Nie można utworzyć konta dla: {user}")


@commands.cooldown(1,10)
@bot.command(help="Usuwa konto użytkownika wraz ze wszystkimi danymi")
async def kill(ctx):
    """ Remove account """
    if not isGod(ctx.author.id):
        await ctx.send("Nie dla psa! Dla Adminów to!")
        return

    logging.info("kill called")
    await mainQueue.addJob(killCoro(ctx))

async def killCoro(ctx):
    # Remove by discord username
    for user in ctx.message.mentions:
        try:
            serverManager.remove_mortal(db["discords"][str(user.id)])

            # Update config
            db["mortals"] = list(serverManager.mortals)
            db["discords"].pop(str(user.id),None)
            saveDb()

            # Message success
            logging.info(f"Deleted user: {user.display_name}")
            await ctx.send(f"Usunięto konto: {user.display_name}")
        except:
            await ctx.send("Nie można usunąć konta")

    # Remove by server username (s1, s2, etc..)
    for user in ctx.message.content.split()[1:]:
        try:
            if "@" not in user:
                serverManager.remove_mortal(user)

                # Update db
                db["mortals"] = list(serverManager.mortals)
                for i in db["discords"]:
                    if db["discords"][i]==user:
                        db["discords"].pop(i)
                        break
                saveDb()

                # Message success
                logging.info(f"Removed user: {user}")
                await ctx.send(f"Usunięto konto: {user}")
        except:
            await ctx.send("Nie można usunąć konta")


@commands.cooldown(1,10)
@bot.command(help="Zmienia hasło użytkownika")
async def password(ctx):
    """ Reset caller's password """
    await mainQueue.addJob(passwordCoro(ctx))

async def passwordCoro(ctx):
    try:
        newdata = recovery(ctx.author.id)

        logging.info(f"Resetted password: {db['discords'][str(ctx.author.id)]}")
        await ctx.send(f"Pomyślnie ustawiono nowe hasła dla: {db['discords'][str(ctx.author.id)]}")
        await ctx.author.send(f"Nowe hasło do przesyłania plików: `{newdata[0]}`\nNowe hasło do bazy danych: `{newdata[1]}`")
    except:
        await ctx.send("Nie udało się zresetować hasła")


@commands.cooldown(1,10)
@bot.command(help="Sprawdza, które konta są powiązane z danymi użytkownikami")
async def whois(ctx):
    """ Identify discord user by server username and vice versa """
    if not isGod(ctx.author.id):
        await ctx.send("Nie dla psa! Dla Adminów to!")
        return

    logging.info("whois called")
    #await ctx.add_reaction('⌛')
    await secondQueue.addJob(whoisCoro(ctx))

async def whoisCoro(ctx):
    logging.info("Whois starting")
    # check by discord username
    for user in ctx.message.mentions:
        try:
            logging.info(f"Whois for {str(user.id)}")
            nick = db["discords"][str(user.id)]
            await ctx.send(nick)
        except:
            await ctx.send("Ten użytkownik nie posiada konta na serwerze.")

    # Check by server username (s1, s2, etc..)
    for user in ctx.message.content.split()[1:]:
        if "@" not in user:
            found = False
            for i in db["discords"]:
                if db["discords"][i]==user:
                    res = await bot.fetch_user(int(i))
                    await ctx.send(res.display_name)
                    found=True
                    break
            if not found:
                await ctx.send("Ten użytkownik nie istnieje.")
    
    logging.info("Whois ended")

def main():
    # Run queues and bot
    asyncio.get_event_loop().run_until_complete(mainQueue.start())
    asyncio.get_event_loop().run_until_complete(secondQueue.start())
    asyncio.get_event_loop().run_until_complete(bot.start(TOKEN))

if __name__ == "__main__":
    main()

logging.warning("Execution ended (that shouldn't be possible)")