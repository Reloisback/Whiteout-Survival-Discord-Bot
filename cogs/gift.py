import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import hashlib
import json

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

class GiftCommand(commands.Cog):
    def __init__(self, bot, conn):
        self.bot = bot
        self.conn = conn
        self.c = conn.cursor()
        self.giftcode_check_loop.start()

    def encode_data(self, data):
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

    def get_stove_info_wos(self, player_id):
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
        data = self.encode_data(data_to_encode)

        response_stove_info = session.post(
            wos_player_info_url,
            headers=headers,
            data=data,
        )
        return session, response_stove_info

    def claim_giftcode_rewards_wos(self, player_id, giftcode):
        session, response_stove_info = self.get_stove_info_wos(player_id=player_id)
        if response_stove_info.json().get("msg") == "success":
            data_to_encode = {
                "fid": f"{player_id}",
                "cdk": giftcode,
                "time": f"{int(datetime.now().timestamp())}",
            }
            data = self.encode_data(data_to_encode)

            response_giftcode = session.post(
                wos_giftcode_url,
                data=data,
            )
            
            response_json = response_giftcode.json()
            print(f"Response for {player_id}: {response_json}")
            
            # Handle successful redemption
            if response_json.get("msg") == "SUCCESS":
                return session, "SUCCESS"

            # Handle already redeemed code
            elif response_json.get("msg") == "RECEIVED." and response_json.get("err_code") == 40008:
                return session, "ALREADY_RECEIVED"

            # Handle gift code not found
            elif response_json.get("msg") == "CDK NOT FOUND." and response_json.get("err_code") == 40014:
                return session, "CDK_NOT_FOUND"

            # Handle same type exchange (already received)
            elif response_json.get("msg") == "SAME TYPE EXCHANGE." and response_json.get("err_code") == 40011:
                return session, "ALREADY_RECEIVED"

            # Handle expired code
            elif response_json.get("msg") == "TIME ERROR." and response_json.get("err_code") == 40007:
                return session, "CODE_EXPIRED"

            # Handle unknown errors
            else:
                print(f"[ERROR] Unexpected response for {player_id}: {response_json}")
                return session, "ERROR"

    async def giftcode_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        self.c.execute("SELECT giftcode, date FROM gift_codes ORDER BY date DESC LIMIT 1")
        latest_code = self.c.fetchone()

        self.c.execute("SELECT giftcode, date FROM gift_codes")
        gift_codes = self.c.fetchall()

        return [
            app_commands.Choice(
                name=f"{code} - {date} {'(Most recently shared)' if (code, date) == latest_code else ''}",
                value=code
            )
            for code, date in gift_codes if current.lower() in code.lower()
        ][:25]

    @app_commands.command(name="gift", description="Use a gift code for all users in the alliance.")
    @app_commands.describe(giftcode="Choose a gift code")
    @app_commands.autocomplete(giftcode=giftcode_autocomplete)
    async def use_giftcode(self, interaction: discord.Interaction, giftcode: str):
        self.c.execute("SELECT 1 FROM admin WHERE id = ?", (interaction.user.id,))
        if not self.c.fetchone():
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        await interaction.response.defer()

        notify_message = await interaction.followup.send(
            content="Alliance list is being checked for Gift Code usage, the process will be completed in approximately 10 minutes."
        )
        await interaction.followup.send(content="https://tenor.com/view/typing-gif-3043127330471612038")

        self.c.execute("SELECT * FROM users")
        users = self.c.fetchall()
        success_results = []
        received_results = []
        error_count = 0

        for user in users:
            fid, nickname, _ = user

            self.c.execute(
                "SELECT status FROM user_giftcodes WHERE fid = ? AND giftcode = ?", 
                (fid, giftcode)
            )
            status = self.c.fetchone()
            
            if status:
                if status[0] in ["SUCCESS", "ALREADY_RECEIVED"]:
                    print(f"[INFO] User {fid} ({nickname}) has already used the code {giftcode}. Skipping.")
                    received_results.append(f"{fid} - {nickname} - ALREADY RECEIVED")
                    continue
                else:
                    print(f"[DEBUG] User {fid} has a different status for code {giftcode}: {status[0]}")

            try:
                _, response_status = self.claim_giftcode_rewards_wos(player_id=fid, giftcode=giftcode)
                
                if response_status == "SUCCESS":
                    success_results.append(f"{fid} - {nickname} - USED")
                    print(f"[SUCCESS] User {fid} ({nickname}) successfully used the code {giftcode}.")
                    self.c.execute(
                        """
                        INSERT INTO user_giftcodes (fid, giftcode, status) 
                        VALUES (?, ?, ?) 
                        ON CONFLICT(fid, giftcode) 
                        DO UPDATE SET status = excluded.status
                        """,
                        (fid, giftcode, "SUCCESS")
                    )
                elif response_status == "ALREADY_RECEIVED":
                    received_results.append(f"{fid} - {nickname} - ALREADY RECEIVED")
                    print(f"[INFO] User {fid} ({nickname}) had already received the code {giftcode}.")
                    self.c.execute(
                        """
                        INSERT INTO user_giftcodes (fid, giftcode, status) 
                        VALUES (?, ?, ?) 
                        ON CONFLICT(fid, giftcode) 
                        DO UPDATE SET status = excluded.status
                        """,
                        (fid, giftcode, "ALREADY_RECEIVED")
                    )
                elif response_status == "ERROR":
                    print(f"[ERROR] Error occurred for user {fid} ({nickname}) when using code {giftcode}.")
                    error_count += 1
                elif response_status == "CODE_EXPIRED":
                    print(f"[ERROR] Gift code {giftcode} is expired. Stopping process.")
                    await notify_message.delete()
                    await interaction.followup.send(
                        embed=discord.Embed(
                            title="Invalid Gift Code",
                            description="The gift code used is expired. Please check the gift code.",
                            color=discord.Color.red()
                        )
                    )
                    return
                elif response_status == "CDK_NOT_FOUND":
                    print(f"[ERROR] Gift code {giftcode} not found for user {fid} ({nickname}). Stopping process.")
                    await notify_message.delete()
                    await interaction.followup.send(
                        embed=discord.Embed(
                            title="Invalid Gift Code",
                            description="The gift code used is incorrect. Please check the gift code.",
                            color=discord.Color.red()
                        )
                    )
                    return

            except Exception as e:
                print(f"[EXCEPTION] Error processing user {fid} ({nickname}): {str(e)}")
                error_count += 1

        self.conn.commit()
        await notify_message.delete()

        if not success_results:
            embed = discord.Embed(
                title="Gift Code Usage",
                description=f"The gift code has already been used by everyone.",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Summary Information", 
                value=f"{len(received_results)} users have already used it.\n{error_count} users encountered an error.", 
                inline=False
            )
            await interaction.followup.send(embed=embed)
            return

        await self.send_embeds(interaction, giftcode, success_results, "Success", discord.Color.green(), "The gift code has been successfully used for these users.")
        
        if received_results or error_count > 0:
            summary_embed = discord.Embed(
                title="Summary Information",
                color=discord.Color.orange()
            )
            summary_embed.add_field(name="Already Used", value=f"{len(received_results)} users have already used it.", inline=False)
            summary_embed.add_field(name="Error", value=f"{error_count} users encountered an error.", inline=False)
            await interaction.followup.send(embed=summary_embed)

    async def use_giftcode_auto(self, giftcode: str):
        self.c.execute("SELECT * FROM users")
        users = self.c.fetchall()

        for user in users:
            fid = user[0]
            self.c.execute("SELECT 1 FROM used_codes WHERE fid = ? AND gift_code = ?", (fid, giftcode))
            if not self.c.fetchone():
                _, response_status = self.claim_giftcode_rewards_wos(player_id=fid, giftcode=giftcode)
                if response_status == "SUCCESS":
                    self.c.execute("INSERT INTO used_codes (fid, gift_code) VALUES (?, ?)", (fid, giftcode))
                self.conn.commit()

    async def send_embeds(self, interaction, giftcode, results, title_suffix, color, footer_text):
        if not results:
            return

        total_users = len(results)
        embed = discord.Embed(
            title=f"{giftcode} Gift Code - {title_suffix} ({total_users})",
            color=color,
            description="\n".join(results)
        )
        embed.set_footer(text=f"Developer: Reloisback | {footer_text}")
        await interaction.followup.send(embed=embed)

    @tasks.loop(minutes=60)
    async def giftcode_check_loop(self):
        print("Checking for new gift codes...")
        try:
            response = requests.get("https://raw.githubusercontent.com/Reloisback/test/main/gift_codes.txt")
            response.raise_for_status()
            gift_codes = response.text.splitlines()

            self.c.execute("SELECT id FROM admin")
            admin_ids = self.c.fetchall()

            github_gift_codes = set()

            for line in gift_codes:
                try:
                    code, date_str = line.split()
                    day, month, year = date_str.split('.')
                    formatted_date = f"{year}-{month}-{day}"
                    github_gift_codes.add(code)

                    self.c.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (code,))
                    if not self.c.fetchone():
                        self.c.execute(
                            "INSERT INTO gift_codes (giftcode, date) VALUES (?, ?)",
                            (code, formatted_date)
                        )
                        self.conn.commit()
                        print(f"New gift code found and added: {code}")

                        for admin_id in admin_ids:
                            try:
                                admin_user = await self.bot.fetch_user(admin_id[0])
                                await admin_user.send(
                                    embed=discord.Embed(
                                        title="New Gift Code Found!",
                                        description=(
                                            f"A new gift code **{code}** has been found.\n"
                                            f"Date: {formatted_date}\n"
                                            f"To use the code, simply type `/gift {code}` in the channel of the Discord server where the bot is.\n"
                                            f"Depending on the number of members, delivery of gifts may take between 1 to 10 minutes."
                                        ),
                                        color=discord.Color.blue()
                                    )
                                )
                            except Exception as e:
                                print(f"Error sending DM to admin {admin_id[0]}: {str(e)}")
                except ValueError:
                    print(f"The line is not in the correct format or is missing: {line}")

            self.c.execute("SELECT giftcode FROM gift_codes")
            db_gift_codes = {row[0] for row in self.c.fetchall()}

            codes_to_delete = db_gift_codes - github_gift_codes

            for code in codes_to_delete:
                self.c.execute("DELETE FROM gift_codes WHERE giftcode = ?", (code,))
                print(f"Deleted code from database: {code}")

            self.conn.commit()

        except requests.RequestException as e:
            print(f"Error downloading gift codes: {e}")

    def cog_unload(self):
        self.giftcode_check_loop.cancel()

async def setup(bot):
    await bot.add_cog(GiftCommand(bot, bot.conn))
