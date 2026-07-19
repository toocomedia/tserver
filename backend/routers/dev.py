from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import asyncio
from datetime import datetime

router = APIRouter(prefix="/dev", tags=["dev"])

@router.get("/cache-test", response_class=HTMLResponse)
async def cache_test():
    """
    A deliberately slow route to test Nginx caching.
    Takes 2 seconds to respond. If cached, subsequent requests will be instant.
    """
    await asyncio.sleep(2.0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Cache Test</title>
        <style>
            body {{ font-family: system-ui, sans-serif; background: #0B0C0B; color: #E6E6DF; text-align: center; padding-top: 100px; }}
            .box {{ border: 1px solid #2a2a2a; padding: 40px; display: inline-block; border-radius: 8px; background: #111; }}
            h1 {{ color: #C7F464; margin-bottom: 20px; }}
            .time {{ font-size: 24px; font-weight: bold; background: #222; padding: 10px; border-radius: 4px; display: inline-block; margin-bottom: 20px; }}
            p {{ color: #aaa; max-width: 400px; line-height: 1.5; margin: 0 auto; }}
        </style>
    </head>
    <body>
        <div class="box">
            <h1>Cache Test Page</h1>
            <div class="time">{now}</div>
            <p>This page took exactly 2 seconds to generate on the backend.</p>
            <br>
            <p>If you put this behind a reverse proxy with caching enabled, the first load will take 2 seconds. If you refresh, it should load <strong>instantly</strong> and the time above will <strong>not change</strong> until the cache TTL expires.</p>
        </div>
    </body>
    </html>
    """
    return html
