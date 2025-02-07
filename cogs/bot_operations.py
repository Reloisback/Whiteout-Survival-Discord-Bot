import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import asyncio
import requests
from .alliance_member_operations import AllianceSelectView

VERSION_URL = "https://raw.githubusercontent.com/Reloisback/Whiteout-Survival-Discord-Bot/refs/heads/main/autoupdateinfo.txt"

class BotOperations(commands.Cog):
    def __init__(self, bot, conn):
        self.bot = bot
        self.conn = conn
        self.settings_db = sqlite3.connect('db/settings.sqlite', check_same_thread=False)
        self.settings_cursor = self.settings_db.cursor()
        self.alliance_db = sqlite3.connect('db/alliance.sqlite', check_same_thread=False)
        self.c_alliance = self.alliance_db.cursor()
        self.setup_database()

    def setup_database(self):
        try:
            self.settings_cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY,
                    is_initial INTEGER DEFAULT 0
                )
            """)
            
            self.settings_cursor.execute("""
                CREATE TABLE IF NOT EXISTS adminserver (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin INTEGER NOT NULL,
                    alliances_id INTEGER NOT NULL,
                    FOREIGN KEY (admin) REFERENCES admin(id),
                    UNIQUE(admin, alliances_id)
                )
            """)
            
            self.settings_db.commit()
                
        except Exception as e:
            pass

    def __del__(self):
        try:
            self.settings_db.close()
            self.alliance_db.close()
        except:
            pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.type == discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")
        
        if custom_id == "bot_operations":
            return
        
        if custom_id == "alliance_control_messages":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if not result or result[0] != 1:
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                self.settings_cursor.execute("SELECT value FROM auto LIMIT 1")
                result = self.settings_cursor.fetchone()
                current_value = result[0] if result else 1

                embed = discord.Embed(
                    title="💬 Alliance Control Messages Settings",
                    description=f"Alliance Control Information Message is Currently {'On' if current_value == 1 else 'Off'}",
                    color=discord.Color.green() if current_value == 1 else discord.Color.red()
                )

                view = discord.ui.View()
                
                open_button = discord.ui.Button(
                    label="Turn On",
                    emoji="✅",
                    style=discord.ButtonStyle.success,
                    custom_id="control_messages_open",
                    disabled=current_value == 1
                )
                
                close_button = discord.ui.Button(
                    label="Turn Off",
                    emoji="❌",
                    style=discord.ButtonStyle.danger,
                    custom_id="control_messages_close",
                    disabled=current_value == 0
                )

                async def open_callback(button_interaction: discord.Interaction):
                    self.settings_cursor.execute("UPDATE auto SET value = 1")
                    self.settings_db.commit()
                    
                    embed.description = "Alliance Control Information Message Turned On"
                    embed.color = discord.Color.green()
                    
                    open_button.disabled = True
                    close_button.disabled = False
                    
                    await button_interaction.response.edit_message(embed=embed, view=view)

                async def close_callback(button_interaction: discord.Interaction):
                    self.settings_cursor.execute("UPDATE auto SET value = 0")
                    self.settings_db.commit()
                    
                    embed.description = "Alliance Control Information Message Turned Off"
                    embed.color = discord.Color.red()
                    
                    open_button.disabled = False
                    close_button.disabled = True
                    
                    await button_interaction.response.edit_message(embed=embed, view=view)

                open_button.callback = open_callback
                close_button.callback = close_callback

                view.add_item(open_button)
                view.add_item(close_button)

                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

            except Exception as e:
                print(f"Alliance control messages error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while managing alliance control messages.",
                        ephemeral=True
                    )
                    
        elif custom_id in ["assign_alliance", "add_admin", "remove_admin", "main_menu", "bot_status", "bot_settings"]:
            try:
                if custom_id == "assign_alliance":
                    try:
                        with sqlite3.connect('db/settings.sqlite') as settings_db:
                            cursor = settings_db.cursor()
                            cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                            result = cursor.fetchone()
                            
                            if not result or result[0] != 1:
                                await interaction.response.send_message(
                                    "❌ Only global administrators can use this command.", 
                                    ephemeral=True
                                )
                                return

                            cursor.execute("""
                                SELECT id, is_initial 
                                FROM admin 
                                ORDER BY is_initial DESC, id
                            """)
                            admins = cursor.fetchall()

                            if not admins:
                                await interaction.response.send_message(
                                    "❌ No administrators found.", 
                                    ephemeral=True
                                )
                                return

                            admin_options = []
                            for admin_id, is_initial in admins:
                                try:
                                    user = await self.bot.fetch_user(admin_id)
                                    admin_name = f"{user.name} ({admin_id})"
                                except Exception as e:
                                    admin_name = f"Unknown User ({admin_id})"
                                
                                admin_options.append(
                                    discord.SelectOption(
                                        label=admin_name[:100],
                                        value=str(admin_id),
                                        description=f"{'Global Admin' if is_initial == 1 else 'Server Admin'}",
                                        emoji="👑" if is_initial == 1 else "👤"
                                    )
                                )

                            admin_embed = discord.Embed(
                                title="👤 Admin Selection",
                                description=(
                                    "Please select an administrator to assign alliance:\n\n"
                                    "**Administrator List**\n"
                                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                                    "Select an administrator from the list below:\n"
                                ),
                                color=discord.Color.blue()
                            )

                            admin_select = discord.ui.Select(
                                placeholder="Select an administrator...",
                                options=admin_options
                            )
                            
                            admin_view = discord.ui.View()
                            admin_view.add_item(admin_select)

                            async def admin_callback(admin_interaction: discord.Interaction):
                                try:
                                    selected_admin_id = int(admin_select.values[0])
                                    
                                    self.c_alliance.execute("""
                                        SELECT alliance_id, name 
                                        FROM alliance_list 
                                        ORDER BY name
                                    """)
                                    alliances = self.c_alliance.fetchall()

                                    if not alliances:
                                        await admin_interaction.response.send_message(
                                            "❌ No alliances found.", 
                                            ephemeral=True
                                        )
                                        return

                                    alliances_with_counts = []
                                    for alliance_id, name in alliances:
                                        with sqlite3.connect('db/users.sqlite') as users_db:
                                            cursor = users_db.cursor()
                                            cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                                            member_count = cursor.fetchone()[0]
                                            alliances_with_counts.append((alliance_id, name, member_count))

                                    alliance_embed = discord.Embed(
                                        title="🏰 Alliance Selection",
                                        description=(
                                            "Please select an alliance to assign to the administrator:\n\n"
                                            "**Alliance List**\n"
                                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                                            "Select an alliance from the list below:\n"
                                        ),
                                        color=discord.Color.blue()
                                    )

                                    view = AllianceSelectView(alliances_with_counts, self)
                                    
                                    async def alliance_callback(alliance_interaction: discord.Interaction):
                                        try:
                                            selected_alliance_id = int(view.current_select.values[0])
                                            
                                            with sqlite3.connect('db/settings.sqlite') as settings_db:
                                                cursor = settings_db.cursor()
                                                cursor.execute("""
                                                    INSERT INTO adminserver (admin, alliances_id)
                                                    VALUES (?, ?)
                                                """, (selected_admin_id, selected_alliance_id))
                                                settings_db.commit()

                                            with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                                                cursor = alliance_db.cursor()
                                                cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (selected_alliance_id,))
                                                alliance_name = cursor.fetchone()[0]
                                            try:
                                                admin_user = await self.bot.fetch_user(selected_admin_id)
                                                admin_name = admin_user.name
                                            except:
                                                admin_name = f"Unknown User ({selected_admin_id})"

                                            success_embed = discord.Embed(
                                                title="✅ Alliance Assigned",
                                                description=(
                                                    f"Successfully assigned alliance to administrator:\n\n"
                                                    f"👤 **Administrator:** {admin_name}\n"
                                                    f"🆔 **Admin ID:** {selected_admin_id}\n"
                                                    f"🏰 **Alliance:** {alliance_name}\n"
                                                    f"🆔 **Alliance ID:** {selected_alliance_id}"
                                                ),
                                                color=discord.Color.green()
                                            )
                                            
                                            if not alliance_interaction.response.is_done():
                                                await alliance_interaction.response.edit_message(
                                                    embed=success_embed,
                                                    view=None
                                                )
                                            else:
                                                await alliance_interaction.message.edit(
                                                    embed=success_embed,
                                                    view=None
                                                )
                                            
                                        except Exception as e:
                                            print(f"Alliance callback error: {e}")
                                            if not alliance_interaction.response.is_done():
                                                await alliance_interaction.response.send_message(
                                                    "❌ An error occurred while assigning the alliance.",
                                                    ephemeral=True
                                                )
                                            else:
                                                await alliance_interaction.followup.send(
                                                    "❌ An error occurred while assigning the alliance.",
                                                    ephemeral=True
                                                )

                                    view.callback = alliance_callback
                                    
                                    if not admin_interaction.response.is_done():
                                        await admin_interaction.response.edit_message(
                                            embed=alliance_embed,
                                            view=view
                                        )
                                    else:
                                        await admin_interaction.message.edit(
                                            embed=alliance_embed,
                                            view=view
                                        )

                                except Exception as e:
                                    print(f"Admin callback error: {e}")
                                    if not admin_interaction.response.is_done():
                                        await admin_interaction.response.send_message(
                                            "An error occurred while processing your request.",
                                            ephemeral=True
                                        )
                                    else:
                                        await admin_interaction.followup.send(
                                            "An error occurred while processing your request.",
                                            ephemeral=True
                                        )

                            admin_select.callback = admin_callback
                            
                            try:
                                await interaction.response.send_message(
                                    embed=admin_embed,
                                    view=admin_view,
                                    ephemeral=True
                                )
                            except Exception as e:
                                await interaction.followup.send(
                                    "An error occurred while sending the initial message.",
                                    ephemeral=True
                                )

                    except Exception as e:
                        try:
                            await interaction.response.send_message(
                                "An error occurred while processing your request.",
                                ephemeral=True
                            )
                        except:
                            pass
                elif custom_id == "add_admin":
                    try:
                        self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                        result = self.settings_cursor.fetchone()
                        
                        if not result or result[0] != 1:
                            await interaction.response.send_message(
                                "❌ Only global administrators can use this command", 
                                ephemeral=True
                            )
                            return

                        await interaction.response.send_message(
                            "Please tag the admin you want to add (@user).", 
                            ephemeral=True
                        )

                        def check(m):
                            return m.author.id == interaction.user.id and len(m.mentions) == 1

                        try:
                            message = await self.bot.wait_for('message', timeout=30.0, check=check)
                            new_admin = message.mentions[0]
                            
                            await message.delete()
                            
                            self.settings_cursor.execute("""
                                INSERT OR IGNORE INTO admin (id, is_initial)
                                VALUES (?, 0)
                            """, (new_admin.id,))
                            self.settings_db.commit()

                            success_embed = discord.Embed(
                                title="✅ Administrator Successfully Added",
                                description=(
                                    f"**New Administrator Information**\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"👤 **Name:** {new_admin.name}\n"
                                    f"🆔 **Discord ID:** {new_admin.id}\n"
                                    f"📅 **Account Creation Date:** {new_admin.created_at.strftime('%d/%m/%Y')}\n"
                                ),
                                color=discord.Color.green()
                            )
                            success_embed.set_thumbnail(url=new_admin.display_avatar.url)
                            
                            await interaction.edit_original_response(
                                content=None,
                                embed=success_embed
                            )

                        except asyncio.TimeoutError:
                            await interaction.edit_original_response(
                                content="❌ Timeout No user has been tagged.",
                                embed=None
                            )

                    except Exception as e:
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "❌ An error occurred while adding an administrator.",
                                ephemeral=True
                            )

                elif custom_id == "remove_admin":
                    try:
                        self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                        result = self.settings_cursor.fetchone()
                        
                        if not result or result[0] != 1:
                            await interaction.response.send_message(
                                "❌ Only global administrators can use this command.", 
                                ephemeral=True
                            )
                            return

                        self.settings_cursor.execute("""
                            SELECT id, is_initial FROM admin 
                            ORDER BY is_initial DESC, id
                        """)
                        admins = self.settings_cursor.fetchall()

                        if not admins:
                            await interaction.response.send_message(
                                "❌ No administrator registered in the system.", 
                                ephemeral=True
                            )
                            return

                        admin_select_embed = discord.Embed(
                            title="👤 Administrator Deletion",
                            description=(
                                "Select the administrator you want to delete:\n\n"
                                "**Administrator List**\n"
                                "━━━━━━━━━━━━━━━━━━━━━━\n"
                            ),
                            color=discord.Color.red()
                        )

                        options = []
                        for admin_id, is_initial in admins:
                            try:
                                user = await self.bot.fetch_user(admin_id)
                                admin_name = f"{user.name}"
                            except:
                                admin_name = "Unknown User"

                            options.append(
                                discord.SelectOption(
                                    label=f"{admin_name[:50]}",
                                    value=str(admin_id),
                                    description=f"{'Global Admin' if is_initial == 1 else 'Server Admin'} - ID: {admin_id}",
                                    emoji="👑" if is_initial == 1 else "👤"
                                )
                            )
                        
                        admin_select = discord.ui.Select(
                            placeholder="Select the administrator you want to delete...",
                            options=options,
                            custom_id="admin_select"
                        )

                        admin_view = discord.ui.View(timeout=None)
                        admin_view.add_item(admin_select)

                        async def admin_callback(select_interaction: discord.Interaction):
                            try:
                                selected_admin_id = int(select_interaction.data["values"][0])
                                
                                self.settings_cursor.execute("""
                                    SELECT id, is_initial FROM admin WHERE id = ?
                                """, (selected_admin_id,))
                                admin_info = self.settings_cursor.fetchone()

                                self.settings_cursor.execute("""
                                    SELECT alliances_id
                                    FROM adminserver
                                    WHERE admin = ?
                                """, (selected_admin_id,))
                                admin_alliances = self.settings_cursor.fetchall()

                                alliance_names = []
                                if admin_alliances: 
                                    alliance_ids = [alliance[0] for alliance in admin_alliances]
                                    
                                    alliance_cursor = self.alliance_db.cursor()
                                    placeholders = ','.join('?' * len(alliance_ids))
                                    query = f"SELECT alliance_id, name FROM alliance_list WHERE alliance_id IN ({placeholders})"
                                    alliance_cursor.execute(query, alliance_ids)
                                    
                                    alliance_results = alliance_cursor.fetchall()
                                    alliance_names = [alliance[1] for alliance in alliance_results]

                                try:
                                    user = await self.bot.fetch_user(selected_admin_id)
                                    admin_name = user.name
                                    avatar_url = user.display_avatar.url
                                except Exception as e:
                                    admin_name = f"Bilinmeyen Kullanıcı ({selected_admin_id})"
                                    avatar_url = None

                                info_embed = discord.Embed(
                                    title="⚠️ Administrator Deletion Confirmation",
                                    description=(
                                        f"**Administrator Information**\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"👤 **Name:** `{admin_name}`\n"
                                        f"🆔 **Discord ID:** `{selected_admin_id}`\n"
                                        f"👤 **Access Level:** `{'Global Admin' if admin_info[1] == 1 else 'Server Admin'}`\n"
                                        f"🔍 **Access Type:** `{'All Alliances' if admin_info[1] == 1 else 'Server + Special Access'}`\n"
                                        f"📊 **Available Alliances:** `{len(alliance_names)}`\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                                    ),
                                    color=discord.Color.yellow()
                                )

                                if alliance_names:
                                    info_embed.add_field(
                                        name="🏰 Alliances Authorized",
                                        value="\n".join([f"• {name}" for name in alliance_names[:10]]) + 
                                              ("\n• ..." if len(alliance_names) > 10 else ""),
                                        inline=False
                                    )
                                else:
                                    info_embed.add_field(
                                        name="🏰 Alliances Authorized",
                                        value="This manager does not yet have an authorized alliance.",
                                        inline=False
                                    )

                                if avatar_url:
                                    info_embed.set_thumbnail(url=avatar_url)

                                confirm_view = discord.ui.View()
                                
                                confirm_button = discord.ui.Button(
                                    label="Confirm", 
                                    style=discord.ButtonStyle.danger,
                                    custom_id="confirm_remove"
                                )
                                cancel_button = discord.ui.Button(
                                    label="Cancel", 
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="cancel_remove"
                                )

                                async def confirm_callback(button_interaction: discord.Interaction):
                                    try:
                                        self.settings_cursor.execute("DELETE FROM adminserver WHERE admin = ?", (selected_admin_id,))
                                        self.settings_cursor.execute("DELETE FROM admin WHERE id = ?", (selected_admin_id,))
                                        self.settings_db.commit()

                                        success_embed = discord.Embed(
                                            title="✅ Administrator Deleted Successfully",
                                            description=(
                                                f"**Deleted Administrator**\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"👤 **Name:** `{admin_name}`\n"
                                                f"🆔 **Discord ID:** `{selected_admin_id}`\n"
                                            ),
                                            color=discord.Color.green()
                                        )
                                        
                                        await button_interaction.response.edit_message(
                                            embed=success_embed,
                                            view=None
                                        )
                                    except Exception as e:
                                        await button_interaction.response.send_message(
                                            "❌ An error occurred while deleting the administrator.",
                                            ephemeral=True
                                        )

                                async def cancel_callback(button_interaction: discord.Interaction):
                                    cancel_embed = discord.Embed(
                                        title="❌ Process Canceled",
                                        description="Administrator deletion canceled.",
                                        color=discord.Color.red()
                                    )
                                    await button_interaction.response.edit_message(
                                        embed=cancel_embed,
                                        view=None
                                    )

                                confirm_button.callback = confirm_callback
                                cancel_button.callback = cancel_callback

                                confirm_view.add_item(confirm_button)
                                confirm_view.add_item(cancel_button)

                                await select_interaction.response.edit_message(
                                    embed=info_embed,
                                    view=confirm_view
                                )

                            except Exception as e:
                                await select_interaction.response.send_message(
                                    "❌ An error occurred during processing.",
                                    ephemeral=True
                                )

                        admin_select.callback = admin_callback

                        await interaction.response.send_message(
                            embed=admin_select_embed,
                            view=admin_view,
                            ephemeral=True
                        )

                    except Exception as e:
                        print(f"Remove admin error: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "❌ An error occurred during the administrator deletion process.",
                                ephemeral=True
                            )

                elif custom_id == "main_menu":
                    try:
                        alliance_cog = self.bot.get_cog("Alliance")
                        if alliance_cog:
                            await alliance_cog.show_main_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Ana menüye dönüş sırasında bir hata oluştu.",
                                ephemeral=True
                            )
                    except Exception as e:
                        print(f"[ERROR] Main Menu error in bot operations: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while returning to main menu.", 
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while returning to main menu.",
                                ephemeral=True
                            )

            except Exception as e:
                if not interaction.response.is_done():
                    print(f"Error processing {custom_id}: {e}")
                    await interaction.response.send_message(
                        "An error occurred while processing your request.",
                        ephemeral=True
                    )

        elif custom_id == "view_admin_permissions":
            try:
                with sqlite3.connect('db/settings.sqlite') as settings_db:
                    cursor = settings_db.cursor()
                    cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                    result = cursor.fetchone()
                    
                    if not result or result[0] != 1:
                        await interaction.response.send_message(
                            "❌ Only global administrators can use this command.", 
                            ephemeral=True
                        )
                        return

                    with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                        alliance_cursor = alliance_db.cursor()
                        
                        cursor.execute("""
                            SELECT a.id, a.is_initial, admin_server.alliances_id
                            FROM admin a
                            JOIN adminserver admin_server ON a.id = admin_server.admin
                            ORDER BY a.is_initial DESC, a.id
                        """)
                        admin_permissions = cursor.fetchall()

                        if not admin_permissions:
                            await interaction.response.send_message(
                                "No admin permissions found.", 
                                ephemeral=True
                            )
                            return

                        admin_alliance_info = []
                        for admin_id, is_initial, alliance_id in admin_permissions:
                            alliance_cursor.execute("""
                                SELECT name FROM alliance_list 
                                WHERE alliance_id = ?
                            """, (alliance_id,))
                            alliance_result = alliance_cursor.fetchone()
                            if alliance_result:
                                admin_alliance_info.append((admin_id, is_initial, alliance_id, alliance_result[0]))

                        embed = discord.Embed(
                            title="👥 Admin Alliance Permissions",
                            description=(
                                "Select an admin to view or modify permissions:\n\n"
                                "**Admin List**\n"
                                "━━━━━━━━━━━━━━━━━━━━━━\n"
                            ),
                            color=discord.Color.blue()
                        )

                        options = []
                        for admin_id, is_initial, alliance_id, alliance_name in admin_alliance_info:
                            try:
                                user = await interaction.client.fetch_user(admin_id)
                                admin_name = user.name
                            except:
                                admin_name = f"Unknown User ({admin_id})"

                            option_label = f"{admin_name[:50]}"
                            option_desc = f"Alliance: {alliance_name[:50]}"
                            
                            options.append(
                                discord.SelectOption(
                                    label=option_label,
                                    value=f"{admin_id}:{alliance_id}",
                                    description=option_desc,
                                    emoji="👑" if is_initial == 1 else "👤"
                                )
                            )

                        if not options:
                            await interaction.response.send_message(
                                "No admin-alliance permissions found.", 
                                ephemeral=True
                            )
                            return

                        select = discord.ui.Select(
                            placeholder="Select an admin to remove permission...",
                            options=options,
                            custom_id="admin_permission_select"
                        )

                        async def select_callback(select_interaction: discord.Interaction):
                            try:
                                admin_id, alliance_id = select.values[0].split(":")
                                
                                confirm_embed = discord.Embed(
                                    title="⚠️ Confirm Permission Removal",
                                    description=(
                                        f"Are you sure you want to remove the alliance permission?\n\n"
                                        f"**Admin:** {admin_name} ({admin_id})\n"
                                        f"**Alliance:** {alliance_name} ({alliance_id})"
                                    ),
                                    color=discord.Color.yellow()
                                )

                                confirm_view = discord.ui.View()
                                
                                async def confirm_callback(confirm_interaction: discord.Interaction):
                                    try:
                                        success = await self.confirm_permission_removal(int(admin_id), int(alliance_id), confirm_interaction)
                                        
                                        if success:
                                            success_embed = discord.Embed(
                                                title="✅ Permission Removed",
                                                description=(
                                                    f"Successfully removed alliance permission:\n\n"
                                                    f"**Admin:** {admin_name} ({admin_id})\n"
                                                    f"**Alliance:** {alliance_name} ({alliance_id})"
                                                ),
                                                color=discord.Color.green()
                                            )
                                            await confirm_interaction.response.edit_message(
                                                embed=success_embed,
                                                view=None
                                            )
                                        else:
                                            await confirm_interaction.response.send_message(
                                                "An error occurred while removing the permission.",
                                                ephemeral=True
                                            )
                                    except Exception as e:
                                        print(f"Confirm callback error: {e}")
                                        await confirm_interaction.response.send_message(
                                            "An error occurred while removing the permission.",
                                            ephemeral=True
                                        )

                                async def cancel_callback(cancel_interaction: discord.Interaction):
                                    cancel_embed = discord.Embed(
                                        title="❌ Operation Cancelled",
                                        description="Permission removal has been cancelled.",
                                        color=discord.Color.red()
                                    )
                                    await cancel_interaction.response.edit_message(
                                        embed=cancel_embed,
                                        view=None
                                    )

                                confirm_button = discord.ui.Button(
                                    label="Confirm",
                                    style=discord.ButtonStyle.danger,
                                    custom_id="confirm_remove"
                                )
                                confirm_button.callback = confirm_callback
                                
                                cancel_button = discord.ui.Button(
                                    label="Cancel",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="cancel_remove"
                                )
                                cancel_button.callback = cancel_callback

                                confirm_view.add_item(confirm_button)
                                confirm_view.add_item(cancel_button)

                                await select_interaction.response.edit_message(
                                    embed=confirm_embed,
                                    view=confirm_view
                                )

                            except Exception as e:
                                print(f"Select callback error: {e}")
                                await select_interaction.response.send_message(
                                    "An error occurred while processing your selection.",
                                    ephemeral=True
                                )

                        select.callback = select_callback
                        
                        view = discord.ui.View()
                        view.add_item(select)

                        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

            except Exception as e:
                print(f"View admin permissions error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while loading admin permissions.",
                        ephemeral=True
                    )

        elif custom_id == "view_administrators":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if not result or result[0] != 1:
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                self.settings_cursor.execute("""
                    SELECT a.id, a.is_initial 
                    FROM admin a
                    ORDER BY a.is_initial DESC, a.id
                """)
                admins = self.settings_cursor.fetchall()

                if not admins:
                    await interaction.response.send_message(
                        "❌ No administrators found in the system.", 
                        ephemeral=True
                    )
                    return

                admin_list_embed = discord.Embed(
                    title="👥 Administrator List",
                    description="List of all administrators and their permissions:\n━━━━━━━━━━━━━━━━━━━━━━",
                    color=discord.Color.blue()
                )

                for admin_id, is_initial in admins:
                    try:
                        user = await self.bot.fetch_user(admin_id)
                        admin_name = user.name
                        admin_avatar = user.display_avatar.url

                        self.settings_cursor.execute("""
                            SELECT alliances_id 
                            FROM adminserver 
                            WHERE admin = ?
                        """, (admin_id,))
                        alliance_ids = self.settings_cursor.fetchall()

                        alliance_names = []
                        if alliance_ids:
                            alliance_id_list = [aid[0] for aid in alliance_ids]
                            placeholders = ','.join('?' * len(alliance_id_list))
                            self.c_alliance.execute(f"""
                                SELECT name 
                                FROM alliance_list 
                                WHERE alliance_id IN ({placeholders})
                            """, alliance_id_list)
                            alliance_names = [name[0] for name in self.c_alliance.fetchall()]

                        admin_info = (
                            f"👤 **Name:** {admin_name}\n"
                            f"🆔 **ID:** {admin_id}\n"
                            f"👑 **Role:** {'Global Admin' if is_initial == 1 else 'Server Admin'}\n"
                            f"🔍 **Access Type:** {'All Alliances' if is_initial == 1 else 'Server + Special Access'}\n"
                        )

                        if alliance_names:
                            alliance_text = "\n".join([f"• {name}" for name in alliance_names[:5]])
                            if len(alliance_names) > 5:
                                alliance_text += f"\n• ... and {len(alliance_names) - 5} more"
                            admin_info += f"🏰 **Managing Alliances:**\n{alliance_text}\n"
                        else:
                            admin_info += "🏰 **Managing Alliances:** No alliances assigned\n"

                        admin_list_embed.add_field(
                            name=f"{'👑' if is_initial == 1 else '👤'} {admin_name}",
                            value=f"{admin_info}\n━━━━━━━━━━━━━━━━━━━━━━",
                            inline=False
                        )

                    except Exception as e:
                        print(f"Error processing admin {admin_id}: {e}")
                        admin_list_embed.add_field(
                            name=f"Unknown User ({admin_id})",
                            value="Error loading administrator information\n━━━━━━━━━━━━━━━━━━━━━━",
                            inline=False
                        )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Back to Bot Operations",
                    emoji="◀️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="bot_operations",
                    row=0
                ))

                await interaction.response.send_message(
                    embed=admin_list_embed,
                    view=view,
                    ephemeral=True
                )

            except Exception as e:
                print(f"View administrators error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while loading administrator list.",
                        ephemeral=True
                    )

        elif custom_id == "transfer_old_database":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if not result or result[0] != 1:
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                database_cog = self.bot.get_cog('DatabaseTransfer')
                if database_cog:
                    await database_cog.transfer_old_database(interaction)
                else:
                    await interaction.response.send_message(
                        "❌ Database transfer module not loaded.", 
                        ephemeral=True
                    )

            except Exception as e:
                print(f"Transfer old database error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while transferring the database.",
                        ephemeral=True
                    )

        elif custom_id == "check_updates":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if not result or result[0] != 1:
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                current_version, new_version, update_notes, updates_needed = await self.check_for_updates()

                if not current_version or not new_version:
                    await interaction.response.send_message(
                        "❌ Failed to check for updates. Please try again later.", 
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="🔄 Bot Update Status",
                    color=discord.Color.blue() if not updates_needed else discord.Color.yellow()
                )

                embed.add_field(
                    name="Current Version",
                    value=f"`{current_version}`",
                    inline=True
                )

                if updates_needed:
                    embed.add_field(
                        name="Updates Available",
                        value="The following files need to be updated:",
                        inline=False
                    )

                    update_text = ""
                    for update in updates_needed:
                        update_text += f"• `{update['file']}`: {update['current']} → {update['new']}\n"
                    embed.add_field(
                        name="Files to Update",
                        value=update_text,
                        inline=False
                    )
                    
                    if update_notes:
                        notes_text = "\n".join([f"• {note}" for note in update_notes])
                        embed.add_field(
                            name="Update Notes",
                            value=notes_text,
                            inline=False
                        )
                    
                    embed.add_field(
                        name="How to Update",
                        value=(
                            "To update to the new version:\n"
                            "1. Restart the bot\n"
                            "2. When prompted, accept the update installation\n\n"
                            "**Note:** The bot will automatically restart after the update."
                        ),
                        inline=False
                    )
                else:
                    embed.description = "✅ Your bot is up to date!"

                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True
                )

            except Exception as e:
                print(f"Check updates error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while checking for updates.",
                        ephemeral=True
                    )

    async def show_bot_operations_menu(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="🤖 Bot Operations",
                description=(
                    "Please choose an operation:\n\n"
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "👥 **Admin Management**\n"
                    "└ Manage bot administrators\n\n"
                    "🔍 **Admin Permissions**\n"
                    "└ View and manage admin permissions\n\n"
                    "🔄 **Bot Updates**\n"
                    "└ Check and manage updates\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Add Admin",
                emoji="➕",
                style=discord.ButtonStyle.success,
                custom_id="add_admin",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Remove Admin",
                emoji="➖",
                style=discord.ButtonStyle.danger,
                custom_id="remove_admin",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="View Administrators",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id="view_administrators",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Assign Alliance to Admin",
                emoji="🔗",
                style=discord.ButtonStyle.success,
                custom_id="assign_alliance",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Delete Admin Permissions",
                emoji="➖",
                style=discord.ButtonStyle.danger,
                custom_id="view_admin_permissions",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Transfer Old Database",
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                custom_id="transfer_old_database",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Check for Updates",
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                custom_id="check_updates",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Log System",
                emoji="📋",
                style=discord.ButtonStyle.primary,
                custom_id="log_system",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Alliance Control Messages",
                emoji="💬",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_control_messages",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Main Menu",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="main_menu",
                row=4
            ))

            await interaction.response.edit_message(embed=embed, view=view)

        except Exception as e:
            if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                print(f"Show bot operations menu error: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing the menu.",
                    ephemeral=True
                )

    async def confirm_permission_removal(self, admin_id: int, alliance_id: int, confirm_interaction: discord.Interaction):
        try:
            self.settings_cursor.execute("""
                DELETE FROM adminserver 
                WHERE admin = ? AND alliances_id = ?
            """, (admin_id, alliance_id))
            self.settings_db.commit()
            return True
        except Exception as e:
            return False

    async def check_for_updates(self):
        try:
            response = requests.get(VERSION_URL)
            if response.status_code != 200:
                return None, None, [], []

            content = response.text.split('\n')
            documents = {}
            updates_needed = []
            update_notes = []

            doc_section = False
            update_section = False
            
            for line in content:
                line = line.strip()
                
                if line == "Documants;":
                    doc_section = True
                    continue
                elif line == "Updated Info;":
                    doc_section = False
                    update_section = True
                    continue
                elif doc_section and '=' in line:
                    file_name, version = [x.strip() for x in line.split('=')]
                    documents[file_name] = version
                elif update_section and line and line != "Updated Info;":
                    if line.startswith("- "):
                        update_notes.append(line)

            with sqlite3.connect('db/settings.sqlite') as conn:
                cursor = conn.cursor()
                
                for file_name, new_version in documents.items():
                    cursor.execute("SELECT version FROM versions WHERE file_name = ?", (file_name,))
                    current = cursor.fetchone()
                    current_version = current[0] if current else "No Version"
                    
                    if not current or current_version != new_version:
                        updates_needed.append({
                            'file': file_name,
                            'current': current_version,
                            'new': new_version
                        })

                cursor.execute("SELECT version FROM versions WHERE file_name = 'main.py'")
                result = cursor.fetchone()
                current_main_version = result[0] if result else "No Version"
                new_main_version = documents.get('main.py', "Unknown")

            return current_main_version, new_main_version, update_notes, updates_needed

        except Exception as e:
            print(f"Error checking for updates: {e}")
            return None, None, [], []

async def setup(bot):
    await bot.add_cog(BotOperations(bot, sqlite3.connect('db/settings.sqlite'))) 
