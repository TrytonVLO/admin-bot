import os
import re
import json
import base64
import asyncio
import discord
import logging
from systemd.journal import JournaldLogHandler
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

async def getMentionedUsers(ctx, db):
    # return all users mentioned individually and from ranks
    # if only word "all" in command - return all users in database
    users = []

    if ''.join(ctx.message.content.lower().split()[1:]) == "all":
        for userid in db["discords"]:
            users.append(await bot.fetch_user(int(userid)))
        return users

    users.extend(ctx.message.mentions)
    for rank in ctx.message.role_mentions:
        users.extend(rank.members)

    return users

# --------------- Initial setup ---------------

# Logs
# get an instance of the logger object this module will use
logger = logging.getLogger("adminbot")

# instantiate the JournaldLogHandler to hook into systemd
journald_handler = JournaldLogHandler()

# set a formatter to include the level name
journald_handler.setFormatter(logging.Formatter(
    '[%(levelname)s] %(asctime)s - %(message)s'
))

# add the journald handler to the current logger
logger.addHandler(journald_handler)

# optionally set the logging level
logger.setLevel(logging.INFO)
logging.info("Starting new session...")

# Queue
mainQueue = Tasker()    # for tasks that change users data (register, kill, password, etc..)
secondQueue = Tasker()  # for reading-only tasks (whois, etc..)

# Discord bot
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
bot = commands.Bot(command_prefix='$')

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
        await ctx.message.add_reaction('🛑')
        await ctx.send("Nie dla psa! Dla Adminów to!")
        return
    
    await ctx.message.add_reaction('⌛')
    await mainQueue.addJob(registerCoro(ctx))

async def registerCoro(ctx):
    for user in await getMentionedUsers(ctx, db):
        # check if user already exists
        if str(user.id) in db["discords"]:
            await ctx.message.add_reaction('⚠')
            await ctx.send(f"Ten użytkownik ma już konto: {db['discords'][str(user.id)]}")
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
            await ctx.message.add_reaction('📬')
            await ctx.send(f"Utworzono użytkownika: {out}")   
            newdata = recovery(user.id)
            embed=discord.Embed(title="Tryton", url="https://tryton.vlo.gda.pl", description="Sleep less, code more!", color=0x11ff00)
            embed.add_field(name="Utworzono dla Ciebie konto na serwerze Tryton", value="https://tryton.vlo.gda.pl", inline=False)
            embed.add_field(name="Login", value=f"```{out}```", inline=False)
            embed.add_field(name="Hasło", value=f"```{newdata[0]}```", inline=False)
            embed.add_field(name="Nazwa bazy danych", value=f"```db{out}```", inline=False)
            embed.add_field(name="Hasło bazy danych", value=f"```{newdata[1]}```", inline=False)
            embed.set_footer(text="Jeśli kiedyś zapomnisz hasła, użyj komendy $password")
            await user.send(embed=embed)
        else:
            await ctx.message.add_reaction('⚠')
            await ctx.send(f"Nie można utworzyć konta dla: {user}")
    
    await ctx.message.remove_reaction('⌛', bot.user)


@commands.cooldown(1,10)
@bot.command(help="Usuwa konto użytkownika wraz ze wszystkimi danymi")
async def kill(ctx):
    """ Remove account """
    if not isGod(ctx.author.id):
        await ctx.message.add_reaction('🛑')
        await ctx.send("Nie dla psa! Dla Adminów to!")
        return

    await ctx.message.add_reaction('⌛')
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
        except Exception as e:
            logging.exception(f"Exception while killing user: {e}")
            await ctx.message.add_reaction('⚠')
            await ctx.send(f"Nie udało się usunąć konta użytkownika {user.display_name}")

    # Remove by server username (s1, s2, etc..)
    for user in ctx.message.content.split()[1:]:
        try:
            if "@" not in user and user.lower() != "all":
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
        except Exception as e:
            logging.exception(f"Exception while killing user: {e}")
            await ctx.message.add_reaction('⚠')
            await ctx.send(f"Nie udało się usunąć konta {user}")
    
    await ctx.message.remove_reaction('⌛', bot.user)


@commands.cooldown(1,10)
@bot.command(help="Zmienia hasło użytkownika")
async def password(ctx):
    """ Reset caller's password """
    await ctx.message.add_reaction('⌛')
    await mainQueue.addJob(passwordCoro(ctx))

async def passwordReset(ctx, user, single=False):
    """ Reset provided user's password and send according message """
    try:
        newdata = recovery(user.id)

        logging.info(f"Resetted password: {db['discords'][str(user.id)]}")
        if single:
            await ctx.message.add_reaction('📬')
        embed=discord.Embed(title="Tryton", url="https://tryton.vlo.gda.pl", description="Sleep less, code more!", color=0x44ff00)
        embed.add_field(name="Przywracanie dostępu do konta", value="Twoje hasła zostały zresetowane", inline=False)
        embed.add_field(name="Nowe hasło", value=f"```{newdata[0]}```", inline=False)
        embed.add_field(name="Nowe hasło bazy danych", value=f"```{newdata[1]}```", inline=False)
        await user.send(embed=embed)
        await ctx.send(f"Pomyślnie ustawiono nowe hasła dla: {db['discords'][str(user.id)]}")
        #await ctx.author.send(f"Nowe hasło do przesyłania plików: `{newdata[0]}`\nNowe hasło do bazy danych: `{newdata[1]}`")
    except Exception as e:
        logging.exception(f"Password reset failed: {e}")
        if single:
            await ctx.message.add_reaction('❌')
            await ctx.send("Nie udało się zresetować hasła. Prawdopodobnie nie masz jeszcze konta na serwerze Tryton.")
        else:
            await ctx.send(f"Nie udało się zresetować hasła dla użytkownika {user.display_name}. Prawdopodobnie nie ma on jeszcze konta na serwerze Tryton.")

async def passwordCoro(ctx):
    if len(ctx.message.content.split()) == 1:
        # no mentioned users - reset author's password
        await passwordReset(ctx, ctx.author, single=True)
    else:
        # some mentions - check permissions
        if not isGod(ctx.author.id):
            await ctx.message.add_reaction('🛑')
            await ctx.send("Normalni użytkownicy nie mogą resetować haseł innych osób.\nJeżeli próbujesz zmienić swoje hasło to użyj samej komendy bez oznaczania nikogo.")
            return

        # reset by discord username
        for user in await getMentionedUsers(ctx, db):
            await passwordReset(ctx, user)

        # reset by server username (s1, s2, etc..)
        for user in ctx.message.content.split()[1:]:
            if "@" not in user and user.lower() != "all":
                found = False
                for i in db["discords"]:
                    if db["discords"][i]==user:
                        found = True
                        res = await bot.fetch_user(int(i))
                        await passwordReset(ctx, res)
                        break
                if not found:
                    await ctx.message.add_reaction('⚠')
                    await ctx.send(f"Użytkownik {user} nie istnieje.")

    await ctx.message.remove_reaction('⌛', bot.user)


@commands.cooldown(1,10)
@bot.command(help="Sprawdza, które konta są powiązane z danymi użytkownikami")
async def whois(ctx):
    """ Identify discord user by server username and vice versa """
    if not isGod(ctx.author.id):
        await ctx.message.add_reaction('🛑')
        await ctx.send("Nie dla psa! Dla Adminów to!")
        return

    await ctx.message.add_reaction('⌛')
    await secondQueue.addJob(whoisCoro(ctx))

async def whoisCoro(ctx):
    # check by discord username
    for user in await getMentionedUsers(ctx, db):
        try:
            nick = db["discords"][str(user.id)]
            perm="👑 Admin" if isGod(user.id) else "👨 Użytkownik"
            embed=discord.Embed(title=user.display_name, url=f"https://tryton.vlo.gda.pl/u/{nick}", description=perm)
            embed.add_field(name="Login na serwerze:", value=nick, inline=False)
            embed.add_field(name="Baza danych:", value=f"db{nick}", inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            logging.exception(f"Whois lookup failed: {e}")
            await ctx.message.add_reaction('⚠')
            await ctx.send(f"Użytkownik {user.display_name} nie posiada konta na serwerze.")

    # Check by server username (s1, s2, etc..)
    for user in ctx.message.content.split()[1:]:
        if "@" not in user and user.lower() != "all":
            found = False
            for i in db["discords"]:
                if db["discords"][i]==user:
                    res = await bot.fetch_user(int(i))
                    perm="👑 Admin" if isGod(i) else "👨 Użytkownik"
                    embed=discord.Embed(title=res.display_name, url=f"https://tryton.vlo.gda.pl/u/{user}", description=perm)
                    embed.add_field(name="Login na serwerze:", value=user, inline=False)
                    embed.add_field(name="Baza danych:", value=f"db{user}", inline=False)
                    await ctx.send(embed=embed)
                    found=True
                    break
            if not found:
                await ctx.message.add_reaction('⚠')
                await ctx.send(f"Użytkownik {user} nie istnieje.")

    await ctx.message.remove_reaction('⌛', bot.user)

@commands.cooldown(1,10)
@bot.command(help="Sprawdza, które konto należy do Ciebie")
async def whoami(ctx):
    """ Check which account is owned by user """

    await ctx.message.add_reaction('⌛')
    await secondQueue.addJob(whoamiCoro(ctx))

async def whoamiCoro(ctx):
    # check by author id
    user=ctx.author
    try:
        nick = db["discords"][str(user.id)]
        perm="👑 Admin" if isGod(user.id) else "👨 Użytkownik"
        embed=discord.Embed(title=ctx.author.display_name, url=f"https://tryton.vlo.gda.pl/u/{nick}", description=perm)
        embed.add_field(name="Login na serwerze:", value=nick, inline=False)
        embed.add_field(name="Baza danych:", value=f"db{nick}", inline=False)
        embed.set_footer(text="Jeśli zapomniałeś swoich haseł, wpisz $password")
        await ctx.send(embed=embed)
    except Exception as e:
        logging.exception(f"Whoami does not know who are You: {e}")
        await ctx.message.add_reaction('❌')
        await ctx.send(f"Nie utworzono dla Ciebie żadnego konta. Jeśli chcesz posiadać konto, skontaktuj się z administracją.")

    await ctx.message.remove_reaction('⌛', bot.user)

@commands.cooldown(1,10)
@bot.command(help="Pokazuje pełną listę użytkowników")
async def users(ctx):
    """ Show full users list """
    if not isGod(ctx.author.id):
        await ctx.message.add_reaction('🛑')
        await ctx.send("Nie dla psa! Dla Adminów to!")
        return
    await ctx.message.add_reaction('⌛')
    await secondQueue.addJob(usersCoro(ctx))

async def usersCoro(ctx):
    em=discord.Embed(title="Wykaz użytkowników",description="Oto wszyscy zarejestrowani na serwerze Tryton:")
    fields=0
    for i in db["discords"]:
        res = await bot.fetch_user(int(i))
        login=db["discords"][str(i)]
        em.add_field(name=res.display_name,value=f"https://tryton.vlo.gda.pl/u/{login}",inline=False)
        fields+=1
        if fields>=25:
            await ctx.send(embed=em)
            em=discord.Embed()
            fields=0
    await ctx.send(embed=em)
    await ctx.message.remove_reaction('⌛', bot.user)

@bot.event
async def on_command_error(ctx,error):
    await ctx.message.add_reaction('❌')
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send("Nie spamuj! Możesz ponownie użyć tej komendy dopiero za {:.1f}s".format(error.retry_after))
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("Nieprawidłowe polecenie. Wpisz `$help`, aby uzyskać listę dostępnych poleceń.")
    else:
        await ctx.send("Wystąpił problem, proszę skontaktuj się z administracją.")

def main():
    # Run queues and bot
    asyncio.get_event_loop().run_until_complete(mainQueue.start())
    asyncio.get_event_loop().run_until_complete(secondQueue.start())
    asyncio.get_event_loop().run_until_complete(bot.start(TOKEN))

if __name__ == "__main__":
    main()

    logging.warning("Execution ended (that shouldn't be possible)")
