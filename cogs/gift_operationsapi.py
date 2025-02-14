import os
import json
import aiohttp
import asyncio
import sqlite3
import re
from datetime import datetime
import traceback
import discord

class GiftCodeAPI:
    def __init__(self, bot):
        self.bot = bot
        self.api_url = "https://wosland.com/apidc/giftapi/giftcode_api.php"
        self.api_key = "serioyun_gift_api_key_2024"
        self.check_interval = 300
        
        if hasattr(bot, 'conn'):
            self.conn = bot.conn
            self.cursor = self.conn.cursor()
        else:
            self.conn = sqlite3.connect('db/giftcode.sqlite')
            self.cursor = self.conn.cursor()
            
        self.settings_conn = sqlite3.connect('db/settings.sqlite')
        self.settings_cursor = self.settings_conn.cursor()
        
        self.users_conn = sqlite3.connect('db/users.sqlite')
        self.users_cursor = self.users_conn.cursor()
        
        asyncio.create_task(self.start_api_check())

    async def start_api_check(self):
        try:
            await asyncio.sleep(60)
            await self.sync_with_api()
            
            while True:
                await asyncio.sleep(self.check_interval)
                await self.sync_with_api()
        except Exception as e:
            traceback.print_exc()

    def __del__(self):
        try:
            self.conn.close()
            self.settings_conn.close()
            self.users_conn.close()
        except:
            pass
                
    async def sync_with_api(self):
        try:
            self.cursor.execute("SELECT giftcode, date FROM gift_codes")
            db_codes = {row[0]: row[1] for row in self.cursor.fetchall()}
            
            async with aiohttp.ClientSession() as session:
                headers = {
                    'X-API-Key': self.api_key,
                    'Content-Type': 'application/json'
                }
                
                async with session.get(self.api_url, headers=headers) as response:
                    response_text = await response.text()
                    
                    if response.status == 200:
                        try:
                            result = json.loads(response_text)
                            if 'error' in result:
                                return False
                            
                            api_giftcodes = result.get('codes', [])
                            
                            valid_codes = []
                            invalid_codes = []
                            for code_line in api_giftcodes:
                                parts = code_line.strip().split()
                                if len(parts) != 2:
                                    invalid_codes.append(code_line)
                                    continue
                                    
                                code, date_str = parts
                                if not re.match("^[a-zA-Z0-9]+$", code):
                                    invalid_codes.append(code_line)
                                    continue
                                    
                                try:
                                    date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                                    valid_codes.append((code, date_obj))
                                except ValueError:
                                    invalid_codes.append(code_line)
                                    continue
                            
                            if invalid_codes:
                                for invalid_code in invalid_codes:
                                    try:
                                        code = invalid_code.split()[0] if ' ' in invalid_code else invalid_code.strip()
                                        data = {'code': code}
                                        
                                        async with session.delete(self.api_url, json=data, headers=headers) as del_response:
                                            if del_response.status == 200:
                                                pass
                                            else:
                                                pass
                                        
                                        await asyncio.sleep(1)
                                        
                                    except Exception as e:
                                        traceback.print_exc()
                                
                                await asyncio.sleep(2)
                                async with session.get(self.api_url, headers=headers) as check_response:
                                    check_text = await check_response.text()
                            
                            new_codes = []
                            for code, date_obj in valid_codes:
                                formatted_date = date_obj.strftime("%Y-%m-%d")
                                if code not in db_codes:
                                    try:
                                        self.cursor.execute("INSERT OR IGNORE INTO gift_codes (giftcode, date) VALUES (?, ?)", (code, formatted_date))
                                        new_codes.append((code, formatted_date))
                                    except Exception as e:
                                        traceback.print_exc()

                            try:
                                self.conn.commit()

                                for code, formatted_date in new_codes:
                                    try:
                                        self.cursor.execute("SELECT alliance_id FROM giftcodecontrol WHERE status = 1")
                                        auto_alliances = self.cursor.fetchall() or []

                                        self.settings_cursor.execute("SELECT id FROM admin WHERE is_initial = 1")
                                        admin_ids = self.settings_cursor.fetchall()
                                        if admin_ids:
                                            admin_embed = discord.Embed(
                                                title="🎁 New Gift Code Found!",
                                                description=(
                                                    f"**Gift Code Details**\n"
                                                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                    f"🎁 **Code:** `{code}`\n"
                                                    f"📅 **Date:** `{formatted_date}`\n"
                                                    f"📝 **Status:** `Retrieved from Reloisback API`\n"
                                                    f"⏰ **Time:** <t:{int(datetime.now().timestamp())}:R>\n"
                                                    f"🔄 **Auto Alliance Count:** `{len(auto_alliances)}`\n"
                                                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                ),
                                                color=discord.Color.green()
                                            )

                                            for admin_id in admin_ids:
                                                try:
                                                    admin_user = await self.bot.fetch_user(admin_id[0])
                                                    if admin_user:
                                                        await admin_user.send(embed=admin_embed)
                                                except Exception as e:
                                                    traceback.print_exc()

                                        if auto_alliances:
                                            for alliance in auto_alliances:
                                                try:
                                                    gift_operations = self.bot.get_cog('GiftOperations')
                                                    if gift_operations:
                                                        await gift_operations.use_giftcode_for_alliance(alliance[0], code)
                                                        await asyncio.sleep(1)
                                                    else:
                                                        traceback.print_exc()
                                                except Exception as e:
                                                    traceback.print_exc()
                                    except Exception as e:
                                        traceback.print_exc()
                            except Exception as e:
                                traceback.print_exc()

                            for db_code, db_date in db_codes.items():
                                try:
                                    date_obj = datetime.strptime(db_date, "%Y-%m-%d")
                                    formatted_date = date_obj.strftime("%d.%m.%Y")
                                    
                                    data = {
                                        'code': db_code,
                                        'date': formatted_date
                                    }
                                    async with session.post(self.api_url, json=data, headers=headers) as post_response:
                                        if post_response.status == 409:
                                            pass
                                        elif post_response.status == 200:
                                            pass
                                        else:
                                            traceback.print_exc()
                                except Exception as e:
                                    traceback.print_exc()

                            return True
                            
                        except json.JSONDecodeError as e:
                            traceback.print_exc()
                            
        except Exception as e:
            traceback.print_exc()
            
    async def add_giftcode(self, giftcode: str) -> bool:
        try:
            self.cursor.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
            result = self.cursor.fetchone()
            
            if result:
                return False

            async with aiohttp.ClientSession() as session:
                headers = {
                    'Content-Type': 'application/json',
                    'X-API-Key': self.api_key
                }
                
                date_str = datetime.now().strftime("%d.%m.%Y")
                data = {
                    'code': giftcode,
                    'date': date_str
                }
                
                async with session.post(self.api_url, json=data, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        if result.get('success'):
                            self.cursor.execute("INSERT OR IGNORE INTO gift_codes (giftcode, date) VALUES (?, ?)", (giftcode, datetime.now().strftime("%Y-%m-%d")))
                            self.conn.commit()
                            return True
                        
                        return False
            
        except Exception as e:
            traceback.print_exc()
            return False
            
    async def remove_giftcode(self, giftcode: str, from_validation: bool = False) -> bool:
        try:
            if not from_validation:
                return False

            async with aiohttp.ClientSession() as session:
                headers = {
                    'Content-Type': 'application/json',
                    'X-API-Key': self.api_key
                }
                data = {'code': giftcode}
                
                async with session.delete(self.api_url, json=data, headers=headers) as response:
                    response_text = await response.text()
                    
                    if response.status == 200:
                        try:
                            result = json.loads(response_text)
                            success = 'success' in result
                            if success:
                                self.cursor.execute("DELETE FROM gift_codes WHERE giftcode = ?", (giftcode,))
                                self.cursor.execute("DELETE FROM user_giftcodes WHERE giftcode = ?", (giftcode,))
                                self.conn.commit()
                            else:
                                return False
                            return success
                        except json.JSONDecodeError as e:
                            traceback.print_exc()
                            return False
                    else:
                        return False
        except Exception as e:
            traceback.print_exc()
            return False
            
    async def check_giftcode(self, giftcode: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}?action=check&giftcode={giftcode}") as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get('exists', False)
            return False
        except Exception as e:
            traceback.print_exc()
            return False 

    async def validate_and_clean_giftcode_file(self):
        try:
            self.cursor.execute("SELECT giftcode FROM gift_codes")
            codes = self.cursor.fetchall()
            
            if not codes:
                return
                    
            for code_row in codes:
                code = code_row[0]
                status = await self.bot.get_cog('GiftOperations').claim_giftcode_rewards_wos("244886619", code)
                
                if status in ["TIME_ERROR", "CDK_NOT_FOUND", "USAGE_LIMIT"]:
                    await self.remove_giftcode(code, from_validation=True)
                    
                await asyncio.sleep(1)
                    
        except Exception as e:
            traceback.print_exc() 