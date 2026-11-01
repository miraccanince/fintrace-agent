"""Local MCP server exposing the investigation tools over stdio.

Tool contracts come from `tools.contracts()` and every call runs through
`tools.make_executor` on a read-only connection, so nothing is reimplemented.

    python -m fintrace.mcp_server --db banking.db
"""

import argparse
import json
import sys
from typing import Any, Callable

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from fintrace import banking_db, tools


def build_server(execute: Callable[[str, dict[str, Any]], dict[str, Any]]) -> Server:
    contracts = tools.contracts()

    async def list_tools(context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=name, description=contract["description"], input_schema=contract["parameters"])
            for name, contract in contracts.items()
        ])

    async def call_tool(context, params) -> types.CallToolResult:
        result = execute(params.name, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], is_error="error" in result)

    return Server("fintrace-tools", on_list_tools=list_tools, on_call_tool=call_tool)


async def _serve(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fintrace.mcp_server", description="Serve the read-only investigation tools over MCP (stdio).")
    parser.add_argument("--db", required=True, help="path to the banking SQLite database")
    args = parser.parse_args(argv)
    try:
        connection = banking_db.connect_readonly(args.db)
    except Exception as error:
        print(f"cannot open database: {error}", file=sys.stderr)  # stdout is the protocol channel
        return 2
    try:
        anyio.run(_serve, build_server(tools.make_executor(connection)))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
