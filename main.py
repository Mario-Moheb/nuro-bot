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

EGYPT_TIMEZONE = pytz.timezone('Africa/Cairo')
WORK_START_HOUR = 9  # 9:00 AM Egypt time
WORKDAY_DURATION_HOURS = 9
UPDATE_REMINDER_HOURS = 2
MAX_BREAK_MINUTES = 60  # 1 hour daily break limit
BREAK_REMINDER_MINUTES = 10
MAX_BREAK_REMINDERS = 3
EMPLOYEE_ROLE_NAME = "employee"

# File to store user data
DATA_FILE = "employee_data.json"

# Data structure
employee_data = {}

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
    global employee_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                employee_data = json.load(f)
        except json.JSONDecodeError:
            employee_data = {}
    else:
        employee_data = {}
        
def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump(employee_data, f)

def get_user_data(user_id):
    str_id = str(user_id)
    if str_id not in employee_data:
        employee_data[str_id] = {
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
    return employee_data[str_id]

def reset_user_daily_data(user_id):
    user_data = get_user_data(user_id)
    user_data["workday_started"] = False
    user_data["workday_start_time"] = None
    user_data["next_update_time"] = None
    user_data["workday_end_time"] = None
    user_data["on_break"] = False
    user_data["break_start_time"] = None
    user_data["total_break_minutes"] = 0
    user_data["break_reminders_sent"] = 0
    user_data["daily_updates"] = []
    save_data()

def get_egypt_time():
    return datetime.datetime.now(EGYPT_TIMEZONE)

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
    now = get_egypt_time()
    
    # Check if it's 9:00 AM Egypt time
    if now.hour == WORK_START_HOUR and now.minute == 0:
        for guild in bot.guilds:
            employee_role = discord.utils.get(guild.roles, name=EMPLOYEE_ROLE_NAME)
            if employee_role:
                # Reset all employee data for the new day
                for member in guild.members:
                    if employee_role in member.roles:
                        reset_user_daily_data(member.id)
                
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
    now = get_egypt_time()
    
    for guild in bot.guilds:
        employee_role = discord.utils.get(guild.roles, name=EMPLOYEE_ROLE_NAME)
        if employee_role:
            for member in guild.members:
                if employee_role in member.roles:
                    user_data = get_user_data(member.id)
                    
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
                                    next_time = now + datetime.timedelta(hours=UPDATE_REMINDER_HOURS)
                                    user_data["next_update_time"] = next_time.isoformat()
                                    save_data()
                                    break

@tasks.loop(minutes=1)
async def check_breaks():
    now = get_egypt_time()
    
    for guild in bot.guilds:
        for member in guild.members:
            user_data = get_user_data(member.id)
            
            # Skip if user isn't on break
            if not user_data["on_break"] or not user_data["break_start_time"]:
                continue
            
            break_start = datetime.datetime.fromisoformat(user_data["break_start_time"])
            elapsed_minutes = (now - break_start).total_seconds() / 60
            
            # Send break reminders every 10 minutes, up to 3 times
            if user_data["break_reminders_sent"] < MAX_BREAK_REMINDERS:
                reminder_time = break_start + datetime.timedelta(minutes=(user_data["break_reminders_sent"] + 1) * BREAK_REMINDER_MINUTES)
                
                if now >= reminder_time:
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).send_messages:
                            await channel.send(f"{member.mention} you're on break. Please type !back when you return.")
                            user_data["break_reminders_sent"] += 1
                            save_data()
                            break

@bot.command()
async def start(ctx):
    user_id = ctx.author.id
    user_data = get_user_data(user_id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=EMPLOYEE_ROLE_NAME)
    if employee_role not in ctx.author.roles:
        await ctx.send("You don't have the employee role.")
        return
    
    now = get_egypt_time()
    
    # Check if already started
    if user_data["workday_started"]:
        await ctx.send("You've already started your workday.")
        return
    
    # Calculate late minutes if after 9 AM
    late_minutes = 0
    if now.hour > WORK_START_HOUR or (now.hour == WORK_START_HOUR and now.minute > 0):
        start_time = now.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
        late_minutes = (now - start_time).total_seconds() / 60
    
    # Set up workday
    user_data["workday_started"] = True
    user_data["workday_start_time"] = now.isoformat()
    
    # Calculate workday end time (9 hours from now, not counting breaks)
    end_time = now + datetime.timedelta(hours=WORKDAY_DURATION_HOURS)
    user_data["workday_end_time"] = end_time.isoformat()
    
    # Set first update reminder time (2 hours from now)
    next_update = now + datetime.timedelta(hours=UPDATE_REMINDER_HOURS)
    user_data["next_update_time"] = next_update.isoformat()
    
    save_data()
    
    response = "Workday started. Next update due in 2 hours."
    if late_minutes > 0:
        response += f"\nYou were {int(late_minutes)} minutes late today."
        
        # Log the late start
        logs_channel = discord.utils.get(ctx.guild.text_channels, name="logs")
        if logs_channel:
            await logs_channel.send(f"{ctx.author.mention} started work {int(late_minutes)} minutes late today.")
    
    await ctx.send(response)

async def break_cmd(ctx):
    user_id = ctx.author.id
    user_data = get_user_data(user_id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=EMPLOYEE_ROLE_NAME)
    if employee_role not in ctx.author.roles:
        await ctx.send("You don't have the employee role.")
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
    if user_data["total_break_minutes"] >= MAX_BREAK_MINUTES:
        await ctx.send(f"You've already used your entire break allowance for today ({MAX_BREAK_MINUTES} minutes).")
        return
    
    now = get_egypt_time()
    user_data["on_break"] = True
    user_data["break_start_time"] = now.isoformat()
    user_data["break_reminders_sent"] = 0
    save_data()
    
    remaining_break = MAX_BREAK_MINUTES - user_data["total_break_minutes"]
    await ctx.send(f"Break started. You have {remaining_break} minutes of break time available for today.")

# Using "break_cmd" as the function name since "break" is a Python keyword
bot.remove_command("break")
# Register the break command
bot.command(name="break")(break_cmd)

@bot.command()
async def back(ctx):
    user_id = ctx.author.id
    user_data = get_user_data(user_id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=EMPLOYEE_ROLE_NAME)
    if employee_role not in ctx.author.roles:
        await ctx.send("You don't have the employee role.")
        return
    
    # Check if on break
    if not user_data["on_break"] or not user_data["break_start_time"]:
        await ctx.send("You weren't on break.")
        return
    
    now = get_egypt_time()
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
    
    remaining_break = max(0, MAX_BREAK_MINUTES - user_data["total_break_minutes"])
    await ctx.send(f"Welcome back! This break was {break_minutes} minutes.\nYou've used {user_data['total_break_minutes']} minutes of break time today.\nYou have {remaining_break} minutes remaining.")

@bot.command()
async def done(ctx):
    user_id = ctx.author.id
    user_data = get_user_data(user_id)
    
    # Check if user has employee role
    employee_role = discord.utils.get(ctx.guild.roles, name=EMPLOYEE_ROLE_NAME)
    if employee_role not in ctx.author.roles:
        await ctx.send("You don't have the employee role.")
        return
    
    # Check if workday started
    if not user_data["workday_started"]:
        await ctx.send("You haven't started your workday yet.")
        return
    
    # Check if on break
    if user_data["on_break"]:
        await ctx.send("Please end your break with !back before ending your workday.")
        return
    
    now = get_egypt_time()
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
    reset_user_daily_data(user_id)
    
    await ctx.send("Workday completed. Have a great day!")

@bot.event
async def on_message(message):
    # Skip bot messages
    if message.author.bot:
        return
    
    # Process commands first
    await bot.process_commands(message)
    
    # Then check for updates to log
    employee_role = discord.utils.get(message.guild.roles, name=EMPLOYEE_ROLE_NAME)
    if employee_role and employee_role in message.author.roles:
        user_data = get_user_data(message.author.id)
        
        # If workday has started and message isn't a command
        if user_data["workday_started"] and not message.content.startswith('!'):
            # Store the update
            user_data["daily_updates"].append(f"[{get_egypt_time().strftime('%H:%M')}] {message.content}")
            save_data()

if __name__ == "__main__":
    bot.run(os.environ.get('DISCORD_TOKEN'))
