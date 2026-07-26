"""Small runtime checks used before exposing an app through Nginx."""
import asyncio

from fastapi import HTTPException


async def wait_for_listener(port: int, attempts: int = 10) -> None:
    for _ in range(attempts):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1,
            )
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            status_line = await asyncio.wait_for(reader.readline(), timeout=3)
            writer.close()
            await writer.wait_closed()
            if not status_line.startswith(b"HTTP/"):
                raise HTTPException(400, "The app listener did not return an HTTP response.")
            try:
                status = int(status_line.split()[1])
            except (IndexError, ValueError) as error:
                raise HTTPException(400, "The app listener returned an invalid HTTP response.") from error
            if status < 500:
                return
            raise HTTPException(400, f"The app responded with HTTP {status} during its local health check. Open Service logs and fix the application before deploying.")
        except OSError:
            await asyncio.sleep(1)
    raise HTTPException(400, "The app service did not listen on its assigned port. Open View logs and correct the start command.")
