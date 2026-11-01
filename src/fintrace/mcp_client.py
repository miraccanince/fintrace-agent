"""Synchronous access to the local MCP tool server, for the synchronous harness.

The MCP SDK client is async only. anyio's blocking portal (already a dependency
of the SDK) lets one session stay open across the harness's blocking calls.
"""

import json
import sys
from contextlib import contextmanager
from functools import partial
from typing import Any, Callable, Iterator

from anyio.from_thread import start_blocking_portal
from mcp import Client, StdioServerParameters

CALL_TIMEOUT_SECONDS = 30


@contextmanager
def connect(db_path: str) -> Iterator[tuple[dict[str, dict[str, Any]], Callable[[str, dict[str, Any]], dict[str, Any]]]]:
    """Start the server, discover its tools, and yield `(advertised, execute)` for
    `harness.run_investigation`. `advertised` is what the server reports; the harness
    still decides what the model may see and call."""
    server = StdioServerParameters(command=sys.executable, args=["-m", "fintrace.mcp_server", "--db", str(db_path)])
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(server)) as client:
            discovered = portal.call(client.list_tools).tools
            advertised = {
                tool.name: {"name": tool.name, "description": tool.description or "", "parameters": tool.input_schema}
                for tool in discovered
            }

            def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = portal.call(partial(client.call_tool, name, arguments, read_timeout_seconds=CALL_TIMEOUT_SECONDS))
                return json.loads(result.content[0].text)

            yield advertised, execute
