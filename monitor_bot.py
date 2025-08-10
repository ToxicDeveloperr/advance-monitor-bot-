# monitor_bot.py
import os
import re
import asyncio
import aiohttp
import logging
import hashlib
import pathlib
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse, parse_qs

from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient

import config

# ---------- logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("monitor-bot")

# ensure tmp dir (use config.TMP_DIR; default should be ./tmp in config)
pathlib.Path(config.TMP_DIR).mkdir(parents=True, exist_ok=True)

# ---------- Mongo ----------
mongo = AsyncIOMotorClient(config.MONGO_URI)
db = mongo[config.DB_NAME]
links_col = db["links"]       # cached processed links: { _id: link_hash, ... }
tasks_col = db["tasks"]       # per-post tasks: { _id: post_id, links: [ {url,...} ], ... }

# ---------- regex / helpers ----------
URL_RE = re.compile(r"(https?://[^\s\)\]\>]+)")
TERA_DOMAINS = ("terabox.app", "terafileshare.com", "terabox", "terabox.com")

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()

def now_ts() -> float:
    return time.time()

def is_terabox_like(url: str) -> bool:
    u = url.lower()
    return any(d in u for d in ("terabox", "terafileshare", "dm-d.terabox.app", "dm-data.terabox.app", "proxy?"))

# ---------- concurrency ----------
post_queue = asyncio.Queue()
group_queue = asyncio.Queue()
global_semaphore = asyncio.Semaphore(config.MAX_GLOBAL_CONCURRENT_DOWNLOADS)
group_locks = {}  # group_id -> asyncio.Semaphore

# ---------- Resolve link ----------
async def resolve_link(session: aiohttp.ClientSession, url: str) -> dict:
    """
    Resolve share link to a proxy/direct url via resolver API.
    Accepts already-direct/proxy urls too.
    """
    # if already proxy/direct
    if "proxy?" in url or "dm-d.terabox.app" in url or "raspy-wave" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        fname = qs.get("file_name", [None])[0]
        return {"ok": True, "direct": url, "file_name": fname or parsed.path.split("/")[-1], "size_bytes": None}

    base = config.LINK_RESOLVER_API.rstrip("/")
    fetch_url = f"{base}/fetch?url={quote_plus(url)}"
    try:
        async with session.get(fetch_url, timeout=30) as resp:
            text = await resp.text()
            try:
                data = await resp.json()
            except Exception:
                logger.warning("Resolver returned non-json: %s", text[:200])
                return {"ok": False, "error": "Resolver returned non-json response"}
            proxy = data.get("proxy_url") or data.get("download_link") or data.get("direct_link") or data.get("url")
            fname = data.get("file_name") or data.get("filename") or None
            size_bytes = data.get("size_bytes") or data.get("size") or data.get("file_size")
            if proxy:
                return {"ok": True, "direct": proxy, "file_name": fname, "size_bytes": size_bytes, "meta": data}
            else:
                return {"ok": False, "error": "Resolver did not return proxy/direct link", "raw": data}
    except Exception as e:
        logger.exception("resolve_link error")
        return {"ok": False, "error": str(e)}

# ---------- download stream to disk ----------
async def download_file(session: aiohttp.ClientSession, url: str, dest_path: str):
    total = 0
    temp_path = dest_path + ".part"
    async with session.get(url, timeout=None) as resp:
        resp.raise_for_status()
        with open(temp_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(config.DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
    os.replace(temp_path, dest_path)
    return total

# ---------- upload to store channel ----------
async def upload_to_store(client: Client, file_path: str, caption: str = ""):
    """
    Upload file to STORE_CHANNEL_ID and return upload metadata.
    """
    msg = await client.send_document(chat_id=config.STORE_CHANNEL_ID, document=file_path, caption=caption)
    file_ref = None
    if getattr(msg, "document", None):
        file_ref = msg.document.file_id
    elif getattr(msg, "video", None):
        file_ref = msg.video.file_id
    return {"chat_id": msg.chat.id, "message_id": msg.message_id, "file_id": file_ref}

# ---------- processing single link ----------
async def process_single_link(client: Client, session: aiohttp.ClientSession, post_id: str, link_entry: dict):
    """
    Process one link: resolve -> download -> upload -> record
    Returns updated link_entry
    """
    link = link_entry["url"]
    link_hash = sha1(link)

    # reuse cached done
    cached = await links_col.find_one({"_id": link_hash})
    if cached and cached.get("status") == "done":
        link_entry.update({"status": "done", "result": cached, "attempts": link_entry.get("attempts", 0)})
        return link_entry

    added = link_entry.get("added_at", now_ts())
    if now_ts() - added > 3600:
        link_entry.update({"status": "timeout", "last_error": "Timeout before processing (1h)"})
        return link_entry

    max_attempts = 2
    attempts = link_entry.get("attempts", 0)

    while attempts < max_attempts:
        attempts += 1
        link_entry["attempts"] = attempts

        resolved = await resolve_link(session, link)
        if not resolved.get("ok"):
            link_entry["last_error"] = f"resolve_failed: {resolved.get('error')}"
            logger.warning("Resolve failed for %s (attempt %d): %s", link, attempts, resolved.get("error"))
            await asyncio.sleep(2)
            if now_ts() - added > 3600:
                link_entry.update({"status":"timeout"})
                return link_entry
            continue

        direct = resolved["direct"]
        file_name = resolved.get("file_name") or f"{link_hash}.bin"
        safe_name = f"{link_hash}_{file_name}"
        dest_path = os.path.join(config.TMP_DIR, safe_name)

        # download
        try:
            async with global_semaphore:
                if now_ts() - added > 3600:
                    link_entry.update({"status":"timeout", "last_error":"Timeout before download start"})
                    return link_entry
                size = await download_file(session, direct, dest_path)
        except Exception as e:
            logger.exception("Download failed for %s (attempt %d)", link, attempts)
            link_entry["last_error"] = f"download_failed: {str(e)}"
            try:
                if os.path.exists(dest_path): os.remove(dest_path)
            except Exception:
                pass
            if now_ts() - added > 3600:
                link_entry.update({"status":"timeout"})
                return link_entry
            await asyncio.sleep(2)
            continue

        # upload
        try:
            upload_meta = await upload_to_store(client, dest_path, caption=file_name)
            try:
                os.remove(dest_path)
            except Exception:
                pass

            doc = {
                "_id": link_hash,
                "link": link,
                "direct_link": direct,
                "filename": file_name,
                "size": size,
                "uploaded": upload_meta,
                "status": "done",
                "ts": datetime.now(timezone.utc),
            }
            # insert cache
            await links_col.replace_one({"_id": link_hash}, doc, upsert=True)
            link_entry.update({"status":"done", "result": doc, "last_error": None})
            return link_entry
        except Exception as e:
            logger.exception("Upload failed for %s (attempt %d)", link, attempts)
            link_entry["last_error"] = f"upload_failed: {str(e)}"
            try:
                if os.path.exists(dest_path): os.remove(dest_path)
            except Exception:
                pass
            if now_ts() - added > 3600:
                link_entry.update({"status":"timeout"})
                return link_entry
            await asyncio.sleep(2)
            continue

    link_entry.update({"status":"failed", "last_error": link_entry.get("last_error")})
    return link_entry

# ---------- worker for posts ----------
async def worker_post_processor(client: Client):
    session_timeout = aiohttp.ClientTimeout(total=None)
    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        while True:
            task = await post_queue.get()
            try:
                post_id = task.get("post_id")
                logger.info("Post worker picked %s", post_id)
                task_doc = await tasks_col.find_one({"_id": post_id})
                if not task_doc:
                    logger.warning("Task %s not found", post_id)
                    post_queue.task_done()
                    continue

                links_list = task_doc.get("links", [])
                message_obj = task.get("message_obj")

                for idx, le in enumerate(links_list):
                    if le.get("status") in ("done", "failed", "timeout"):
                        continue
                    updated = await process_single_link(client=client, session=session, post_id=post_id, link_entry=le)
                    links_list[idx] = updated
                    await tasks_col.update_one({"_id": post_id}, {"$set": {"links": links_list}})

                # Prepare final summary
                lines = []
                for le in links_list:
                    u = le["url"]
                    st = le.get("status")
                    if st == "done":
                        res = le.get("result", {})
                        fm = res.get("uploaded", {})
                        fid = fm.get("file_id")
                        fname = res.get("filename")
                        size = res.get("size")
                        lines.append(f"Link: {u}\nFile ID: `{fid}`\nName: {fname} Size: {size}")
                    else:
                        lines.append(f"Link: {u}\nStatus: {st}\nError: {le.get('last_error')}")

                final_text = "\n\n".join(lines)

                # Try edit original message caption; else send to OUTPUT channel
                try:
                    if message_obj and getattr(message_obj, "chat", None) and getattr(message_obj, "message_id", None):
                        try:
                            base_caption = message_obj.caption or message_obj.text or ""
                            new_caption = (base_caption + "\n\n" + final_text)[:1024]
                            await client.edit_message_caption(chat_id=message_obj.chat.id, message_id=message_obj.message_id, caption=new_caption)
                        except Exception:
                            await client.send_message(chat_id=config.OUTPUT_CHANNEL_ID or message_obj.chat.id, text=f"Results for post {post_id}:\n\n{final_text}")
                    else:
                        await client.send_message(chat_id=config.OUTPUT_CHANNEL_ID or task.get("chat_id"), text=f"Results for post {post_id}:\n\n{final_text}")
                except Exception:
                    logger.exception("Failed to publish final result")
                    await client.send_message(chat_id=config.OUTPUT_CHANNEL_ID or task.get("chat_id"), text=f"Results for post {post_id} (fallback):\n\n{final_text}")

                # delete task doc
                await tasks_col.delete_one({"_id": post_id})

            except Exception:
                logger.exception("Error in worker_post_processor")
            finally:
                post_queue.task_done()

# ---------- worker for group /leech ----------
async def worker_group_processor(client: Client):
    session_timeout = aiohttp.ClientTimeout(total=None)
    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        while True:
            req = await group_queue.get()
            try:
                group_id = req.get("group_id")
                url = req.get("url")
                reply_to = req.get("reply_to_message_id")
                from_user = req.get("from_user")
                logger.info("Group worker: leech request from %s in %s -> %s", from_user, group_id, url)
                if group_id not in group_locks:
                    group_locks[group_id] = asyncio.Semaphore(config.MAX_CONCURRENT_PER_GROUP)
                async with group_locks[group_id]:
                    # store a pending record in links_col with status pending
                    link_hash = sha1(url)
                    pending_doc = {
                        "_id": link_hash,
                        "link": url,
                        "status": "pending",
                        "requested_from_group": group_id,
                        "requested_by": from_user,
                        "requested_reply_msg_id": reply_to,
                        "ts": datetime.now(timezone.utc),
                    }
                    await links_col.replace_one({"_id": link_hash}, pending_doc, upsert=True)

                    updated = await process_single_link(client=client, session=session, post_id=f"group_{group_id}_{int(now_ts())}", link_entry={"url": url, "status":"pending", "attempts":0, "added_at": now_ts()})
                    if updated.get("status") == "done":
                        fm = updated["result"]["uploaded"]
                        fid = fm.get("file_id")
                        fname = updated["result"].get("filename")
                        await client.send_message(chat_id=group_id, text=f"Download finished.\nFile ID: `{fid}`\nName: {fname}", reply_to_message_id=reply_to)
                    else:
                        await client.send_message(chat_id=group_id, text=f"Download failed: {updated.get('last_error')}", reply_to_message_id=reply_to)

            except Exception:
                logger.exception("Error in worker_group_processor")
            finally:
                group_queue.task_done()

# ---------- Pyrogram client ----------
# Use memory-session bot mode so no session file required (authorizes via bot_token)
app = Client(
    name=":memory:",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

# ---------- handlers ----------

# Source-channel handler: only process configured SOURCE_CHANNELS
@app.on_message(filters.chat(config.SOURCE_CHANNELS) & filters.channel)
async def on_channel_post(client: Client, message: Message):
    """
    When new message arrives in SOURCE_CHANNELS, extract terabox links and create DB task + enqueue.
    """
    try:
        text = message.caption or message.text or ""
        raw_urls = URL_RE.findall(text)
        urls = [u for u in raw_urls if is_terabox_like(u)]
        if not urls:
            logger.debug("No terabox-like links in message %s/%s", message.chat.id, message.message_id)
            return

        post_id = f"{message.chat.id}_{message.message_id}"
        now = now_ts()
        link_entries = []
        for u in urls:
            link_entries.append({"url": u, "status": "pending", "attempts": 0, "added_at": now, "last_error": None})

        task_doc = {
            "_id": post_id,
            "chat_id": message.chat.id,
            "message_id": message.message_id,
            "links": link_entries,
            "ts": datetime.now(timezone.utc)
        }
        await tasks_col.replace_one({"_id": post_id}, task_doc, upsert=True)
        await post_queue.put({"post_id": post_id, "chat_id": message.chat.id, "links": urls, "message_obj": message})
        logger.info("Queued post %s with %d links", post_id, len(urls))
    except Exception:
        logger.exception("on_channel_post error")

# Group command: /leech <url> (users can also request)
@app.on_message(filters.command("leech") & filters.group & filters.text)
async def on_leech_command(client: Client, message: Message):
    try:
        if len(message.command) < 2:
            await message.reply_text("Usage: /leech <url>")
            return
        url = message.text.split(None, 1)[1].strip()
        if not url.startswith("http"):
            await message.reply_text("Please provide a valid URL.")
            return
        await group_queue.put({"group_id": message.chat.id, "url": url, "reply_to_message_id": message.message_id, "from_user": message.from_user.id})
        await message.reply_text("Queued. Will process soon.")
    except Exception:
        logger.exception("on_leech_command error")

# When a file (document/video) arrives in group as reply to a message that contains the original link,
# we try to match the replied-to message for URLs and update links_col + tasks accordingly.
@app.on_message(filters.group & (filters.document | filters.video))
async def on_group_file(client: Client, message: Message):
    try:
        reply = message.reply_to_message
        if not reply:
            return
        text = reply.text or reply.caption or ""
        urls = URL_RE.findall(text)
        if not urls:
            return

        # For each url in the replied-to message, save the uploaded file_id and update tasks
        file_id = None
        if getattr(message, "document", None):
            file_id = message.document.file_id
        elif getattr(message, "video", None):
            file_id = message.video.file_id

        for u in urls:
            link_hash = sha1(u)
            uploaded_meta = {
                "_id": link_hash,
                "link": u,
                "uploaded": {"chat_id": message.chat.id, "message_id": message.message_id, "file_id": file_id},
                "status": "done",
                "ts": datetime.now(timezone.utc)
            }
            await links_col.replace_one({"_id": link_hash}, uploaded_meta, upsert=True)

            # Update any tasks that reference this link (scan tasks and replace)
            cursor = tasks_col.find({"links.url": u})
            async for task_doc in cursor:
                changed = False
                links = task_doc.get("links", [])
                for idx, le in enumerate(links):
                    if le.get("url") == u and le.get("status") != "done":
                        links[idx]["status"] = "done"
                        links[idx]["result"] = uploaded_meta
                        links[idx]["last_error"] = None
                        changed = True
                if changed:
                    await tasks_col.update_one({"_id": task_doc["_id"]}, {"$set": {"links": links}})
                    logger.info("Updated task %s with uploaded file for link %s", task_doc["_id"], u)

    except Exception:
        logger.exception("on_group_file error")

# No-op group listener placeholder
@app.on_message(filters.group & ~filters.command("leech"))
async def on_group_message(client: Client, message: Message):
    return

# ---------- health server ----------
async def start_http_server(host="0.0.0.0", port: int = None):
    app_http = web.Application()
    async def health(request):
        return web.Response(text="OK")
    async def info(request):
        return web.json_response({
            "status": "running",
            "time": datetime.now(timezone.utc).isoformat()
        })
    app_http.router.add_get("/", info)
    app_http.router.add_get("/health", health)

    runner = web.AppRunner(app_http)
    await runner.setup()
    site_port = port or int(os.getenv("PORT", "8000"))
    site = web.TCPSite(runner, host, site_port)
    await site.start()
    logger.info("HTTP server started on %s:%s", host, site_port)
    return runner

# ---------- main ----------
async def main():
    await app.start()
    logger.info("Bot started.")

    # start health server
    port_env = os.getenv("PORT")
    port = int(port_env) if port_env else 8000
    http_runner = await start_http_server(port=port)

    # start workers
    workers = []
    n_post_workers = max(2, config.MAX_GLOBAL_CONCURRENT_DOWNLOADS)
    for _ in range(n_post_workers):
        workers.append(asyncio.create_task(worker_post_processor(app)))
    for _ in range(2):
        workers.append(asyncio.create_task(worker_group_processor(app)))

    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        logger.info("Shutting down workers")
    finally:
        try:
            await app.stop()
        except Exception:
            pass
        try:
            await http_runner.cleanup()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
