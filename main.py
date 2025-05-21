import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import pytz
import json
import os
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# File to store user data and server configurations
DATA_FILE = "bot_data.json"

# Default settings that can be customized per server
DEFAULT_SETTINGS = {
    "timezone": "Africa/Cairo",
    "work_start_hour": 9,  # 9:00 AM local time
    "workday_duration_hours": 9,
    "update_reminder_hours": 2,
    "max_break_minutes": 60,  # 1 hour daily break limit
    "break_reminder_minutes": 10,
    "max_break_reminders": 3,
    "employee_role_name": "employee"
}

# Data structure
bot_data = {
    "servers": {},
    "users": {}
}

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

def load_data():
    global bot_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                bot_data = json.load(f)
        except json.JSONDecodeError:
            bot_data = {"servers": {}, "users": {}}
    else:
        bot_data = {"servers": {}, "users": {}}

def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump(bot_data, f)

def get_server_settings(guild_id):
    str_guild_id = str(guild_id)
    if str_guild_id not in bot_data["servers"]:
        bot_data["servers"][str_guild_id] = DEFAULT_SETTINGS.copy()
        save_data()
    return bot_data["servers"][str_guild_id]

def get_user_data(user_id, guild_id):
    # Create a unique key combining user ID and guild ID
    key = f"{user_id}:{guild_id}"
    if key not in bot_data["users"]:
        bot_data["users"][key] = {
            "workday_started": False,
            "workday_start_time": None,
            "next_update_time": None,
            "workday_end_time": None,
            "on_break": False,
            "break_start_time": None,
            "total_break_minutes": 0,
            "break_reminders_sent": 0,
            "daily_updates": []
        }
        save_data()
    return bot_data["users"][key]

def reset_user_daily_data(user_id, guild_id):
    key = f"{user_id}:{guild_id}"
    bot_data["users"][key] = {
        "workday_started": False,
        "workday_start_time": None,
        "next_update_time": None,
        "workday_end_time": None,
        "on_break": False,
        "break_start_time": None,
        "total_break_minutes": 0,
        "break_reminders_sent": 0,
        "daily_updates": []
    }
    save_data()

def get_local_time(guild_id):
    settings = get_server_settings(guild_id)
    timezone = pytz.timezone(settings["timezone"])
    return datetime.datetime.now(timezone)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    load_data()
    check_work_start.start()
    check_updates.start()
    check_breaks.start()
    keep_alive()

@tasks.loop(minutes=1)
async def check_work_start():
    for guild in bot.guilds:
        settings = get_server_settings(guild.id)
        now = get_local_time(guild.id)
        
        # Check if it's work start hour (default 9:00 AM) in the server's timezone
        if now.hour == settings["work_start_hour"] and now.minute == 0:
            employee_role = discord.utils.get(guild.roles, name=settings["employee_role_name"])
            if employee_role:
                # Reset all employee data for the new day
                for member in guild.members:
                    if employee_role in member.roles:
                        reset_user_daily_data(member.id, guild.id)
                
                # Find a general channel to send the message
                general_channel = None
                for channel in guild.text_channels:
                    if channel.name.lower() in ["general", "main", "chat"]:
                        general_channel = channel
                        break
                
                if general_channel:
                    await general_channel.send(f"{employee_role.mention} Work day starts now! Type !start to begin your workday.")

@tasks.loop(minutes=1)
async def check_updates():
    for guild in bot.guilds:
        settings = get_server_settings(guild.id)
        now = get_local_time(guild.id)
        
        employee_role = discord.utils.get(guild.roles, name=settings["employee_role_name"])
        if employee_role:
            for member in guild.members:
                if employee_role in member.roles:
                    user_data = get_user_data(member.id, guild.id)
                    
                    # Skip if user hasn't started workday
                    if not user_data["workday_started"]:
                        continue
                    
                    # Check if it's time for an update reminder
                    if user_data["next_update_time"]:
                        next_update = datetime.datetime.fromisoformat(user_data["next_update_time"])
                        if now >= next_update:
                            # Find a channel to send the reminder
                            for channel in guild.text_channels:
                                if channel.permissions_for(guild.me).send_messages:
                                    await channel.send(f"{member.mention} update required")
                                    
                                    # Set next update time
                                    next_time = now + datetime.timedelta(hours=settings["update_reminder_hours"])
                                    user_data["next_update_time"] = next_time.isoformat()
                                    save_data()
                                    break

@tasks.loop(minutes=1)
async def check_breaks():
    for guild in bot.guilds:
        settings = get_server_settings(guild.id)
        now = get_local_time(guild.id)
        
        for member in guild.members:
            user_data = get_user_data(member.id, guild.id)
            
            # Skip if user isn't on break
            if not user_data["on_break"] or not user_data["break_start_time"]:
                continue
            
            break_start = datetime.datetime.fromisoformat(user_data["break_start_time"])
            elapsed_minutes = (now - break_start).total_seconds() / 60
            
            # Send break reminders every X minutes, up to MAX times
            if user_data["break_reminders_sent"] < settings["max_break_reminders"]:
                reminder_time = break_start + datetime.timedelta(minutes=(user_data["break_reminders_sent"] + 1) * settings["break_reminder_minutes"])
                
                if now >= reminder_time:
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).send_messages:
                            await channel.send(f"{member.mention} you're on break. Please type !back when you return.")
                            user_data["break_reminders_sent"] += 1
                            save_data()
                            break

@bot.command()
async def start(ctx):
    settings = get_server_settings(ctx.guild.id)
    user_data = get_user_data(ctx.author.id, ctx.guild.id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=settings["employee_role_name"])
    if employee_role not in ctx.author.roles:
        await ctx.send(f"You don't have the {settings['employee_role_name']} role.")
        return
    
    now = get_local_time(ctx.guild.id)
    
    # Check if already started
    if user_data["workday_started"]:
        await ctx.send("You've already started your workday.")
        return
    
    # Calculate late minutes if after start hour
    late_minutes = 0
    if now.hour > settings["work_start_hour"] or (now.hour == settings["work_start_hour"] and now.minute > 0):
        start_time = now.replace(hour=settings["work_start_hour"], minute=0, second=0, microsecond=0)
        late_minutes = (now - start_time).total_seconds() / 60
    
    # Set up workday
    user_data["workday_started"] = True
    user_data["workday_start_time"] = now.isoformat()
    
    # Calculate workday end time from now, not counting breaks
    end_time = now + datetime.timedelta(hours=settings["workday_duration_hours"])
    user_data["workday_end_time"] = end_time.isoformat()
    
    # Set first update reminder time
    next_update = now + datetime.timedelta(hours=settings["update_reminder_hours"])
    user_data["next_update_time"] = next_update.isoformat()
    
    save_data()
    
    response = f"Workday started. Next update due in {settings['update_reminder_hours']} hours."
    if late_minutes > 0:
        response += f"\nYou were {int(late_minutes)} minutes late today."
        
        # Log the late start
        logs_channel = discord.utils.get(ctx.guild.text_channels, name="logs")
        if logs_channel:
            await logs_channel.send(f"{ctx.author.mention} started work {int(late_minutes)} minutes late today.")
    
    await ctx.send(response)

async def break_cmd(ctx):
    settings = get_server_settings(ctx.guild.id)
    user_data = get_user_data(ctx.author.id, ctx.guild.id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=settings["employee_role_name"])
    if employee_role not in ctx.author.roles:
        await ctx.send(f"You don't have the {settings['employee_role_name']} role.")
        return
    
    # Check if workday started
    if not user_data["workday_started"]:
        await ctx.send("You haven't started your workday yet. Type !start first.")
        return
    
    # Check if already on break
    if user_data["on_break"]:
        await ctx.send("You're already on break. Type !back when you return.")
        return
    
    # Check if break limit reached
    if user_data["total_break_minutes"] >= settings["max_break_minutes"]:
        await ctx.send(f"You've already used your entire break allowance for today ({settings['max_break_minutes']} minutes).")
        return
    
    now = get_local_time(ctx.guild.id)
    user_data["on_break"] = True
    user_data["break_start_time"] = now.isoformat()
    user_data["break_reminders_sent"] = 0
    save_data()
    
    remaining_break = settings["max_break_minutes"] - user_data["total_break_minutes"]
    await ctx.send(f"Break started. You have {remaining_break} minutes of break time available for today.")

# Using "break_cmd" as the function name since "break" is a Python keyword
bot.remove_command("break")
# Register the break command
bot.command(name="break")(break_cmd)

@bot.command()
async def back(ctx):
    settings = get_server_settings(ctx.guild.id)
    user_data = get_user_data(ctx.author.id, ctx.guild.id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=settings["employee_role_name"])
    if employee_role not in ctx.author.roles:
        await ctx.send(f"You don't have the {settings['employee_role_name']} role.")
        return
    
    # Check if on break
    if not user_data["on_break"] or not user_data["break_start_time"]:
        await ctx.send("You weren't on break.")
        return
    
    now = get_local_time(ctx.guild.id)
    break_start = datetime.datetime.fromisoformat(user_data["break_start_time"])
    break_minutes = int((now - break_start).total_seconds() / 60)
    
    user_data["on_break"] = False
    user_data["break_start_time"] = None
    user_data["total_break_minutes"] += break_minutes
    user_data["break_reminders_sent"] = 0
    save_data()
    
    # Extend workday end time by the break duration
    if user_data["workday_end_time"]:
        end_time = datetime.datetime.fromisoformat(user_data["workday_end_time"])
        new_end_time = end_time + datetime.timedelta(minutes=break_minutes)
        user_data["workday_end_time"] = new_end_time.isoformat()
        save_data()
    
    remaining_break = max(0, settings["max_break_minutes"] - user_data["total_break_minutes"])
    await ctx.send(f"Welcome back! This break was {break_minutes} minutes.\nYou've used {user_data['total_break_minutes']} minutes of break time today.\nYou have {remaining_break} minutes remaining.")

@bot.command()
async def done(ctx):
    settings = get_server_settings(ctx.guild.id)
    user_data = get_user_data(ctx.author.id, ctx.guild.id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=settings["employee_role_name"])
    if employee_role not in ctx.author.roles:
        await ctx.send(f"You don't have the {settings['employee_role_name']} role.")
        return
    
    # Check if workday started
    if not user_data["workday_started"]:
        await ctx.send("You haven't started your workday yet.")
        return
    
    # Check if on break
    if user_data["on_break"]:
        await ctx.send("Please end your break with !back before ending your workday.")
        return
    
    now = get_local_time(ctx.guild.id)
    start_time = datetime.datetime.fromisoformat(user_data["workday_start_time"])
    total_hours = (now - start_time).total_seconds() / 3600 - (user_data["total_break_minutes"] / 60)
    
    # Log the workday completion
    logs_channel = discord.utils.get(ctx.guild.text_channels, name="logs")
    if logs_channel:
        # Compile all updates
        daily_summary = f"**Daily Log for {ctx.author.display_name} - {now.strftime('%Y-%m-%d')}**\n\n"
        daily_summary += f"- Workday started at: {start_time.strftime('%H:%M')}\n"
        daily_summary += f"- Total break time: {user_data['total_break_minutes']} minutes\n"
        daily_summary += f"- Total work time: {total_hours:.2f} hours\n\n"
        
        if user_data["daily_updates"]:
            daily_summary += "**Updates throughout the day:**\n"
            for i, update in enumerate(user_data["daily_updates"], 1):
                daily_summary += f"{i}. {update}\n"
        else:
            daily_summary += "No updates were recorded today."
        
        await logs_channel.send(daily_summary)
    
    # Reset user data
    reset_user_daily_data(ctx.author.id, ctx.guild.id)
    
    await ctx.send("Workday completed. Have a great day!")

# Server configuration commands
@bot.command()
@commands.has_permissions(administrator=True)
async def config(ctx, setting=None, *, value=None):
    """View or change server configuration settings. Admin only."""
    settings = get_server_settings(ctx.guild.id)
    
    # If no parameters, show all settings
    if setting is None:
        config_msg = "**Current Server Configuration:**\n"
        for key, val in settings.items():
            config_msg += f"- **{key}**: {val}\n"
        config_msg += "\nUse `!config <setting> <value>` to change a setting."
        await ctx.send(config_msg)
        return
        
    # Check if the setting exists
    if setting not in DEFAULT_SETTINGS:
        valid_settings = ", ".join(DEFAULT_SETTINGS.keys())
        await ctx.send(f"Invalid setting. Valid settings are: {valid_settings}")
        return
        
    # If no value provided, show current value
    if value is None:
        await ctx.send(f"Current value of **{setting}** is: {settings[setting]}")
        return
        
    # Update the setting with appropriate type conversion
    try:
        if setting in ["work_start_hour", "workday_duration_hours", "update_reminder_hours", 
                      "max_break_minutes", "break_reminder_minutes", "max_break_reminders"]:
            settings[setting] = int(value)
        elif setting == "timezone":
            # Validate timezone
            try:
                pytz.timezone(value)
                settings[setting] = value
            except pytz.exceptions.UnknownTimeZoneError:
                await ctx.send(f"Unknown timezone: {value}. Please use a valid timezone identifier.")
                return
        else:
            settings[setting] = value
            
        save_data()
        await ctx.send(f"Updated **{setting}** to: {settings[setting]}")
    except ValueError:
        await ctx.send(f"Invalid value format for {setting}.")

@bot.event
async def on_message(message):
    # Skip bot messages
    if message.author.bot:
        return
    
    # Process commands first
    await bot.process_commands(message)
    
    # Then check for updates to log
    if message.guild:  # Make sure it's a guild message, not a DM
        settings = get_server_settings(message.guild.id)
        employee_role = discord.utils.get(message.guild.roles, name=settings["employee_role_name"])
        
        if employee_role and employee_role in message.author.roles:
            user_data = get_user_data(message.author.id, message.guild.id)
            
            # If workday has started and message isn't a command
            if user_data["workday_started"] and not message.content.startswith('!'):
                # Store the update
                now = get_local_time(message.guild.id)
                user_data["daily_updates"].append(f"[{now.strftime('%H:%M')}] {message.content}")
                save_data()

if __name__ == "__main__":
    bot.run(os.environ.get('DISCORD_TOKEN'))