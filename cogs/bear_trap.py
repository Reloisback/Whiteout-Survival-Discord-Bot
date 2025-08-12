import discord
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta
import pytz
import os
import asyncio
import json
import urllib.parse
import traceback

class BearTrap(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.db_path = 'db/beartime.sqlite'
        os.makedirs('db', exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS bear_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                timezone TEXT NOT NULL,
                description TEXT NOT NULL,
                notification_type INTEGER NOT NULL,
                mention_type TEXT NOT NULL,
                repeat_enabled INTEGER NOT NULL DEFAULT 0,
                repeat_minutes INTEGER DEFAULT 0,
                is_enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER NOT NULL,
                last_notification TIMESTAMP,
                next_notification TIMESTAMP
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER NOT NULL,
                notification_time INTEGER NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (notification_id) REFERENCES bear_notifications(id) ON DELETE CASCADE
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS bear_notification_embeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER NOT NULL,
                title TEXT,
                description TEXT,
                color INTEGER,
                image_url TEXT,
                thumbnail_url TEXT,
                footer TEXT,
                author TEXT,
                mention_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (notification_id) REFERENCES bear_notifications(id) ON DELETE CASCADE
            )
        """)

        try:
            self.cursor.execute("SELECT mention_message FROM bear_notification_embeds LIMIT 1")
        except sqlite3.OperationalError:
            self.cursor.execute("ALTER TABLE bear_notification_embeds ADD COLUMN mention_message TEXT")
        
        self.conn.commit()

    async def cog_load(self):

        self.notification_task = asyncio.create_task(self.check_notifications())

    async def cog_unload(self):

        if hasattr(self, 'notification_task'):
            self.notification_task.cancel()

    async def save_notification(self, guild_id: int, channel_id: int, start_date: datetime,
                              hour: int, minute: int, timezone: str, description: str,
                              created_by: int, notification_type: int, mention_type: str,
                              repeat_48h: bool, repeat_minutes: int = 0) -> int:
        try:
            embed_data = None
            notification_description = description

            if description.startswith("CUSTOM_TIMES:"):
                parts = description.split("|", 1)
                notification_description = description
                
                if len(parts) > 1 and "EMBED_MESSAGE:" in parts[1]:
                    if hasattr(self, 'current_embed_data'):
                        embed_data = self.current_embed_data
            elif "EMBED_MESSAGE:" in description:
                notification_description = "EMBED_MESSAGE:true"
                if hasattr(self, 'current_embed_data'):
                    embed_data = self.current_embed_data

            tz = pytz.timezone(timezone)
            next_notification = start_date.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
                tzinfo=tz
            )

            self.cursor.execute("""
                INSERT INTO bear_notifications 
                (guild_id, channel_id, hour, minute, timezone, description, notification_type,
                mention_type, repeat_enabled, repeat_minutes, created_by, next_notification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (guild_id, channel_id, hour, minute, timezone, notification_description, notification_type,
                  mention_type, 1 if repeat_48h else 0, repeat_minutes, created_by,
                  next_notification.isoformat()))
            
            notification_id = self.cursor.lastrowid

            if embed_data:
                await self.save_notification_embed(notification_id, embed_data)

            self.conn.commit()
            return notification_id
        except Exception as e:
            print(f"Error saving notification: {e}")
            raise

    async def save_notification_embed(self, notification_id: int, embed_data: dict) -> bool:
        try:
            self.cursor.execute("""
                INSERT INTO bear_notification_embeds 
                (notification_id, title, description, color, image_url, thumbnail_url, footer, author, mention_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                notification_id,
                embed_data.get('title'),
                embed_data.get('description'),
                int(embed_data.get('color', discord.Color.blue().value)),
                embed_data.get('image_url'),
                embed_data.get('thumbnail_url'),
                embed_data.get('footer'),
                embed_data.get('author'),
                embed_data.get('mention_message')
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error saving embed: {e}")
            return False

    async def get_notification_embed(self, notification_id: int) -> dict:
        try:
            self.cursor.execute("""
                SELECT title, description, color, image_url, thumbnail_url, footer, author, mention_message
                FROM bear_notification_embeds 
                WHERE notification_id = ?
            """, (notification_id,))
            
            result = self.cursor.fetchone()
            if result:
                return {
                    'title': result[0],
                    'description': result[1],
                    'color': result[2],
                    'image_url': result[3],
                    'thumbnail_url': result[4],
                    'footer': result[5],
                    'author': result[6],
                    'mention_message': result[7]
                }
            return None
        except Exception as e:
            print(f"Error getting embed: {e}")
            return None

    async def check_notifications(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:

                self.cursor.execute("""
                    SELECT * FROM bear_notifications 
                    WHERE is_enabled = 1 AND next_notification IS NOT NULL
                """)
                notifications = self.cursor.fetchall()

                now = datetime.now(pytz.UTC)
                for notification in notifications:
                    try:
                        await self.process_notification(notification)
                    except Exception as e:
                        print(f"Error processing notification {notification[0]}: {e}")
                        continue

            except Exception as e:
                print(f"Error in notification checker: {e}")

            await asyncio.sleep(0.1)

    async def process_notification(self, notification):
        try:
            (id, guild_id, channel_id, hour, minute, timezone, description,
             notification_type, mention_type, repeat_enabled, repeat_minutes,
             is_enabled, created_at, created_by, last_notification,
             next_notification) = notification
            
            if not is_enabled:
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                self.cursor.execute("""
                    UPDATE bear_notifications 
                    SET is_enabled = 0 
                    WHERE id = ?
                """, (id,))
                self.conn.commit()
                return

            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
            next_time = datetime.fromisoformat(next_notification)

            if next_time < now and repeat_enabled and repeat_minutes > 0:
                time_diff = (now - next_time).total_seconds() / 60
                periods_passed = int(time_diff / repeat_minutes) + 1
                next_time = next_time + timedelta(minutes=repeat_minutes * periods_passed)
                
                self.cursor.execute("""
                    UPDATE bear_notifications 
                    SET next_notification = ? 
                    WHERE id = ?
                """, (next_time.isoformat(), id))
                self.conn.commit()
                return

            time_until = next_time - now
            minutes_until = time_until.total_seconds() / 60

            if time_until.total_seconds() < -0.1:
                return

            notification_times = []
            
            if notification_type == 1:  
                notification_times = [30, 10, 5, 0]
            elif notification_type == 2:  
                notification_times = [10, 5, 0]
            elif notification_type == 3:  
                notification_times = [5, 0]
            elif notification_type == 4:  
                notification_times = [5]
            elif notification_type == 5:  
                notification_times = [0]
            elif notification_type == 6:  
                if description.startswith("CUSTOM_TIMES:"):
                    times_str = description.split("CUSTOM_TIMES:")[1].split("|")[0]
                    if ',' in times_str:
                        notification_times = [int(t.strip()) for t in times_str.split(',')]
                    else:
                        notification_times = [int(t.strip()) for t in times_str.split('-')]
                    description = description.split("|")[1]

            should_notify = False
            current_time = None

            for notify_time in notification_times:
                time_diff = abs(minutes_until - notify_time)
                if time_diff < 0.1:  
                    thirty_seconds_ago = (now - timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')
                    
                    self.cursor.execute("""
                        SELECT COUNT(*) FROM notification_history 
                        WHERE notification_id = ? 
                        AND notification_time = ? 
                        AND sent_at >= ?
                    """, (id, notify_time, thirty_seconds_ago))
                    
                    count = self.cursor.fetchone()[0]
                    if count == 0:  
                        should_notify = True
                        current_time = notify_time
                    break

            if should_notify:
                mention_text = ""
                if mention_type == "everyone":
                    mention_text = "@everyone"
                elif mention_type.startswith("role_"):
                    role_id = int(mention_type.split("_")[1])
                    role = channel.guild.get_role(role_id)
                    if role:
                        mention_text = role.mention
                    else:
                        mention_text = f"Role {role_id}"
                elif mention_type.startswith("member_"):
                    member_id = int(mention_type.split("_")[1])
                    member = await channel.guild.fetch_member(member_id)
                    if member:
                        mention_text = member.mention
                    else:
                        mention_text = f"Member {member_id}"

                rounded_time = round(minutes_until)
                
                if rounded_time == 1:
                    time_unit = "minute"
                elif rounded_time < 60:
                    time_unit = "minutes"
                elif rounded_time == 60:
                    rounded_time = 1
                    time_unit = "hour"
                elif rounded_time < 1440:
                    rounded_time = round(rounded_time / 60)
                    time_unit = "hours"
                elif rounded_time == 1440:
                    rounded_time = 1
                    time_unit = "day"
                else:
                    rounded_time = round(rounded_time / 1440)
                    time_unit = "days"

                time_text = f"{rounded_time} {time_unit}"

                if "EMBED_MESSAGE:" in description:
                    try:
                        embed_data = await self.get_notification_embed(id)
                        
                        if embed_data:
                            try:
                                embed = discord.Embed()
                                
                                try:
                                    color_value = embed_data.get("color")
                                    if color_value is not None:
                                        embed.color = int(color_value)
                                    else:
                                        embed.color = discord.Color.blue()
                                except (ValueError, TypeError):
                                    embed.color = discord.Color.blue()

                                title = embed_data.get("title", "")
                                if title and isinstance(title, str):
                                    title = title.replace("%t", time_text)
                                    title = title.replace("{time}", time_text)
                                    if "@tag" in title:
                                        title = title.replace("@tag", mention_text)
                                    embed.title = title

                                description = embed_data.get("description", "")
                                if description and isinstance(description, str):
                                    description = description.replace("%t", time_text)
                                    description = description.replace("{time}", time_text)
                                    if "@tag" in description:
                                        description = description.replace("@tag", mention_text)
                                    embed.description = description

                                image_url = embed_data.get("image_url", "")
                                if image_url and isinstance(image_url, str) and image_url.strip() and image_url.startswith(('http://', 'https://')):
                                    embed.set_image(url=image_url)

                                thumbnail_url = embed_data.get("thumbnail_url", "")
                                if thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.strip() and thumbnail_url.startswith(('http://', 'https://')):
                                    embed.set_thumbnail(url=thumbnail_url)

                                footer_text = embed_data.get("footer", "")
                                if footer_text and isinstance(footer_text, str):
                                    footer_text = footer_text.replace("%t", time_text)
                                    footer_text = footer_text.replace("{time}", time_text)
                                    if "@tag" in footer_text:
                                        footer_text = footer_text.replace("@tag", mention_text)
                                    embed.set_footer(text=footer_text)

                                author_text = embed_data.get("author", "")
                                if author_text and isinstance(author_text, str):
                                    author_text = author_text.replace("%t", time_text)
                                    author_text = author_text.replace("{time}", time_text)
                                    if "@tag" in author_text:
                                        author_text = author_text.replace("@tag", mention_text)
                                    embed.set_author(name=author_text)

                                if embed.to_dict():
                                    if mention_text:
                                        mention_message = embed_data.get("mention_message", "")
                                        if mention_message and "@tag" in mention_message:
                                            mention_message = mention_message.replace("@tag", mention_text)
                                            mention_message = mention_message.replace("%t", time_text)
                                            mention_message = mention_message.replace("{time}", time_text)
                                            await channel.send(mention_message)
                                        else:
                                            mention_text = mention_text.replace("%t", time_text)
                                            mention_text = mention_text.replace("{time}", time_text)
                                            await channel.send(mention_text)
                                    await channel.send(embed=embed)
                                else:
                                    if rounded_time > 0:
                                        await channel.send(f"{mention_text} ⏰ **Notification** will start in **{time_text}**!")
                                    else:
                                        await channel.send(f"{mention_text} ⏰ **Notification**")
                            except Exception as e:
                                print(f"Error creating embed: {e}")
                                if rounded_time > 0:
                                    await channel.send(f"{mention_text} ⏰ **Error sending embed notification** will start in **{time_text}**!")
                                else:
                                    await channel.send(f"{mention_text} ⏰ **Error sending embed notification**")
                    except Exception as e:
                        print(f"Error creating embed: {e}")
                        if rounded_time > 0:
                            await channel.send(f"{mention_text} ⏰ **Error sending embed notification** will start in **{time_text}**!")
                        else:
                            await channel.send(f"{mention_text} ⏰ **Error sending embed notification**")
                else:
                    actual_description = description
                    if description.startswith("CUSTOM_TIMES:"):
                        parts = description.split("|", 1)
                        if len(parts) > 1:
                            actual_description = parts[1]
                    
                    if actual_description.startswith("PLAIN_MESSAGE:"):
                        actual_description = actual_description.replace("PLAIN_MESSAGE:", "", 1)
                    
                    if "@tag" in actual_description or "%t" in actual_description or "{time}" in actual_description:
                        message = actual_description
                        if "@tag" in message:
                            message = message.replace("@tag", mention_text)
                        if "%t" in message:
                            message = message.replace("%t", time_text)
                        if "{time}" in message:
                            message = message.replace("{time}", time_text)
                        await channel.send(message)
                    else:
                        if rounded_time > 0:
                            await channel.send(f"{mention_text} ⏰ **{actual_description}** will start in **{time_text}**!")
                        else:
                            await channel.send(f"{mention_text} ⏰ **{actual_description}**")

                current_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
                self.cursor.execute("""
                    INSERT INTO notification_history (notification_id, notification_time, sent_at)
                    VALUES (?, ?, ?)
                """, (id, current_time, current_time_str))

                self.cursor.execute("""
                    UPDATE bear_notifications 
                    SET last_notification = ? 
                    WHERE id = ?
                """, (now.isoformat(), id))

                if rounded_time == 0:
                    if repeat_enabled and repeat_minutes > 0:
                        current_next = datetime.fromisoformat(next_notification)
                        next_time = current_next + timedelta(minutes=repeat_minutes)

                        self.cursor.execute("""
                            UPDATE bear_notifications 
                            SET next_notification = ? 
                            WHERE id = ?
                        """, (next_time.isoformat(), id))
                    else:
                        self.cursor.execute("""
                            UPDATE bear_notifications 
                            SET is_enabled = 0 
                            WHERE id = ?
                        """, (id,))
                
                self.conn.commit()
                
        except Exception as e:
            print(f"Error processing notification: {e}")
            import traceback
            traceback.print_exc()

    async def get_notifications(self, guild_id: int) -> list:
        try:
            self.cursor.execute("""
                SELECT * FROM bear_notifications 
                WHERE guild_id = ? 
                ORDER BY next_notification
            """, (guild_id,))
            return self.cursor.fetchall()
        except Exception as e:
            print(f"Error getting notifications: {e}")
            return []

    async def toggle_notification(self, notification_id: int, enabled: bool) -> bool:
        try:

            self.cursor.execute("""
                SELECT is_enabled FROM bear_notifications WHERE id = ?
            """, (notification_id,))
            result = self.cursor.fetchone()
            if not result:
                return False

            self.cursor.execute("""
                UPDATE bear_notifications 
                SET is_enabled = ? 
                WHERE id = ?
            """, (1 if enabled else 0, notification_id))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error toggling notification: {e}")
            return False

    def get_world_times(self):
        current_utc = datetime.now(pytz.UTC)
        times = {
            "UTC": current_utc,
            "US/Pacific": current_utc.astimezone(pytz.timezone('US/Pacific')),
            "US/Eastern": current_utc.astimezone(pytz.timezone('US/Eastern')),
            "Europe/London": current_utc.astimezone(pytz.timezone('Europe/London')),
            "Europe/Istanbul": current_utc.astimezone(pytz.timezone('Europe/Istanbul')),
            "Asia/Tokyo": current_utc.astimezone(pytz.timezone('Asia/Tokyo')),
        }
        return times
    async def show_bear_trap_menu(self, interaction: discord.Interaction):
        try:
            times = self.get_world_times()
            time_display = "\n".join([
                f"🌍 **{zone}:** {time.strftime('%H:%M:%S')}"
                for zone, time in times.items()
            ])
            
            embed = discord.Embed(
                title="🐻 Bear Trap System",
                description=(
                    "Configure time notification settings:\n\n"
                    "**Current World Times**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{time_display}\n\n"
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⏰ **Set Time**\n"
                    "└ Configure notification time\n"
                    "└ Not just for Bear! Use it for any event:\n"
                    "   Bear - KE - Forst - CJ and everything else\n"
                    "└ Add unlimited notifications\n\n"
                    "🗑️ **Remove Notification**\n"
                    "└ Delete unwanted notifications\n\n"
                    "✅ **Enable/Disable**\n"
                    "└ Toggle notifications\n\n"
                    "📋 **View Settings**\n"
                    "└ Check current configuration\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.gold()
            )

            embed.set_footer(text="Last Updated")
            embed.timestamp = datetime.now()
            
            view = BearTrapView(self)
            
            try:
                await interaction.response.edit_message(embed=embed, view=view)
            except discord.InteractionResponded:
                pass
                
        except Exception as e:
            print(f"Error in show_bear_trap_menu: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred. Please try again.",
                    ephemeral=True
                )

    async def check_admin(self, interaction: discord.Interaction) -> bool:
        try:
            conn = sqlite3.connect('db/settings.sqlite')
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM admin WHERE id = ?", (interaction.user.id,))
            is_admin = cursor.fetchone() is not None
            conn.close()
            
            if not is_admin:
                await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
                return False
            return True
        except Exception as e:
            print(f"Error in admin check: {e}")
            return False

    async def show_channel_selection(self, interaction: discord.Interaction, start_date, hour, minute, timezone, message_data, channels):
        try:
            embed = discord.Embed(
                title="📢 Select Channel",
                description=(
                    "Choose a channel to send notifications:\n\n"
                    "Select a text channel from the dropdown menu below.\n"
                    "Make sure the bot has permission to send messages in the selected channel."
                ),
                color=discord.Color.blue()
            )

            view = ChannelSelectView(
                self,
                start_date,
                hour,
                minute,
                timezone,
                message_data,
                interaction.message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )

        except Exception as e:
            print(f"Error in show_channel_selection: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing channel selection!",
                ephemeral=True
            )

class RepeatOptionView(discord.ui.View):
    def __init__(self, cog, start_date, hour, minute, timezone, description, channel_id, notification_type, mention_type, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.description = description
        self.channel_id = channel_id
        self.notification_type = notification_type
        self.mention_type = mention_type
        self.original_message = original_message

    @discord.ui.button(label="No Repeat", style=discord.ButtonStyle.danger, custom_id="no_repeat")
    async def no_repeat_button(self, interaction, button):
        await self.save_notification(interaction, False)

    @discord.ui.button(label="Custom Interval", style=discord.ButtonStyle.primary, custom_id="custom_interval")
    async def custom_interval_button(self, interaction, button):
        modal = RepeatIntervalModal(self)
        await interaction.response.send_modal(modal)

    async def save_notification(self, interaction, repeat, repeat_minutes=0, interval_text=None):
        try:
            notification_id = await self.cog.save_notification(
                guild_id=interaction.guild_id,
                channel_id=self.channel_id,
                start_date=self.start_date,
                hour=self.hour,
                minute=self.minute,
                timezone=self.timezone,
                description=self.description,
                created_by=interaction.user.id,
                notification_type=self.notification_type,
                mention_type=self.mention_type,
                repeat_48h=repeat,
                repeat_minutes=repeat_minutes
            )

            notification_types = {
                1: "Sends notifications at 30 minutes, 10 minutes, 5 minutes before and when time's up",
                2: "Sends notifications at 10 minutes, 5 minutes before and when time's up",
                3: "Sends notifications at 5 minutes before and when time's up",
                4: "Sends notification only 5 minutes before",
                5: "Sends notification only when time's up",
                6: "Sends notifications at custom times"
            }

            if self.mention_type == "everyone":
                mention_display = "@everyone"
            elif self.mention_type.startswith("role_"):
                role_id = int(self.mention_type.split('_')[1])
                role = interaction.guild.get_role(role_id)
                mention_display = f"@{role.name}" if role else f"Role: {role_id}"
            elif self.mention_type.startswith("member_"):
                member_id = int(self.mention_type.split('_')[1])
                member = interaction.guild.get_member(member_id)
                mention_display = f"@{member.display_name}" if member else f"Member: {member_id}"
            else:
                mention_display = "No Mention"

            if not repeat:
                repeat_text = "❌ No repeat"
            elif interval_text:
                repeat_text = f"🔄 Repeats every {interval_text}"
            else:
                minutes = repeat_minutes
                if minutes == 1:
                    repeat_text = "🔄 Repeats every minute"
                elif minutes == 60:
                    repeat_text = "🔄 Repeats every hour"
                elif minutes == 1440:
                    repeat_text = "🔄 Repeats daily"
                elif minutes == 2880:
                    repeat_text = "🔄 Repeats every 2 days"
                elif minutes == 4320:
                    repeat_text = "🔄 Repeats every 3 days"
                elif minutes == 10080:
                    repeat_text = "🔄 Repeats weekly"
                else:
                    repeat_text = f"🔄 Repeats every {minutes} minutes"

            embed = discord.Embed(
                title="✅ Notification Set Successfully",
                description=(
                    f"**📅 Date:** {self.start_date.strftime('%d/%m/%Y')}\n"
                    f"**⏰ Time:** {self.hour:02d}:{self.minute:02d} {self.timezone}\n"
                    f"**📢 Channel:** <#{self.channel_id}>\n"
                    f"**📝 Description:** {self.description.split('|')[-1] if '|' in self.description else self.description}\n\n"
                    f"**⚙️ Notification Type**\n{notification_types[self.notification_type]}\n\n"
                    f"**👥 Mentions:** {mention_display}\n"
                    f"**🔄 Repeat:** {repeat_text}"
                ),
                color=discord.Color.green()
            )

            embed.set_footer(text="Created at")
            embed.timestamp = datetime.now()
            
            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=None
            )

        except Exception as e:
            print(f"Error saving notification: {e}")
            await interaction.followup.send(
                "❌ An error occurred while saving the notification.",
                ephemeral=True
            )

class RepeatIntervalModal(discord.ui.Modal):
    def __init__(self, repeat_view: RepeatOptionView):
        super().__init__(title="Set Repeat Interval")
        self.repeat_view = repeat_view
        
        self.months = discord.ui.TextInput(
            label="Months",
            placeholder="Enter number of months (e.g., 1)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )
        
        self.weeks = discord.ui.TextInput(
            label="Weeks",
            placeholder="Enter number of weeks (e.g., 2)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )
        
        self.days = discord.ui.TextInput(
            label="Days",
            placeholder="Enter number of days (e.g., 3)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )
        
        self.hours = discord.ui.TextInput(
            label="Hours",
            placeholder="Enter number of hours (e.g., 12)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )
        
        self.minutes = discord.ui.TextInput(
            label="Minutes",
            placeholder="Enter number of minutes (e.g., 30)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )

        for item in [self.months, self.weeks, self.days, self.hours, self.minutes]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            try:
                months = int(self.months.value)
                weeks = int(self.weeks.value)
                days = int(self.days.value)
                hours = int(self.hours.value)
                minutes = int(self.minutes.value)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Please enter valid numbers for all fields!",
                    ephemeral=True
                )
                return

            if not any([months > 0, weeks > 0, days > 0, hours > 0, minutes > 0]):
                await interaction.response.send_message(
                    "❌ Please enter at least one time interval greater than 0!",
                    ephemeral=True
                )
                return

            total_minutes = (months * 30 * 24 * 60) + (weeks * 7 * 24 * 60) + (days * 24 * 60) + (hours * 60) + minutes

            interval_parts = []
            if months > 0:
                interval_parts.append(f"{months} month{'s' if months > 1 else ''}")
            if weeks > 0:
                interval_parts.append(f"{weeks} week{'s' if weeks > 1 else ''}")
            if days > 0:
                interval_parts.append(f"{days} day{'s' if days > 1 else ''}")
            if hours > 0:
                interval_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
            if minutes > 0:
                interval_parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
            
            if len(interval_parts) > 1:
                interval_text = ", ".join(interval_parts[:-1]) + " and " + interval_parts[-1]
            else:
                interval_text = interval_parts[0]
            
            await self.repeat_view.save_notification(interaction, True, total_minutes, interval_text)

        except Exception as e:
            print(f"Error in repeat interval modal: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while setting the repeat interval.",
                ephemeral=True
            )

class TextInputModal(discord.ui.Modal):
    def __init__(self, title, label, placeholder, default_value="", max_length=None, style=discord.TextStyle.short):
        super().__init__(title=title)
        self.value = None
        self.input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            default=default_value,
            max_length=max_length,
            style=style
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        self.value = self.input.value
        await interaction.response.defer()

class EmbedEditorView(discord.ui.View):
    def __init__(self, cog, start_date, hour, minute, timezone, original_message):
        super().__init__(timeout=None)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.original_message = original_message
        self.embed_data = {
            "title": "⏰ Bear Trap",
            "description": "Add a description...",
            "color": discord.Color.blue().value,
            "footer": "Bear Trap Notification System",
            "author": "Bear Trap",
            "mention_message": ""
        }

    async def update_embed(self, interaction: discord.Interaction):
        try:
            example_time = "30 minutes"
            embed = discord.Embed(color=self.embed_data.get("color", discord.Color.blue().value))
            
            if "title" in self.embed_data:
                title = self.embed_data["title"].replace("%t", example_time).replace("{time}", example_time)
                embed.title = title
            if "description" in self.embed_data:
                description = self.embed_data["description"].replace("%t", example_time).replace("{time}", example_time)
                embed.description = description
            if "footer" in self.embed_data:
                footer = self.embed_data["footer"].replace("%t", example_time).replace("{time}", example_time)
                embed.set_footer(text=footer)
            if "author" in self.embed_data:
                author = self.embed_data["author"].replace("%t", example_time).replace("{time}", example_time)
                embed.set_author(name=author)
            if "image_url" in self.embed_data and self.embed_data["image_url"]:
                embed.set_image(url=self.embed_data["image_url"])
            if "thumbnail_url" in self.embed_data and self.embed_data["thumbnail_url"]:
                embed.set_thumbnail(url=self.embed_data["thumbnail_url"])

            mention_preview = self.embed_data.get('mention_message', '@tag')
            if mention_preview:
                mention_preview = mention_preview.replace("%t", example_time)
                mention_preview = mention_preview.replace("{time}", example_time)

            content = (
                "📝 **Embed Editor**\n\n"
                "**Note:** \n"
                "• Use `%t` or `{time}` to show the remaining time\n"
                "• Use `@tag` for mentions (will be replaced with the actual mention)\n"
                "• You can use these in title, description, footer, and author fields\n"
                "• Time will automatically show with appropriate units (minutes/hours/days)\n\n"
                f"Currently showing '{example_time}' as an example.\n\n"
                f"**Current Mention Message Preview:**\n"
                f"{mention_preview}\n\n"               
            )

            if not interaction.response.is_done():
                await interaction.response.edit_message(content=content, embed=embed, view=self)
            else:
                await interaction.followup.edit_message(message_id=interaction.message.id, content=content, embed=embed, view=self)
            
        except Exception as e:
            print(f"Error updating embed: {e}")
            try:
                await interaction.followup.send("❌ An error occurred while updating the embed!", ephemeral=True)
            except:
                pass

    @discord.ui.button(label="Mention Message", style=discord.ButtonStyle.secondary, row=1)
    async def edit_mention_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Mention Message",
                label="Mention Message",
                placeholder="Example: Hey @tag time! (@tag will be replaced with the actual mention)",
                default_value=self.embed_data.get("mention_message", ""),
                max_length=2000
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                self.embed_data["mention_message"] = modal.value
                await self.update_embed(interaction)
                
        except Exception as e:
            print(f"Error in edit_mention_message: {e}")
            await interaction.followup.send("❌ An error occurred while editing the mention message!", ephemeral=True)

    @discord.ui.button(label="Title", style=discord.ButtonStyle.primary, row=0)
    async def edit_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Title",
                label="New Title",
                placeholder="Example: ⏰ Bear starts in {time}!",
                default_value=self.embed_data.get("title", ""),
                max_length=256
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                self.embed_data["title"] = modal.value
                await self.update_embed(interaction)
                
        except Exception as e:
            print(f"Error in edit_title: {e}")
            await interaction.followup.send("❌ An error occurred while editing the title!", ephemeral=True)

    @discord.ui.button(label="Description", style=discord.ButtonStyle.primary, row=0)
    async def edit_description(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Description",
                label="New Description",
                placeholder="Example: Get ready for Bear! Only {time} remaining.",
                default_value=self.embed_data.get("description", ""),
                max_length=4000,
                style=discord.TextStyle.paragraph
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                self.embed_data["description"] = modal.value
                await self.update_embed(interaction)
                
        except Exception as e:
            print(f"Error in edit_description: {e}")
            await interaction.followup.send("❌ An error occurred while editing the description!", ephemeral=True)

    @discord.ui.button(label="Color", style=discord.ButtonStyle.success, row=0)
    async def edit_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            current_color = self.embed_data.get('color', discord.Color.blue().value)
            current_hex = f"#{hex(current_color)[2:].zfill(6)}"
            
            modal = TextInputModal(
                title="Color Code",
                label="Hex Color Code",
                placeholder="#FF0000",
                default_value=current_hex,
                max_length=7
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                try:
                    hex_value = modal.value.strip('#')
                    color_value = int(hex_value, 16)
                    self.embed_data["color"] = color_value
                    await self.update_embed(interaction)
                except ValueError:
                    await interaction.followup.send("❌ Invalid color code! Example: #FF0000", ephemeral=True)
                    
        except Exception as e:
            print(f"Error in edit_color: {e}")
            await interaction.followup.send("❌ An error occurred while editing the color!", ephemeral=True)

    @discord.ui.button(label="Footer", style=discord.ButtonStyle.secondary, row=1)
    async def edit_footer(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Footer",
                label="Footer Text",
                placeholder="Example: Bear Trap Notification System",
                default_value=self.embed_data.get("footer", ""),
                max_length=2048
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                self.embed_data["footer"] = modal.value
                await self.update_embed(interaction)
                
        except Exception as e:
            print(f"Error in edit_footer: {e}")
            await interaction.followup.send("❌ An error occurred while editing the footer!", ephemeral=True)

    @discord.ui.button(label="Author", style=discord.ButtonStyle.secondary, row=1)
    async def edit_author(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Author",
                label="Author Text",
                placeholder="Example: Bear Trap",
                default_value=self.embed_data.get("author", ""),
                max_length=256
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                self.embed_data["author"] = modal.value
                await self.update_embed(interaction)
                
        except Exception as e:
            print(f"Error in edit_author: {e}")
            await interaction.followup.send("❌ An error occurred while editing the author!", ephemeral=True)

    @discord.ui.button(label="Add Image", style=discord.ButtonStyle.secondary, row=2)
    async def add_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Image URL",
                label="Image URL",
                placeholder="https://example.com/image.png",
                default_value=self.embed_data.get("image_url", ""),
                max_length=1000
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                if not modal.value.startswith(('http://', 'https://')):
                    await interaction.followup.send("❌ Invalid URL! URL must start with 'http://' or 'https://'.", ephemeral=True)
                    return

                self.embed_data["image_url"] = modal.value
                await self.update_embed(interaction)
                
        except Exception as e:
            print(f"Error in add_image: {e}")
            await interaction.followup.send("❌ An error occurred while adding the image!", ephemeral=True)

    @discord.ui.button(label="Add Thumbnail", style=discord.ButtonStyle.secondary, row=2)
    async def add_thumbnail(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Thumbnail URL",
                label="Thumbnail URL",
                placeholder="https://example.com/thumbnail.png",
                default_value=self.embed_data.get("thumbnail_url", ""),
                max_length=1000
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if modal.value:
                if not modal.value.startswith(('http://', 'https://')):
                    await interaction.followup.send("❌ Invalid URL! URL must start with 'http://' or 'https://'.", ephemeral=True)
                    return

                self.embed_data["thumbnail_url"] = modal.value
                await self.update_embed(interaction)
                
        except Exception as e:
            print(f"Error in add_thumbnail: {e}")
            await interaction.followup.send("❌ An error occurred while adding the thumbnail!", ephemeral=True)

    @discord.ui.button(label="Confirm ✅", style=discord.ButtonStyle.green, row=3)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            self.cog.current_embed_data = self.embed_data

            embed_data = "EMBED_MESSAGE:true"

            await self.cog.show_channel_selection(
                interaction,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                embed_data,
                interaction.guild.text_channels
            )
            
        except Exception as e:
            print(f"Error in confirm button: {e}")
            try:
                await interaction.followup.send(
                    "❌ An error occurred while confirming the embed! Please try again.",
                    ephemeral=True
                )
            except:
                pass

    @discord.ui.button(label="Import Embed", style=discord.ButtonStyle.secondary, emoji="📥", row=2)
    async def import_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = ImportEmbedModal(self)
            await interaction.response.send_modal(modal)
        except Exception as e:
            print(f"Error showing import modal: {e}")
            await interaction.followup.send(
                "❌ An error occurred while importing the embed.",
                ephemeral=True
            )

class MessageTypeView(discord.ui.View):
    def __init__(self, cog, start_date, hour, minute, timezone):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.original_message = None

    @discord.ui.button(label="Embed Message", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def embed_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            example_time = "30 minutes"
            
            embed = discord.Embed(
                title="Bear Trap Notification",
                description="Get ready for Bear! Only %t remaining.",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Bear Trap Notification System")
            
            content = (
                "📝 **Embed Editor**\n\n"
                "**Note:** \n"
                "• Use `%t` or `{time}` to show the remaining time\n"
                "• Use `@tag` for mentions (will be replaced with the actual mention)\n"
                "• You can use these in title, description, footer, and author fields\n"
                "• Time will automatically show with appropriate units (minutes/hours/days)\n\n"
                f"Currently showing '{example_time}' as an example."
            )

            view = EmbedEditorView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                interaction.message
            )
            view.embed_data = {
                "title": embed.title,
                "description": embed.description,
                "color": embed.color.value,
                "footer": "Bear Trap Notification System"
            }

            await interaction.response.edit_message(
                content=content,
                embed=embed,
                view=view
            )
            
        except Exception as e:
            print(f"Error in embed_message: {e}")
            await interaction.followup.send(
                "❌ An error occurred while starting the embed editor!",
                ephemeral=True
            )

    @discord.ui.button(label="Plain Message", style=discord.ButtonStyle.secondary, emoji="✍️", row=0)
    async def plain_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = discord.ui.Modal(title="Message Content")
        message_content = discord.ui.TextInput(
            label="Message",
            placeholder="Enter notification message... You can use @tag for mentions and %t or {time} for time",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )
        modal.add_item(message_content)

        async def modal_submit(modal_interaction):
            channels = interaction.guild.text_channels
            await self.cog.show_channel_selection(
                modal_interaction,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                f"PLAIN_MESSAGE:{message_content.value}",
                channels
            )

        modal.on_submit = modal_submit
        await interaction.response.send_modal(modal)

class TimeSelectModal(discord.ui.Modal):
    def __init__(self, cog: BearTrap):
        super().__init__(title="Set Notification Time")
        self.cog = cog
        
        current_utc = datetime.now(pytz.UTC)
        
        self.start_date = discord.ui.TextInput(
            label="Start Date (DD/MM/YYYY)",
            placeholder="Enter start date (e.g., 25/03/2024)",
            min_length=8,
            max_length=10,
            required=True,
            default=current_utc.strftime("%d/%m/%Y")
        )
        
        self.hour = discord.ui.TextInput(
            label="Hour (0-23)",
            placeholder="Enter hour (e.g., 14)",
            min_length=1,
            max_length=2,
            required=True,
            default=current_utc.strftime("%H")
        )
        
        self.minute = discord.ui.TextInput(
            label="Minute (0-59)",
            placeholder="Enter minute (e.g., 30)",
            min_length=1,
            max_length=2,
            required=True,
            default=current_utc.strftime("%M")
        )
        
        self.timezone = discord.ui.TextInput(
            label="Timezone",
            placeholder="Enter timezone (e.g., UTC, Europe/Istanbul)",
            min_length=1,
            max_length=50,
            required=True,
            default="UTC"
        )

        for item in [self.start_date, self.hour, self.minute, self.timezone]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            try:
                timezone = pytz.timezone(self.timezone.value)
            except pytz.exceptions.UnknownTimeZoneError:
                await interaction.response.send_message(
                    "❌ Invalid timezone! Please use a valid timezone (e.g., UTC, Europe/Istanbul).",
                    ephemeral=True
                )
                return

            try:
                start_date = datetime.strptime(self.start_date.value, "%d/%m/%Y")
                now = datetime.now(timezone)
                start_date = timezone.localize(start_date)
                
                if start_date.date() < now.date():
                    await interaction.response.send_message(
                        "❌ Start date cannot be in the past for the selected timezone!",
                        ephemeral=True
                    )
                    return
            except ValueError:
                await interaction.response.send_message(
                    "❌ Invalid date format! Please use DD/MM/YYYY format.",
                    ephemeral=True
                )
                return

            hour = int(self.hour.value)
            minute = int(self.minute.value)
            
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Invalid time format")

            view = MessageTypeView(
                self.cog,
                start_date,
                hour,
                minute,
                self.timezone.value
            )
            
            embed = discord.Embed(
                title="📝 Select Message Type",
                description=(
                    "How should your notification message look?\n\n"
                    "**📝 Embed Message**\n"
                    "• Customizable title\n"
                    "• Rich text format\n"
                    "• Custom color selection\n"
                    "• Footer and author\n\n"
                    "**✍️ Plain Message**\n"
                    "• Simple text format\n"
                    "• Quick creation"
                ),
                color=discord.Color.blue()
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True
            )

        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid time format! Please use numbers for hour (0-23) and minute (0-59).",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error in time modal: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while setting the time.",
                ephemeral=True
            )

class NotificationTypeView(discord.ui.View):
    def __init__(self, cog, start_date, hour, minute, timezone, message_data, channel_id, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.channel_id = channel_id
        self.original_message = original_message

    @discord.ui.button(label="30m, 10m, 5m & Time", style=discord.ButtonStyle.primary, custom_id="type_1", row=0)
    async def type_1(self, interaction, button):
        await self.show_mention_type_menu(interaction, 1)

    @discord.ui.button(label="10m, 5m & Time", style=discord.ButtonStyle.primary, custom_id="type_2", row=0)
    async def type_2(self, interaction, button):
        await self.show_mention_type_menu(interaction, 2)

    @discord.ui.button(label="5m & Time", style=discord.ButtonStyle.primary, custom_id="type_3", row=1)
    async def type_3(self, interaction, button):
        await self.show_mention_type_menu(interaction, 3)

    @discord.ui.button(label="Only 5m", style=discord.ButtonStyle.primary, custom_id="type_4", row=1)
    async def type_4(self, interaction, button):
        await self.show_mention_type_menu(interaction, 4)

    @discord.ui.button(label="Only Time", style=discord.ButtonStyle.primary, custom_id="type_5", row=1)
    async def type_5(self, interaction, button):
        await self.show_mention_type_menu(interaction, 5)

    @discord.ui.button(label="Custom Times", style=discord.ButtonStyle.success, custom_id="type_6", row=2)
    async def type_6(self, interaction, button):
        modal = CustomTimesModal(self.cog, self.start_date, self.hour, self.minute, self.timezone, self.message_data, self.channel_id, self.original_message)
        await interaction.response.send_modal(modal)

    async def show_mention_type_menu(self, interaction, notification_type):
        try:
            embed = discord.Embed(
                title="📢 Select Mention Type",
                description=(
                    "Choose how to mention users:\n\n"
                    "1️⃣ @everyone\n"
                    "2️⃣ Specific Role\n"
                    "3️⃣ Specific Member\n"
                    "4️⃣ No Mention"
                ),
                color=discord.Color.blue()
            )

            view = MentionTypeView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                self.message_data,
                self.channel_id,
                notification_type,
                self.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )
        except Exception as e:
            print(f"Error in show_mention_type_menu: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing mention options!",
                ephemeral=True
            )

class CustomTimesModal(discord.ui.Modal):
    def __init__(self, cog, start_date, hour, minute, timezone, message_data, channel_id, original_message):
        super().__init__(title="Set Custom Notification Times")
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.channel_id = channel_id
        self.original_message = original_message
        
        self.custom_times = discord.ui.TextInput(
            label="Custom Notification Times",
            placeholder="Enter times in minutes (e.g., 60-20-15-4-2 or 60-20-15-4-2-0)",
            min_length=1,
            max_length=50,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.custom_times)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            times_str = self.custom_times.value.strip()
            times = [int(t) for t in times_str.split('-')]
            
            if not all(isinstance(t, int) and t >= 0 for t in times):
                raise ValueError("All times must be non-negative integers")
                
            if not times:
                raise ValueError("At least one time must be specified")
                
            if not all(times[i] > times[i+1] for i in range(len(times)-1)):
                raise ValueError("Times must be in descending order")

            embed = discord.Embed(
                title="📢 Select Mention Type",
                description=(
                    "Choose how to mention users:\n\n"
                    "1️⃣ @everyone\n"
                    "2️⃣ Specific Role\n"
                    "3️⃣ Specific Member\n"
                    "4️⃣ No Mention"
                ),
                color=discord.Color.blue()
            )

            view = MentionTypeView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                f"CUSTOM_TIMES:{'-'.join(map(str, times))}|{self.message_data}",
                self.channel_id,
                6,
                self.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )

        except ValueError as e:
            await interaction.response.send_message(
                f"❌ Invalid input: {str(e)}",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error in custom times modal: {e}")
            await interaction.followup.send(
                "❌ An error occurred while processing custom times.",
                ephemeral=True
            )

class MentionTypeView(discord.ui.View):
    def __init__(self, cog, start_date, hour, minute, timezone, message_data, channel_id, notification_type, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.channel_id = channel_id
        self.notification_type = notification_type
        self.original_message = original_message

    async def show_mention_type_menu(self, interaction, mention_type):
        try:
            embed = discord.Embed(
                title="🔄 Repeat Settings",
                description=(
                    "**Configure Notification Repeat**\n\n"
                    "Choose how often you want this notification to repeat:\n\n"
                    "• No Repeat: Notification will be sent only once\n"
                    "• Custom Interval: Set a custom repeat interval (minutes/hours/days/weeks/months)"
                ),
                color=discord.Color.blue()
            )

            view = RepeatOptionView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                self.message_data,
                self.channel_id,
                self.notification_type,
                mention_type,
                self.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )
        except Exception as e:
            print(f"Error in show_mention_type_menu: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing mention options!",
                ephemeral=True
            )

    @discord.ui.button(label="@everyone", style=discord.ButtonStyle.danger, emoji="📢", row=0)
    async def everyone_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.show_mention_type_menu(interaction, "everyone")
        except Exception as e:
            print(f"Error in everyone button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while setting @everyone mention!",
                ephemeral=True
            )

    @discord.ui.button(label="Select Member", style=discord.ButtonStyle.primary, emoji="👤", row=0)
    async def member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            select = discord.ui.UserSelect(
                placeholder="Select a member to mention",
                min_values=1,
                max_values=1
            )

            async def user_select_callback(select_interaction):
                try:
                    selected_user_id = select_interaction.data["values"][0]
                    await self.show_mention_type_menu(select_interaction, f"member_{selected_user_id}")
                except Exception as e:
                    print(f"Error in user selection: {e}")
                    await select_interaction.followup.send(
                        "❌ An error occurred while selecting the member!",
                        ephemeral=True
                    )

            select.callback = user_select_callback
            view = discord.ui.View(timeout=300)
            view.add_item(select)

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="👤 Select Member",
                    description="Choose a member to mention:",
                    color=discord.Color.blue()
                ),
                view=view
            )
        except Exception as e:
            print(f"Error in member button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing member selection!",
                ephemeral=True
            )

    @discord.ui.button(label="Select Role", style=discord.ButtonStyle.success, emoji="👥", row=0)
    async def role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            select = discord.ui.RoleSelect(
                placeholder="Select a role to mention",
                min_values=1,
                max_values=1
            )

            async def role_select_callback(select_interaction):
                try:
                    selected_role_id = select_interaction.data["values"][0]
                    await self.show_mention_type_menu(select_interaction, f"role_{selected_role_id}")
                except Exception as e:
                    print(f"Error in role selection: {e}")
                    await select_interaction.followup.send(
                        "❌ An error occurred while selecting the role!",
                        ephemeral=True
                    )

            select.callback = role_select_callback
            view = discord.ui.View(timeout=300)
            view.add_item(select)

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="👥 Select Role",
                    description="Choose a role to mention:",
                    color=discord.Color.blue()
                ),
                view=view
            )
        except Exception as e:
            print(f"Error in role button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing role selection!",
                ephemeral=True
            )

    @discord.ui.button(label="No Mention", style=discord.ButtonStyle.secondary, emoji="🔕", row=0)
    async def no_mention_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.show_mention_type_menu(interaction, "none")
        except Exception as e:
            print(f"Error in no mention button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while setting no mention!",
                ephemeral=True
            )

class MentionSelectMenu(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        
        options = []
        
        options.append(
            discord.SelectOption(
                label="@everyone",
                value="everyone",
                description="Mention everyone in the server",
                emoji="📢"
            )
        )
        
        options.append(
            discord.SelectOption(
                label="No Mention",
                value="none",
                description="Don't mention anyone",
                emoji="🔕"
            )
        )
        
        guild = view.original_message.guild
        roles = sorted(
            [role for role in guild.roles if not role.is_default() and not role.managed],
            key=lambda r: r.position,
            reverse=True
        )
        
        for role in roles:
            options.append(
                discord.SelectOption(
                    label=role.name,
                    value=f"role_{role.id}",
                    description=f"Role with {len(role.members)} members",
                    emoji="👥"
                )
            )
        
        members = sorted(
            [member for member in guild.members if not member.bot],
            key=lambda m: m.display_name.lower()
        )
        
        for member in members:
            options.append(
                discord.SelectOption(
                    label=member.display_name,
                    value=f"member_{member.id}",
                    description=f"@{member.name}",
                    emoji="👤"
                )
            )
        
        super().__init__(
            placeholder="🔍 Search and select who to mention...",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            selected_value = self.values[0]
            
            await self.parent_view.show_mention_type_menu(interaction, selected_value)
            
        except Exception as e:
            print(f"Error in mention selection: {e}")
            await interaction.followup.send(
                "❌ An error occurred while processing your selection!",
                ephemeral=True
            )

class BearTrapView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Set Time",
        emoji="⏰",
        style=discord.ButtonStyle.primary,
        custom_id="set_time",
        row=0
    )
    async def set_time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return

        try:            
            embed = discord.Embed(
                title="⏰ Notification Creation Method",
                description=(
                    "Please select how you want to create the notification:\n\n"
                    "**⚙️ Create in Discord**\n"
                    "• For simple notifications\n"
                    "• Quick setup\n"
                    "• Basic features\n\n"
                    "**🌐 Create on Website (Recommended)**\n"
                    "• For advanced notifications\n"
                    "• Customizable embeds\n"
                    "• Rich text formatting\n"
                    "• Custom color selection\n"
                    "• Add images and thumbnails\n"
                    "• Footer and author fields"
                ),
                color=discord.Color.blue()
            )

            view = discord.ui.View(timeout=300)

            discord_button = discord.ui.Button(
                label="Create in Discord",
                emoji="⚙️",
                style=discord.ButtonStyle.primary,
                custom_id="create_in_discord"
            )

            async def discord_button_callback(discord_interaction):
                modal = TimeSelectModal(self.cog)
                await discord_interaction.response.send_modal(modal)

            discord_button.callback = discord_button_callback

            web_button = discord.ui.Button(
                label="Create on Website",
                emoji="🌐", 
                style=discord.ButtonStyle.success,
                custom_id="create_in_web"
            )

            async def web_button_callback(web_interaction):
                try:
                    editor_cog = self.cog.bot.get_cog('BearTrapEditor')
                    if not editor_cog:
                        await web_interaction.response.send_message(
                            "❌ BearTrapEditor module not found!",
                            ephemeral=True
                        )
                        return

                    view = editor_cog.TimeSelectOptionsView(editor_cog)
                    await view.start_setup(web_interaction)

                except Exception as e:
                    print(f"Error in web button: {e}")
                    await web_interaction.response.send_message(
                        "❌ An error occurred while starting the website process!",
                        ephemeral=True
                    )

            web_button.callback = web_button_callback

            view.add_item(discord_button)
            view.add_item(web_button)

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            error_msg = f"[ERROR] Error in set time button: {str(e)}\nType: {type(e)}\nTrace: {traceback.format_exc()}"
            print(error_msg)
            
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred!",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ An error occurred!",
                        ephemeral=True
                    )
            except Exception as notify_error:
                print(f"[ERROR] Failed to notify user about error: {notify_error}")

    @discord.ui.button(
        label="Remove Notification",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="remove_notification",
        row=0
    )
    async def remove_notification_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found for this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]}",
                        value=str(notif[0])
                    )
                )
            select = discord.ui.Select(
                placeholder="Select a notification to remove",
                options=options[:25]
            )

            async def select_callback(select_interaction):
                try:
                    notification_id = int(select_interaction.data["values"][0])
                    selected_notif = next(n for n in notifications if n[0] == notification_id)

                    notification_types = {
                        1: "Sends notifications at 30 minutes, 10 minutes, 5 minutes before and when time's up",
                        2: "Sends notifications at 10 minutes, 5 minutes before and when time's up",
                        3: "Sends notifications at 5 minutes before and when time's up",
                        4: "Sends notification only 5 minutes before",
                        5: "Sends notification only when time's up",
                        6: "Sends notifications at custom times"
                    }

                    mention_type = selected_notif[8]
                    if mention_type == "everyone":
                        mention_display = "@everyone"
                    elif mention_type.startswith("role_"):
                        role_id = int(mention_type.split('_')[1])
                        role = select_interaction.guild.get_role(role_id)
                        mention_display = f"@{role.name}" if role else f"Role: {role_id}"
                    elif mention_type.startswith("member_"):
                        member_id = int(mention_type.split('_')[1])
                        member = select_interaction.guild.get_member(member_id)
                        mention_display = f"@{member.display_name}" if member else f"Member: {member_id}"
                    else:
                        mention_display = "Unknown"

                    if not selected_notif[9]:  
                        repeat_text = "❌ No repeat"
                    else:
                        minutes = selected_notif[10]  
                        if minutes == 1:
                            repeat_text = "🔄 Repeats every minute"
                        elif minutes == 60:
                            repeat_text = "🔄 Repeats every hour"
                        elif minutes == 1440:
                            repeat_text = "🔄 Repeats daily"
                        elif minutes == 2880:
                            repeat_text = "🔄 Repeats every 2 days"
                        elif minutes == 4320:
                            repeat_text = "🔄 Repeats every 3 days"
                        elif minutes == 10080:
                            repeat_text = "🔄 Repeats weekly"
                        else:
                            repeat_text = f"🔄 Repeats every {minutes} minutes"

                    embed = discord.Embed(
                        title="🗑️ Remove Notification",
                        description=(
                            f"**📅 Date:** {datetime.fromisoformat(selected_notif[15]).strftime('%d/%m/%Y')}\n"
                            f"**⏰ Time:** {selected_notif[3]:02d}:{selected_notif[4]:02d} {selected_notif[5]}\n"
                            f"**📢 Channel:** <#{selected_notif[2]}>\n"
                            f"**📝 Description:** {selected_notif[6].split('|')[-1] if '|' in selected_notif[6] else selected_notif[6]}\n\n"
                            f"**⚙️ Notification Type**\n{notification_types[selected_notif[7]]}\n\n"
                            f"**👥 Mentions:** {mention_display}\n"
                            f"**🔄 Repeat:** {repeat_text}\n\n"
                            "Are you sure you want to remove this notification?"
                        ),
                        color=discord.Color.red()
                    )

                    confirm_view = discord.ui.View()
                    
                    async def confirm_callback(confirm_interaction):
                        try:
                            self.cog.cursor.execute("DELETE FROM bear_notification_embeds WHERE notification_id = ?", (notification_id,))
                            
                            self.cog.cursor.execute("DELETE FROM notification_history WHERE notification_id = ?", (notification_id,))

                            self.cog.cursor.execute("DELETE FROM bear_notifications WHERE id = ?", (notification_id,))
                            
                            self.cog.conn.commit()
                            await confirm_interaction.response.edit_message(
                                content="✅ Notification and its history have been removed successfully.",
                                embed=None,
                                view=None
                            )
                        except Exception as e:
                            print(f"Error removing notification: {e}")
                            await confirm_interaction.response.send_message(
                                "❌ An error occurred while removing the notification.",
                                ephemeral=True
                            )

                    async def cancel_callback(cancel_interaction):
                        await cancel_interaction.response.edit_message(
                            content="❌ Removal cancelled.",
                            embed=None,
                            view=None
                        )

                    confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
                    cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
                    
                    confirm_button.callback = confirm_callback
                    cancel_button.callback = cancel_callback
                    
                    confirm_view.add_item(confirm_button)
                    confirm_view.add_item(cancel_button)
                    
                    await select_interaction.response.edit_message(embed=embed, view=confirm_view)
                except Exception as e:
                    print(f"Error in remove notification callback: {e}")
                    await select_interaction.response.send_message(
                        "❌ An error occurred while removing the notification.",
                        ephemeral=True
                    )

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)
            
            await interaction.response.send_message(
                "Select a notification to remove:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            print(f"Error in remove notification: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while loading notifications.",
                ephemeral=True
            )

    @discord.ui.button(
        label="View Notifications",
        emoji="📋",
        style=discord.ButtonStyle.primary,
        custom_id="view_notifications",
        row=1
    )
    async def view_notifications_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found for this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                status = "🟢 Enabled" if notif[11] else "🔴 Disabled"
                
                if "EMBED_MESSAGE:" in notif[6]:
                    self.cog.cursor.execute("""
                        SELECT title, description 
                        FROM bear_notification_embeds 
                        WHERE notification_id = ?
                    """, (notif[0],))
                    embed_data = self.cog.cursor.fetchone()
                    
                    if embed_data and embed_data[0]:
                        display_description = f"📝 Embed: {embed_data[0]}"
                    else:
                        display_description = "📝 Embed Message"
                else:
                    display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                    if display_description.startswith("PLAIN_MESSAGE:"):
                        display_description = display_description.replace("PLAIN_MESSAGE:", "✍️ ")

                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]} | {status}",
                        value=str(notif[0])
                    )
                )
            select = discord.ui.Select(
                placeholder="Select a notification to view details",
                options=options[:25]
            )

            view = discord.ui.View()

            async def select_callback(select_interaction):
                try:
                    notification_id = int(select_interaction.data["values"][0])
                    selected_notif = next(n for n in notifications if n[0] == notification_id)

                    notification_types = {
                        1: "Sends notifications at 30 minutes, 10 minutes, 5 minutes before and when time's up",
                        2: "Sends notifications at 10 minutes, 5 minutes before and when time's up",
                        3: "Sends notifications at 5 minutes before and when time's up",
                        4: "Sends notification only 5 minutes before",
                        5: "Sends notification only when time's up",
                        6: "Sends notifications at custom times"
                    }

                    mention_type = selected_notif[8]
                    if mention_type == "everyone":
                        mention_display = "@everyone"
                    elif mention_type.startswith("role_"):
                        role_id = int(mention_type.split('_')[1])
                        role = select_interaction.guild.get_role(role_id)
                        mention_display = f"@{role.name}" if role else f"Role: {role_id}"
                    elif mention_type.startswith("member_"):
                        member_id = int(mention_type.split('_')[1])
                        member = select_interaction.guild.get_member(member_id)
                        mention_display = f"@{member.display_name}" if member else f"Member: {member_id}"
                    else:
                        mention_display = "Unknown"

                    if not selected_notif[9]:  
                        repeat_text = "❌ No repeat"
                    else:
                        minutes = selected_notif[10]  
                        if minutes == 1:
                            repeat_text = "🔄 Repeats every minute"
                        elif minutes == 60:
                            repeat_text = "🔄 Repeats every hour"
                        elif minutes == 1440:
                            repeat_text = "🔄 Repeats daily"
                        elif minutes == 2880:
                            repeat_text = "🔄 Repeats every 2 days"
                        elif minutes == 4320:
                            repeat_text = "🔄 Repeats every 3 days"
                        elif minutes == 10080:
                            repeat_text = "🔄 Repeats weekly"
                        else:
                            repeat_text = f"🔄 Repeats every {minutes} minutes"

                    embed_data = None
                    if "EMBED_MESSAGE:" in selected_notif[6]:
                        self.cog.cursor.execute("""
                            SELECT title, description, color, image_url, thumbnail_url, footer, author, mention_message
                            FROM bear_notification_embeds 
                            WHERE notification_id = ?
                        """, (notification_id,))
                        embed_result = self.cog.cursor.fetchone()
                        if embed_result:
                            embed_data = {
                                'title': embed_result[0],
                                'description': embed_result[1],
                                'color': embed_result[2],
                                'image_url': embed_result[3],
                                'thumbnail_url': embed_result[4],
                                'footer': embed_result[5],
                                'author': embed_result[6],
                                'mention_message': embed_result[7]
                            }

                    details_embed = discord.Embed(
                        title="📋 Notification Details",
                        description=(
                            f"**📅 Date:** {datetime.fromisoformat(selected_notif[15]).strftime('%d/%m/%Y')}\n"
                            f"**⏰ Time:** {selected_notif[3]:02d}:{selected_notif[4]:02d} {selected_notif[5]}\n"
                            f"**📢 Channel:** <#{selected_notif[2]}>\n"
                            f"**📝 Description:** {selected_notif[6].split('|')[-1] if '|' in selected_notif[6] else selected_notif[6]}\n\n"
                            f"**⚙️ Notification Type**\n{notification_types[selected_notif[7]]}\n\n"
                            f"**👥 Mentions:** {mention_display}\n"
                            f"**🔄 Repeat:** {repeat_text}"
                        ),
                        color=discord.Color.blue()
                    )

                    if embed_data:
                        preview_embed = discord.Embed(
                            title=embed_data['title'] if embed_data['title'] else "No Title",
                            description=embed_data['description'] if embed_data['description'] else "No Description",
                            color=embed_data['color'] if embed_data['color'] else discord.Color.blue()
                        )
                        
                        if embed_data['image_url']:
                            preview_embed.set_image(url=embed_data['image_url'])
                        if embed_data['thumbnail_url']:
                            preview_embed.set_thumbnail(url=embed_data['thumbnail_url'])
                        if embed_data['footer']:
                            preview_embed.set_footer(text=embed_data['footer'])
                        if embed_data['author']:
                            preview_embed.set_author(name=embed_data['author'])

                        mention_preview = ""
                        if embed_data['mention_message']:
                            mention_preview = embed_data['mention_message']
                            example_time = "30 minutes"
                            if "%t" in mention_preview:
                                mention_preview = mention_preview.replace("%t", example_time)
                            if "{time}" in mention_preview:
                                mention_preview = mention_preview.replace("{time}", example_time)

                        copyable_data = {
                            'title': embed_data['title'],
                            'description': embed_data['description'],
                            'color': embed_data['color'],
                            'footer': embed_data['footer'],
                            'author': embed_data['author'],
                            'image_url': embed_data['image_url'],
                            'thumbnail_url': embed_data['thumbnail_url'],
                            'mention_message': embed_data['mention_message']
                        }
                        
                        embed_json = json.dumps(copyable_data, indent=2)

                        view = discord.ui.View()
                        view.add_item(select)

                        content = "**📋 Notification Details**\n\n"
                        content += f"**Embed Code:**\n```json\n{embed_json}\n```\n"
                        if mention_preview:
                            content += f"**Message Preview:**\n{mention_preview}"

                        await select_interaction.response.edit_message(
                            content=content,
                            embeds=[details_embed, preview_embed],
                            view=view
                        )
                    else:
                        view = discord.ui.View()
                        view.add_item(select)
                        
                        message_preview = None
                        if "PLAIN_MESSAGE:" in selected_notif[6]:
                            message_preview = selected_notif[6].replace("PLAIN_MESSAGE:", "")
                        
                        await select_interaction.response.edit_message(
                            content="**📋 Notification Details**" + 
                                  (f"\n\n**Message Preview:**\n{message_preview}" if message_preview else ""),
                            embed=details_embed,
                            view=view
                        )

                except Exception as e:
                    print(f"Error in select callback: {e}")
                    await select_interaction.response.send_message(
                        "❌ An error occurred while processing your selection.",
                        ephemeral=True
                    )

            select.callback = select_callback
            view.add_item(select)
            
            await interaction.response.send_message(
                "Select a notification to view details:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            print(f"Error viewing notifications: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while loading notifications.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Enable/Disable",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="toggle_notifications",
        row=1
    )
    async def toggle_notifications_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found for this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                status = "🟢 Enabled" if notif[11] else "🔴 Disabled"
                if "EMBED_MESSAGE:" in notif[6]:
                        # Query the database for the embed title
                        self.cog.cursor.execute("""
                            SELECT title 
                            FROM bear_notification_embeds 
                            WHERE notification_id = ?
                        """, (notif[0],))
                        embed_data = self.cog.cursor.fetchone()
                        display_description = f"📝 Embed: {embed_data[0]}" if embed_data and embed_data[0] else "📝 Embed Message"
                else:
                    display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                    if display_description.startswith("PLAIN_MESSAGE:"):
                        display_description = display_description.replace("PLAIN_MESSAGE:", "✍️ ")
                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]} | {status}",
                        value=str(notif[0])
                    )
                )
            select = discord.ui.Select(
                placeholder="Select a notification to toggle",
                options=options[:25]
            )

            async def select_callback(select_interaction):
                notification_id = int(select_interaction.data["values"][0])
                current_status = next(n[11] for n in notifications if n[0] == notification_id)
                new_status = not current_status
                
                if await self.cog.toggle_notification(notification_id, new_status):
                    status_text = "enabled" if new_status else "disabled"
                    embed = discord.Embed(
                        title="✅ Notification Status Updated",
                        description=f"The notification has been {status_text}.",
                        color=discord.Color.green() if new_status else discord.Color.red()
                    )
                    await select_interaction.response.edit_message(embed=embed, view=None)
                else:
                    await select_interaction.response.send_message(
                        "❌ Failed to update notification status.",
                        ephemeral=True
                    )

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)
            
            await interaction.response.send_message(
                "Select a notification to toggle:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            print(f"Error in toggle notifications: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while loading notifications.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="main_menu",
        row=2
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            alliance_cog = self.cog.bot.get_cog("Alliance")
            if alliance_cog:
                await alliance_cog.show_main_menu(interaction)
        except Exception as e:
            print(f"Error returning to main menu: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while returning to main menu.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Edit",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
        custom_id="edit_notification",
        row=1
    )
    async def edit_notification_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found to edit in this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                status = "🟢 Enabled" if notif[11] else "🔴 Disabled"
                
                if "EMBED_MESSAGE:" in notif[6]:
                    self.cog.cursor.execute("""
                        SELECT title, description 
                        FROM bear_notification_embeds 
                        WHERE notification_id = ?
                    """, (notif[0],))
                    embed_data = self.cog.cursor.fetchone()
                    
                    if embed_data and embed_data[0]:
                        display_description = f"📝 Embed: {embed_data[0]}"
                    else:
                        display_description = "📝 Embed Message"
                else:
                    display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                    if display_description.startswith("PLAIN_MESSAGE:"):
                        display_description = display_description.replace("PLAIN_MESSAGE:", "✍️ ")

                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]} | {status}",
                        value=str(notif[0])
                    )
                )

            select = discord.ui.Select(
                placeholder="Select a notification to edit",
                options=options[:25]
            )

            async def select_callback(select_interaction):
                try:
                    notification_id = int(select_interaction.data["values"][0])
                    editor_cog = self.cog.bot.get_cog('BearTrapEditor')
                    if editor_cog:
                        await editor_cog.start_edit_process(select_interaction, notification_id)
                    else:
                        await select_interaction.response.send_message(
                            "❌ Editor module not found!",
                            ephemeral=True
                        )
                except Exception as e:
                    print(f"Error in edit notification callback: {e}")
                    await select_interaction.response.send_message(
                        "❌ An error occurred during editing!",
                        ephemeral=True
                    )

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)
            
            await interaction.response.send_message(
                "Select the notification you want to edit:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            print(f"Error in edit button: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while starting the edit process!",
                ephemeral=True
            )

class ChannelSelectView(discord.ui.View):
    def __init__(self, cog, start_date, hour, minute, timezone, message_data, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.original_message = original_message
        
        self.add_item(ChannelSelectMenu(self))

class ChannelSelectMenu(discord.ui.ChannelSelect):
    def __init__(self, view):
        self.parent_view = view
        super().__init__(
            placeholder="Select a channel for notifications",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.private,
                discord.ChannelType.news,
                discord.ChannelType.forum,
                discord.ChannelType.news_thread,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
                discord.ChannelType.stage_voice
            ],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            channel = self.values[0]
            actual_channel = interaction.guild.get_channel(channel.id)
            if not actual_channel.permissions_for(interaction.guild.me).send_messages:
                await interaction.response.send_message(
                    "❌ I don't have permission to send messages in this channel!",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="⏰ Select Notification Type",
                description=(
                    "Choose when to send notifications:\n\n"
                    "**30m, 10m, 5m & Time**\n"
                    "• 30 minutes before\n"
                    "• 10 minutes before\n"
                    "• 5 minutes before\n"
                    "• When time's up\n\n"
                    "**10m, 5m & Time**\n"
                    "• 10 minutes before\n"
                    "• 5 minutes before\n"
                    "• When time's up\n\n"
                    "**5m & Time**\n"
                    "• 5 minutes before\n"
                    "• When time's up\n\n"
                    "**Only 5m**\n"
                    "• Only 5 minutes before\n\n"
                    "**Only Time**\n"
                    "• Only when time's up\n\n"
                    "**Custom Times**\n"
                    "• Set your own notification times"
                ),
                color=discord.Color.blue()
            )

            view = NotificationTypeView(
                self.parent_view.cog,
                self.parent_view.start_date,
                self.parent_view.hour,
                self.parent_view.minute,
                self.parent_view.timezone,
                self.parent_view.message_data,
                channel.id,
                self.parent_view.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )

        except Exception as e:
            print(f"Error in channel select callback: {e}")
            try:
                await interaction.response.send_message(
                    "❌ An error occurred while processing your selection!",
                    ephemeral=True
                )
            except discord.InteractionResponded:
                await interaction.followup.send(
                    "❌ An error occurred while processing your selection!",
                    ephemeral=True
                )

class ImportEmbedModal(discord.ui.Modal):
    def __init__(self, embed_view):
        super().__init__(title="Import Embed")
        self.embed_view = embed_view
        
        self.embed_code = discord.ui.TextInput(
            label="Embed Code",
            placeholder="Paste the embed code here...",
            style=discord.TextStyle.paragraph,
            required=True
        )
        self.add_item(self.embed_code)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            embed_data = json.loads(self.embed_code.value)
            
            self.embed_view.embed_data.update({
                'title': embed_data.get('title') or '',
                'description': embed_data.get('description') or '',
                'color': embed_data.get('color', discord.Color.blue().value),
                'footer': embed_data.get('footer') or '',
                'author': embed_data.get('author') or '',
                'image_url': embed_data.get('image_url') or '',
                'thumbnail_url': embed_data.get('thumbnail_url') or '',
                'mention_message': embed_data.get('mention_message') or '@tag'
            })
            
            await self.embed_view.update_embed(interaction)
            await interaction.followup.send(
                "✅ Embed imported successfully!",
                ephemeral=True
            )
            
        except json.JSONDecodeError:
            await interaction.response.send_message(
                "❌ Invalid embed code format. Please make sure you copied the entire code correctly.",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error importing embed: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while importing the embed.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ An error occurred while importing the embed.",
                        ephemeral=True
                    )
            except:
                pass

async def setup(bot):
    await bot.add_cog(BearTrap(bot)) 
