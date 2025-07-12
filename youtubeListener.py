import feedparser
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
# Load secrets and data for youtube bot setup
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")
DISCORD_YOUTUBE_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID_FOR_YT"))
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"

LAST_VIDEO_FILE = "last_video.txt"  # File to save last video ID

def load_last_video_id():
    try:
        with open(LAST_VIDEO_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

def save_last_video_id(video_id):
    with open(LAST_VIDEO_FILE, "w") as f:
        f.write(video_id)

latest_video_id = load_last_video_id()  # Load on startup

async def start_youtube_watcher(client):
    global latest_video_id
    await client.wait_until_ready()
    channel = client.get_channel(DISCORD_YOUTUBE_CHANNEL_ID)

    while not client.is_closed():
        feed = feedparser.parse(RSS_URL)
        if feed.entries:
            newest = feed.entries[0]
            video_id = newest.yt_videoid
            video_url = f"https://www.youtube.com/watch?v={video_id}"

            if video_id != latest_video_id:
                latest_video_id = video_id
                save_last_video_id(video_id)  # Save on new video found
                await channel.send(f"New video posted: {video_url}")

        await asyncio.sleep(300)
