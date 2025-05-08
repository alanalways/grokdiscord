import discord
from discord.ext import commands
import os
import requests
import sqlite3
from datetime import datetime
from bs4 import BeautifulSoup
from flask import Flask
import logging

# 設置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 初始化記憶體 SQLite 資料庫
def init_db():
    conn = sqlite3.connect(':memory:')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS conversations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id TEXT NOT NULL,
                  message TEXT NOT NULL,
                  role TEXT NOT NULL,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

# 儲存對話到資料庫
def save_message(user_id, message, role):
    conn = sqlite3.connect(':memory:')
    c = conn.cursor()
    c.execute("INSERT INTO conversations (user_id, message, role) VALUES (?, ?, ?)", (user_id, message, role))
    conn.commit()
    conn.close()

# 取得對話歷史
def get_conversation_history(user_id, limit=10):
    conn = sqlite3.connect(':memory:')
    c = conn.cursor()
    c.execute("SELECT role, message FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (user_id, limit))
    history = c.fetchall()
    conn.close()
    history.reverse()
    return [{"role": row[0], "content": row[1]} for row in history]

# 初始化資料庫
init_db()

# 聯網搜尋功能
def web_search(query):
    try:
        search_url = f"https://www.google.com/search?q={query}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        results = soup.find_all('div', class_='BNeawe s3v9rd AP7Wnd')
        if results:
            return results[0].get_text()[:500]
        return "無法從網路上找到相關資訊。"
    except Exception as e:
        return f"搜尋失敗：{str(e)}"

# 調用 Grok API 的通用函數
def call_grok_api(messages, model, image_url=None):
    headers = {"Authorization": f"Bearer {os.environ['GROK_API_KEY']}", "Content-Type": "application/json"}
    data = {"model": model, "messages": messages, "max_tokens": 1000}
    if image_url:
        data["messages"].append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_url}}]})
    try:
        response = requests.post("https://api.x.ai/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except requests.RequestException as e:
        return f"錯誤：無法連接到 xAI API - {str(e)}"

# 生成圖片的函數
def generate_image(prompt):
    headers = {"Authorization": f"Bearer {os.environ['GROK_API_KEY']}", "Content-Type": "application/json"}
    data = {"model": "grok-2-image-1212", "prompt": prompt}
    try:
        response = requests.post("https://api.x.ai/v1/image/generations", headers=headers, json=data)
        response.raise_for_status()
        return response.json()["data"][0]["url"]
    except requests.RequestException as e:
        return f"錯誤：無法生成圖片 - {str(e)}"

# 檢查是否為圖片生成請求
def is_image_generation_request(message):
    keywords = ["生成圖片", "畫", "圖片", "繪製", "create image", "draw"]
    return any(keyword in message.lower() for keyword in keywords)

app = Flask(__name__)

# 虛擬 Web 端點，滿足 Render 端口要求
@app.route('/')
def health_check():
    return "Bot is running", 200

# 自動創建私人頻道（觸發於標記 Bot）
@bot.event
async def on_message(message):
    logger.info(f"Received message: {message.content} from {message.author}")
    if message.author == bot.user:
        return

    user_id = str(message.author.id)
    guild = message.guild

    # 測試 ping 命令
    if message.content.lower() == "!ping":
        await message.channel.send("Pong!")
        return

    # 觸發私人頻道創建
    if bot.user.mentioned_in(message) and not any(channel.name.startswith(f"private-{message.author.name}") for channel in guild.channels):
        logger.info(f"Creating private channel for {message.author}")
        category = discord.utils.get(guild.categories, name="Private Channels")
        if not category:
            category = await guild.create_category("Private Channels")

        channel = await guild.create_text_channel(f"private-{message.author.name}-{message.author.discriminator}", category=category)
        try:
            await channel.set_permissions(guild.default_role, read_messages=False)
            await channel.set_permissions(message.author, read_messages=True, send_messages=True)
            await channel.set_permissions(bot.user, read_messages=True, send_messages=True)

            admin_role = discord.utils.get(guild.roles, name="Admin")
            if admin_role:
                await channel.set_permissions(admin_role, read_messages=True, send_messages=True)

            await message.channel.send(f"為您創建了私人頻道：{channel.mention}！")
        except discord.errors.Forbidden:
            await message.channel.send("我缺少管理頻道的權限！請確保我有 'Manage Channels' 權限，並且我的角色層級高於 @everyone。")
        return

    # 在私人頻道內一問一答
    if isinstance(message.channel, discord.TextChannel) and message.channel.name.startswith(f"private-{message.author.name}"):
        logger.info(f"Processing message in private channel for {message.author}")
        # 檢查是否為刪除頻道指令
        if message.content.lower() == "!delete":
            await message.channel.send("正在刪除此頻道...")
            await message.channel.delete()
            return

        # 處理圖片訊息
        if message.attachments:
            image_url = message.attachments[0].url
            save_message(user_id, "用戶傳送了一張圖片", "user")

            conversation_history = get_conversation_history(user_id)
            conversation_history.append({"role": "user", "content": "請描述這張圖片的內容"})
            reply = call_grok_api(conversation_history, model="grok-2-vision-1212", image_url=image_url)

            save_message(user_id, reply, "assistant")
            await message.channel.send(reply)
            return

        # 處理文字訊息
        save_message(user_id, message.content, "user")

        conversation_history = get_conversation_history(user_id)
        conversation_history.append({"role": "user", "content": message.content})

        if is_image_generation_request(message.content):
            image_url = generate_image(message.content)
            if "錯誤" in image_url:
                await message.channel.send(image_url)
            else:
                save_message(user_id, "生成了一張圖片", "assistant")
                await message.channel.send(file=discord.File(requests.get(image_url, stream=True).raw))
            return
        else:
            reply = call_grok_api(conversation_history, model="grok-3-beta")
            if len(reply) < 50 or "錯誤" in reply:
                reply = web_search(message.content)
            save_message(user_id, reply, "assistant")
            await message.channel.send(reply)

@bot.event
async def on_ready():
    logger.info(f'{bot.user} 已上線！')

# 啟動 Flask 和 Bot
if __name__ == "__main__":
    import threading
    bot_thread = threading.Thread(target=bot.run, args=(os.environ['DISCORD_TOKEN'],), daemon=True)
    bot_thread.start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
