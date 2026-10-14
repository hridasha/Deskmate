"""The `fetch` tool: read a web page as text. Runs in-process through the SSRF-guarded HTTP client (see safe_url)."""
import re

from bs4 import BeautifulSoup
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

import safe_url

MAX_BYTES = 2_000_000
UA = "Mozilla/5.0 (compatible; Deskmates/1.0)"


class FetchArgs(BaseModel):
    url: str = Field(description="http or https address of the page")
    max_length: int = Field(5000, ge=200, le=20000, description="most characters to return")
    start_index: int = Field(0, ge=0, description="start from this character (to read the next part of a long page)")


def to_text(body: str, content_type: str) -> str:
    if "html" not in content_type and not body.lstrip().startswith("<"):
        return body
    soup = BeautifulSoup(body, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "iframe"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", soup.get_text("\n")))
    return (f"# {title}\n\n" if title else "") + text.strip()


async def fetch(url: str, max_length: int = 5000, start_index: int = 0) -> str:
    try:
        safe_url.check_url(url)
        async with safe_url.guarded_client(headers={"User-Agent": UA}) as client:
            async with client.stream("GET", url.strip()) as r:
                if r.status_code >= 400:
                    return f"Couldn't fetch the page (HTTP {r.status_code})."
                raw = b""
                async for part in r.aiter_bytes():
                    raw += part
                    if len(raw) > MAX_BYTES:
                        break
                ctype = r.headers.get("content-type", "").lower()
                if ctype and not any(k in ctype for k in ("text", "html", "json", "xml")):
                    return f"Not a text page ({ctype.split(';')[0]})."
                body = raw.decode(r.encoding or "utf-8", errors="replace")
    except safe_url.BlockedHost as e:
        return f"Blocked: {e}"
    except Exception as e:  # noqa: BLE001  redirects to a blocked host surface here too
        msg = str(e)
        return f"Blocked: {msg}" if "internal address" in msg else f"Couldn't fetch the page ({type(e).__name__})."
    text = to_text(body, ctype)
    part = text[start_index : start_index + max_length]
    if not part:
        return "No more content."
    more = start_index + max_length
    return part + (f"\n\n[Truncated. Call again with start_index={more} to continue.]" if len(text) > more else "")


def make_tool() -> StructuredTool:
    return StructuredTool.from_function(coroutine=fetch, name="fetch", description="Fetch a web page and return its text. Use for current information from a URL the user gives you.", args_schema=FetchArgs)
