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
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter, Retry
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

conn = sqlite3.connect('gift_db.sqlite')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (fid INTEGER PRIMARY KEY, nickname TEXT, furnace_lv INTEGER DEFAULT 0)''')
conn.commit()
wos_player_info_url = "https://wos-giftcode-api.centurygame.com/api/player"
wos_giftcode_url = "https://wos-giftcode-api.centurygame.com/api/gift_code"
wos_giftcode_redemption_url = "https://wos-giftcode.centurygame.com"
wos_encrypt_key = "tB87#kPtkxqOS2"
retry_config = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429], 
    allowed_methods=["POST"]
)
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

@bot.command(name='allistadd')
async def add_user(ctx, ids: str):
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

        await ctx.send(embed=embed)

    if already_exists:
        embed = discord.Embed(
            title="Already Exists / No Data Found",
            description="\n".join(already_exists),
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    msg_parts = []
    if added:
        msg_parts.append(f"Successfully added: {', '.join(added)}")
    if already_exists:
        msg_parts.append(f"Already exists or no data found: {', '.join(already_exists)}")
    
    for part in msg_parts:
        while len(part) > 2000:
            await ctx.send(part[:2000]) 
            part = part[2000:]  
        if part: 
            await ctx.send(part)




@bot.command(name='allistremove')
async def remove_user(ctx, fid: int):
    c.execute("DELETE FROM users WHERE fid=?", (fid,))
    conn.commit()
    await ctx.send(f"ID {fid} removed from the list.")

def encode_data(data):
    secret = wos_encrypt_key
    sorted_keys = sorted(data.keys())
    encoded_data = "&".join(
        [
            f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}"
            for key in sorted_keys
        ]
    )
    sign = hashlib.md5(f"{encoded_data}{secret}".encode()).hexdigest()
    return {"sign": sign, **data}

def get_stove_info_wos(player_id):
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry_config))

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/x-www-form-urlencoded",
        "origin": wos_giftcode_redemption_url,
    }

    data_to_encode = {
        "fid": f"{player_id}",
        "time": f"{int(datetime.now().timestamp())}",
    }
    data = encode_data(data_to_encode)

    response_stove_info = session.post(
        wos_player_info_url,
        headers=headers,
        data=data,
    )
    return session, response_stove_info

def claim_giftcode_rewards_wos(player_id, giftcode):
    session, response_stove_info = get_stove_info_wos(player_id=player_id)
    if response_stove_info.json().get("msg") == "success":
        data_to_encode = {
            "fid": f"{player_id}",
            "cdk": giftcode,
            "time": f"{int(datetime.now().timestamp())}",
        }
        data = encode_data(data_to_encode)

        response_giftcode = session.post(
            wos_giftcode_url,
            data=data,
        )
        
        response_json = response_giftcode.json()
        print(f"Response for {player_id}: {response_json}")
        
        if response_json.get("msg") == "SUCCESS":
            return session, "SUCCESS"
        elif response_json.get("msg") == "RECEIVED." and response_json.get("err_code") == 40008:
            return session, "ALREADY_RECEIVED"
        else:
            return session, "ERROR"

@bot.command(name='gift')
async def use_giftcode(ctx, giftcode: str):
    await ctx.message.delete()

    notify_message = await ctx.send(
        content="Alliance list is being checked for Gift Code usage, the process will be completed in approximately 4 minutes."
    )
    notify_message = await ctx.send(
        content="https://tenor.com/view/typing-gif-3043127330471612038"
    )

    c.execute("SELECT * FROM users")
    users = c.fetchall()
    success_results = []
    received_results = []
    error_results = []

    for user in users:
        fid, nickname, _ = user
        try:
            _, response_status = claim_giftcode_rewards_wos(player_id=fid, giftcode=giftcode)
            
            if response_status == "SUCCESS":
                success_results.append(f"{fid} - {nickname} - USED")
            elif response_status == "ALREADY_RECEIVED":
                received_results.append(f"{fid} - {nickname} - ALREADY RECEIVED")
            else:
                error_results.append(f"{fid} - {nickname} - ERROR")
        except Exception as e:
            print(f"Error processing user {fid}: {str(e)}")
            error_results.append(f"{fid} - {nickname} - ERROR")

    await notify_message.delete()

    for chunk in chunk_results(success_results):
        success_embed = discord.Embed(
            title=f"{giftcode} Gift Code - Success",
            color=discord.Color.green()
        )
        success_embed.set_footer(text="Developer: Reloisback | These users have not redeemed the gift code before. Check your in-game mail")
        
        for result in chunk:
            success_embed.add_field(name=result, value="\u200b", inline=False)
        
        await ctx.send(embed=success_embed)

    for chunk in chunk_results(received_results):
        received_embed = discord.Embed(
            title=f"{giftcode} Gift Code - Already Received",
            color=discord.Color.orange()
        )
        received_embed.set_footer(text="Developer: Reloisback | These users have already received the gift code.")
        
        for result in chunk:
            received_embed.add_field(name=result, value="\u200b", inline=False)
        
        await ctx.send(embed=received_embed)

    for chunk in chunk_results(error_results):
        error_embed = discord.Embed(
            title=f"{giftcode} Gift Code - Error",
            color=discord.Color.red()
        )
        error_embed.set_footer(text="Developer: Reloisback | An error occurred for these users during gift code redemption.")
        
        for result in chunk:
            error_embed.add_field(name=result, value="\u200b", inline=False)
        
        await ctx.send(embed=error_embed)

def chunk_results(results, chunk_size=25):
    for i in range(0, len(results), chunk_size):
        yield results[i:i + chunk_size]

def fix_rtl(text):
    return f"\u202B{text}\u202C"

@bot.command(name='allist')
async def show_users(ctx):
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
            await ctx.send(embed=embed)
            user_info = ""  
            part_number += 1 

        user_info += line

    if user_info:
        embed = discord.Embed(
            title=embed_title if part_number == 1 else f"{ALLIANCE_NAME} ALLIANCE LIST (Part {part_number})",
            description=f"```{header}{user_info}```",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)



@tasks.loop(seconds=60)
async def change_bot_status():
    global status_list, current_status_index
    if status_list:
        await bot.change_presence(activity=discord.Game(name=status_list[current_status_index]))
        current_status_index = (current_status_index + 1) % len(status_list)

@bot.command(name='botstatus')
async def set_bot_status(ctx):
    global status_list, current_status_index
    status_list = []
    current_status_index = 0

    await ctx.send("How many situations do you want to enter?")

    try:
        def check(msg):
            return msg.author == ctx.author and msg.channel == ctx.channel

        msg = await bot.wait_for('message', check=check)
        num_statuses = int(msg.content)

        for i in range(num_statuses):
            await ctx.send(f"{i + 1}. write down the situation:")
            msg = await bot.wait_for('message', check=check)
            status_list.append(msg.content)

        await ctx.send("If you want the states to change every how many seconds, write that down:")
        msg = await bot.wait_for('message', check=check)
        change_interval = int(msg.content)

        change_bot_status.change_interval(seconds=change_interval)
        change_bot_status.start()
        await ctx.send(f"Bot status is set! It will switch every {change_interval} seconds.")

    except ValueError:
        await ctx.send("Please enter a valid number.")
    except Exception as e:
        await ctx.send(f"Error occurred: {str(e)}")

status_list = []
current_status_index = 0

@tasks.loop(minutes=20)
async def auto_update_agslist():
    channel = bot.get_channel(CHANNEL_ID)
    await check_agslist(channel)

@bot.event
async def on_ready():
    print(f'Bot is online as {bot.user}')
    channel = bot.get_channel(CHANNEL_ID)
    await check_agslist(channel)  
    auto_update_agslist.start()  
    countdown_timer.start() 

@bot.command(name='updateallist')
async def update_agslist(ctx):
    await ctx.message.delete()  
    await check_agslist(ctx.channel) 

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
        print("No change.")

    print("Control over!") 

@bot.command(name='w')
async def user_info(ctx, fid: int):
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
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"User with ID {fid} not found or an error occurred.")
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
