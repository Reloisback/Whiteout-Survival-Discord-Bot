import discord
from discord.ext import commands, tasks
import hashlib
import time
import sqlite3
import aiohttp
from wcwidth import wcswidth
import asyncio
import ssl
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

conn = sqlite3.connect('gift_db.sqlite')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (fid INTEGER PRIMARY KEY, nickname TEXT, furnace_lv INTEGER DEFAULT 0)''')
conn.commit()

def load_settings():
    default_settings = {
        'BOT_TOKEN': '',
        'SECRET': 'tB87#kPtkxqOS2',
        'CHANNEL_ID': '',
        'ALLIANCE_NAME': 'RELOISBACK'
    }

    if not os.path.exists('settings.txt'):
        with open('settings.txt', 'w') as f:
            for key, value in default_settings.items():
                f.write(f"{key}={value}\n")

        print("settings.txt file has been created. Please fill in the file and restart the program.")
        exit()

    settings = {}
    with open('settings.txt', 'r') as f:
        for line in f:
            if '=' in line:
                key, value = line.strip().split('=', 1)
                settings[key] = value

    for key in default_settings:
        if not settings.get(key):
            print(f"{key} is missing from settings.txt. Please check the file.")
            exit()

    return settings

settings = load_settings()

BOT_TOKEN = settings['BOT_TOKEN']
SECRET = settings['SECRET']
CHANNEL_ID = int(settings['CHANNEL_ID'])
ALLIANCE_NAME = settings['ALLIANCE_NAME']

@bot.tree.command(name="addid", description= "Add new PlayerIDs. Usage: /allistadd <player_id, player_id>")
async def add_user(interaction:discord.Interaction, ids: str):
    added = []
    already_exists = []
    
    id_list = ids.split(',')

    total_ids = len(id_list) 
    for index, fid in enumerate(id_list):
        fid = fid.strip()
        if not fid:
            already_exists.append(f"{fid} - Empty ID provided")
            continue

        current_time = int(time.time() * 1000)
        form = f"fid={fid}&time={current_time}"
        sign = hashlib.md5((form + SECRET).encode('utf-8')).hexdigest()
        form = f"sign={sign}&{form}"

        url = 'https://wos-giftcode-api.centurygame.com/api/player'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        async with aiohttp.ClientSession() as session:
            while True:
                async with session.post(url, headers=headers, data=form, ssl=ssl_context) as response:
                    if response.status == 200:
                        data = await response.json()

                        if not data['data']:
                            already_exists.append(f"{fid} - No data found")
                            break 

                        if isinstance(data['data'], list) and data['data']:
                            nickname = data['data'][0]['nickname']
                            furnace_lv = data['data'][0].get('stove_lv', 0)
                        else:
                            nickname = data['data'].get('nickname', None)
                            furnace_lv = data['data'].get('stove_lv', 0)

                        if nickname:
                            c.execute("SELECT * FROM users WHERE fid=?", (fid,))
                            result = c.fetchone()

                            if result is None:
                                c.execute("INSERT INTO users (fid, nickname, furnace_lv) VALUES (?, ?, ?)", (fid, nickname, furnace_lv))
                                conn.commit()
                                added.append({
                                    'fid': fid,
                                    'nickname': nickname,
                                    'furnace_lv': furnace_lv
                                })
                                print(f"Added: {fid} - {nickname}") 
                            else:
                                already_exists.append(f"{fid} - Already exists")
                        else:
                            already_exists.append(f"{fid} - Nickname not found")
                        break 

                    elif response.status == 429:
                        print(f"Rate limit reached for {fid}. Waiting 1 minute...") 
                        await asyncio.sleep(60) 
                        continue 

                    else:
                        already_exists.append(f"{fid} - Request failed with status: {response.status}")
                        break  

    if added:
        embed = discord.Embed(
            title="Added People",
            description="The following users were successfully added:",
            color=discord.Color.green()
        )

        for user in added:
            embed.add_field(
                name=user['nickname'],
                value=f"Furnace Level: {user['furnace_lv']}\nID: {user['fid']}",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    if already_exists:
        embed = discord.Embed(
            title="Already Exists / No Data Found",
            description="\n".join(already_exists),
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

    msg_parts = []
    if added:
        msg_parts.append(f"Successfully added: {', '.join(added)}")
    if already_exists:
        msg_parts.append(f"Already exists or no data found: {', '.join(already_exists)}")
    
    for part in msg_parts:
        while len(part) > 2000:
            await interaction.followup.send(part[:2000]) 
            part = part[2000:]  
        if part: 
            await interaction.followup.send(part)




@bot.tree.command(name="removeid", description="Removes player IDs. Usage: /allistremove <player_id>")
async def remove_user(interaction:discord.Interaction, fid: int):
    c.execute("DELETE FROM users WHERE fid=?", (fid,))
    conn.commit()
    await interaction.response.send_message(f"ID {fid} removed from the list.")

@bot.tree.command(name="code", description="Starts process with manual giftcode")
async def use_giftcode(interaction:discord.Interaction, giftcode: str):
    notify_message = await interaction.response.send_message(
        "Alliance list is being checked for Gift Code usage, the process will be completed in approximately 4 minutes."
    )
    notify_message = await interaction.followup.send(
        content="https://tenor.com/view/typing-gif-3043127330471612038"
    )

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    c.execute("SELECT * FROM users")
    users = c.fetchall()
    success_results = []
    failure_results = []

    for index, user in enumerate(users):
        fid, nickname, furnace_lv = user
        current_time = int(time.time() * 1000)
        form = f"fid={fid}&cdk={giftcode}&time={current_time}"
        sign = hashlib.md5((form + SECRET).encode('utf-8')).hexdigest()
        form = f"sign={sign}&{form}"

        url = 'https://wos-giftcode-api.centurygame.com/api/giftcode/redeem'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        while True:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=form, ssl=ssl_context) as response:
                    if response.status == 200:
                        success_results.append(f"{fid} - {nickname} - USED")
                        break 
                    elif response.status == 429: 
                        print(f"Rate limit reached for {fid}. Waiting 1 minute...")  # Log to console
                        await asyncio.sleep(60) 
                        continue
                    else:
                        failure_results.append(f"{fid} - {nickname} - USED BEFORE")
                        break 

    await notify_message.delete()

    def chunk_results(results, chunk_size=25):
        for i in range(0, len(results), chunk_size):
            yield results[i:i + chunk_size]

    for chunk in chunk_results(success_results):
        success_embed = discord.Embed(
            title=f"{giftcode} Gift Code - Success",
            color=discord.Color.green()
        )
        success_embed.set_footer(text="Developer: Reloisback | These users have not redeemed the gift code before. Check your in-game mail")
        
        for result in chunk:
            success_embed.add_field(name=result, value="\u200b", inline=False)
        
        await interaction.followup.send(embed=success_embed)

    for chunk in chunk_results(failure_results):
        failure_embed = discord.Embed(
            title=f"{giftcode} Gift Code - Failed",
            color=discord.Color.red()
        )
        failure_embed.set_footer(text="Developer: Reloisback | These users have already redeemed the gift code.")
        
        for result in chunk:
            failure_embed.add_field(name=result, value="\u200b", inline=False)
        
        await interaction.followup.send(embed=failure_embed)

@bot.tree.command(name="listids", description="Lists all players")
async def show_users(interaction: discord.Interaction):
    c.execute("SELECT * FROM users ORDER BY furnace_lv DESC")
    users = c.fetchall()
    user_count = len(users)

    embed_title = f"{ALLIANCE_NAME} ALLIANCE LIST ({user_count} members)"

    max_name_len = max(wcswidth(fix_rtl(user[1])) for user in users)
    max_furnace_len = max(len(str(user[2])) for user in users) 
    max_id_len = max(len(str(user[0])) for user in users) 

    header = "Name".ljust(max_name_len) + " | Furnace Level".ljust(max_furnace_len + 1) + " | Game ID\n"
    header += "-" * (max_name_len + max_furnace_len + max_id_len + 6) + "\n"

    user_info = ""
    part_number = 1 

    for user in users:
        fid, nickname, furnace_lv = user
        formatted_nickname = fix_rtl(nickname) if any("\u0600" <= c <= "\u06FF" for c in nickname) else nickname
        line = formatted_nickname.ljust(max_name_len) + f" | {str(furnace_lv).ljust(max_furnace_len)} | {fid}\n"
        
        if len(user_info) + len(line) > 2000:
            embed = discord.Embed(
                title=embed_title if part_number == 1 else f"{ALLIANCE_NAME} ALLIANCE LIST (Part {part_number})",
                description=f"```{header}{user_info}```",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
            user_info = ""  
            part_number += 1 

        user_info += line

    if user_info:
        embed = discord.Embed(
            title=embed_title if part_number == 1 else f"{ALLIANCE_NAME} ALLIANCE LIST (Part {part_number})",
            description=f"```{header}{user_info}```",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)



@tasks.loop(seconds=60)
async def change_bot_status():
    global status_list, current_status_index
    if status_list:
        await bot.change_presence(activity=discord.Game(name=status_list[current_status_index]))
        current_status_index = (current_status_index + 1) % len(status_list)

@bot.tree.command(name="botstatus", description="Set the status of the bot")
async def set_bot_status(interaction:discord.Interaction):
    global status_list, current_status_index
    status_list = []
    current_status_index = 0

    await interaction.response.send_message("How many situations do you want to enter? (max. 3)")
    def check(msg):
        return msg.author == msg.author and msg.channel == msg.channel
    
    
    try:
        while True:

            msg = await bot.wait_for('message', check=check)
            num_statuses = int(msg.content)

            #followups cant be more than 5, so we need to set a limit here
            #could be fixed with a UI

            if num_statuses > 3:
                await interaction.followup.send("Sorry, cant be more than 3 at the moment") #
                break

            for i in range(num_statuses):
                await interaction.followup.send(f"{i + 1}. write down the situation:")
                msg = await bot.wait_for('message', check=check)
                status_list.append(msg.content)
                break

            await interaction.followup.send("If you want the states to change every how many seconds, write that down:")
            msg = await bot.wait_for('message', check=check)
            change_interval = int(msg.content)

            change_bot_status.change_interval(seconds=change_interval)
            change_bot_status.start()
            await interaction.followup.send(f"Bot status is set! It will switch every {change_interval} seconds.")
            break

    except ValueError:
        await interaction.followup.send("Please enter a valid number.")
    except Exception as e:
        await interaction.followup.send(f"Error occurred: {str(e)}")

status_list = []
current_status_index = 0

@tasks.loop(minutes=20)
async def auto_update_agslist():
    channel = bot.get_channel(CHANNEL_ID)
    await check_agslist(channel)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Bot is online as {bot.user}')
    channel = bot.get_channel(CHANNEL_ID)
    await check_agslist(channel)  
    auto_update_agslist.start()  
    countdown_timer.start() 

@bot.tree.command(name="update", description="Updates alliance list")
async def update_agslist(interaction:discord.Interaction):  
    await check_agslist(channel=interaction.channel) 
    await interaction.response.send_message("This could take a second. Take a seat")

@tasks.loop(minutes=1)
async def countdown_timer():
    next_run_in = auto_update_agslist.next_iteration - discord.utils.utcnow()
    minutes, seconds = divmod(next_run_in.total_seconds(), 60)
    print(f"Next update in {int(minutes)} minutes and {int(seconds)} seconds")

async def check_agslist(channel):
    print("The control started...") 
    c.execute("SELECT fid, nickname, furnace_lv FROM users")
    users = c.fetchall()

    furnace_changes = []
    nickname_changes = []

    url = 'https://wos-giftcode-api.centurygame.com/api/player'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    for index, user in enumerate(users):
        fid, old_nickname, old_furnace_lv = user
        
        current_time = int(time.time() * 1000)
        form = f"fid={fid}&time={current_time}"
        sign = hashlib.md5((form + SECRET).encode('utf-8')).hexdigest()
        form = f"sign={sign}&{form}"

        while True:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=form, ssl=False) as response:
                    if response.status == 200:
                        data = await response.json()
                        new_furnace_lv = data['data']['stove_lv']
                        new_nickname = data['data']['nickname'].strip()

                        if new_furnace_lv != old_furnace_lv:
                            c.execute("UPDATE users SET furnace_lv = ? WHERE fid = ?", (new_furnace_lv, fid))
                            conn.commit()
                            furnace_changes.append(f"{old_nickname}: {old_furnace_lv} -> {new_furnace_lv}")

                        if new_nickname.lower() != old_nickname.lower().strip():
                            c.execute("UPDATE users SET nickname = ? WHERE fid = ?", (new_nickname, fid))
                            conn.commit()
                            nickname_changes.append(f"{old_nickname} -> {new_nickname}")

                        break 

                    elif response.status == 429:
                        await asyncio.sleep(60)
                        continue 
                    else:
                        await channel.send(f"Error fetching data for user with ID {fid}. API response: {response.status}")
                        break

    if furnace_changes or nickname_changes:
        if furnace_changes:
            furnace_embed = discord.Embed(
                title="Furnace Level Changes",
                description="\n".join(furnace_changes),
                color=discord.Color.orange()
            )
            furnace_embed.set_footer(text="Reloisback")
            await channel.send(embed=furnace_embed)

        if nickname_changes:
            nickname_embed = discord.Embed(
                title="Nickname Changes",
                description="\n".join(nickname_changes),
                color=discord.Color.blue()
            )
            furnace_embed.set_footer(text="Reloisback")
            await channel.send(embed=nickname_embed)
    else:
        await channel.send("No changes detected")
        print("No change.")

    print("Control over!") 

@bot.tree.command(name='details', description="Detailed information and profile picture of a player")
async def user_info(interaction: discord.Interaction, fid: int):
    interaction.response.defer()
    current_time = int(time.time() * 1000)
    form = f"fid={fid}&time={current_time}"
    sign = hashlib.md5((form + SECRET).encode('utf-8')).hexdigest()
    form = f"sign={sign}&{form}"

    url = 'https://wos-giftcode-api.centurygame.com/api/player'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=form, ssl=ssl_context) as response:
            if response.status == 200:
                data = await response.json()
                embed = discord.Embed(title=data['data']['nickname'], color=0x00ff00)
                embed.add_field(name='ID', value=data['data']['fid'], inline=True)
                embed.add_field(name='Furnace Level', value=data['data']['stove_lv'], inline=True)
                embed.add_field(name='State', value=f"{data['data']['kid']}", inline=True)
                embed.set_image(url=data['data']['avatar_image'])
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(f"User with ID {fid} not found or an error occurred.")
bot.run(BOT_TOKEN)

# -------------------------------------------
# English Version:
# Hello, this bot was created by Reloisback on 18.10.2024 for Whiteout Survival users to use in their Discord channels for free.
# If you don't know how to use Python, feel free to add me as a friend on Discord (Reloisback) and I would be happy to help you.
# If you purchase a Windows server and still don't know how to set it up, and you want the bot to run 24/7, you can also contact me.
# I can provide free support and assist with the setup process.
# As I mentioned before, these codes are completely free, and I do not charge anyone.
# But if one day you would like to support me, here are my coin details:
# USDT Tron (TRC20): TC3y2crhRXzoQYhe3rMDNzz6DSrvtonwa3
# USDT Ethereum (ERC20): 0x60acb1580072f20f008922346a83a7ed8bb7fbc9
#
# I will never forget your support, and I will continue to develop such projects for free.
#
# Thank you.
#
# -------------------------------------------
# Türkçe Versiyon:
# Merhaba, bu bot Reloisback tarafından 18.10.2024 tarihinde Whiteout Survival kullanıcılarının Discord kanallarında kullanması için ücretsiz olarak yapılmıştır.
# Eğer Python kullanmayı bilmiyorsanız Discord üzerinden Reloisback arkadaş olarak ekleyerek bana ulaşabilirsiniz, size yardımcı olmaktan mutluluk duyarım.
# Eğer bir Windows sunucu satın alırsanız ve hala kurmayı bilmiyorsanız ve botun 7/24 çalışmasını istiyorsanız yine benimle iletişime geçebilirsiniz.
# Sizin için ücretsiz destek sağlayabilirim ve kurulumda yardımcı olabilirim.
# Tekrardan söylediğim gibi bu kodlar tamamen ücretsizdir ve hiç kimseden ücret talep etmiyorum.
# Fakat bir gün bana destek olmak isterseniz işte coin bilgilerim:
# USDT Tron (TRC20): TC3y2crhRXzoQYhe3rMDNzz6DSrvtonwa3
# USDT Ethereum (ERC20): 0x60acb1580072f20f008922346a83a7ed8bb7fbc9
#
# Desteklerinizi hiç bir zaman unutmayacağım ve bu tür projeleri ücretsiz şekilde geliştirmeye devam edeceğim.
#
# Teşekkürler.
# -------------------------------------------
