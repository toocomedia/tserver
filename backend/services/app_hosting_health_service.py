"""Small runtime checks used before exposing an app through Nginx."""
import asyncio

from fastapi import HTTPException


async def wait_for_listener(port: int, attempts: int = 10) -> None:
    for _ in range(attempts):
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1,
            )
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(1)
    raise HTTPException(400, "The app service did not listen on its assigned port. Open View logs and correct the start command.")
