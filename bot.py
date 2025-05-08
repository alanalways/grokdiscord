import discord
from discord.ext import commands
import os
import requests
import json
import aiosqlite
import asyncio
from dotenv import load_dotenv
import base64
from io import BytesIO
from PIL import Image

# 加載環境變數
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")

# 設置 Discord 機器人
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 資料庫初始化
async def init_db():
    async with aiosqlite.connect("conversations.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                user_id INTEGER,
                channel_id INTEGER,
                message TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# 儲存對話
async def store_conversation(user_id, channel_id, message):
    async with aiosqlite.connect("conversations.db") as db:
        await db.execute(
            "INSERT INTO conversations (user_id, channel_id, message) VALUES (?, ?, ?)",
            (user_id, channel_id, message)
        )
        await db.commit()

# 獲取對話歷史
async def get_conversation(user_id, limit=10):
    async with aiosqlite.connect("conversations.db") as db:
        async with db.execute(
            "SELECT message FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return [row[0] async for row in cursor]

# 調用 Grok API
async def call_grok_api(prompt, image=None, mode="text"):
    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "grok-beta",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048
    }
    
    if image and mode == "image_analysis":
        # 圖片分析
        image_data = base64.b64encode(image.read()).decode("utf-8")
        data["messages"][0]["content"] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": f"data:image/jpeg;base64,{image_data}"}
        ]
    elif mode == "image_generation":
        # 圖片生成
        data["model"] = "flux.1"  # 假設支持 Flux.1
        data["messages"][0]["content"] = f"Generate an image: {prompt}"

    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        return response.json()
    else:
        return {"error": f"API request failed: {response.text}"}

# 機器人啟動事件
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await init_db()

# 創建私人頻道
async def create_private_channel(guild, user):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    for role in guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    
    channel = await guild.create_text_channel(
        f"private-{user.name}",
        overwrites=overwrites
    )
    return channel

# 處理訊息
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    if bot.user in message.mentions:
        # 創建私人頻道
        channel = await create_private_channel(message.guild, message.author)
        await message.channel.send(f"已為你創建私人頻道：{channel.mention}")
        
        # 儲存對話
        await store_conversation(message.author.id, channel.id, message.content)
        
        # 獲取對話歷史
        history = await get_conversation(message.author.id)
        context = "\n".join(history[::-1])  # 倒序組成上下文
        
        # 處理圖片
        if message.attachments:
            attachment = message.attachments[0]
            if attachment.content_type.startswith("image/"):
                image = BytesIO()
                await attachment.save(image)
                prompt = f"Analyze this image and respond to: {message.content}"
                response = await call_grok_api(prompt, image, mode="image_analysis")
            else:
                response = await call_grok_api(f"Non-image attachment received: {message.content}")
        else:
            # 檢查是否要求圖片生成
            if "generate image" in message.content.lower():
                prompt = message.content.replace("generate image", "").strip()
                response = await call_grok_api(prompt, mode="image_generation")
            else:
                # 普通對話或聯網搜尋
                prompt = f"Context: {context}\nUser: {message.content}"
                response = await call_grok_api(prompt)
        
        # 處理回應
        if "error" in response:
            await channel.send(f"錯誤：{response['error']}")
        else:
            content = response["choices"][0]["message"]["content"]
            if response.get("image"):  # 假設 API 返回圖片
                image_data = base64.b64decode(response["image"])
                image = Image.open(BytesIO(image_data))
                image.save("output.png")
                await channel.send(file=discord.File("output.png"))
            else:
                await channel.send(content)
    
    await bot.process_commands(message)

# 啟動機器人
bot.run(DISCORD_TOKEN)
