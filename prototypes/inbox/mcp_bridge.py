"""Explicit-target experimental MCP bridge; never registers or starts a daemon."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import mcp.server.stdio
from mcp import types
from mcp.server import Server
from orchestrator.config import Settings
from orchestrator.mcp_shim import _deliver_tool_result
from orchestrator.rpc_handlers import get_tool_definitions


def validate_target(url: str, data_dir: Path) -> str:
    from prototypes.inbox.server import validate_isolation
    parts = urlsplit(url)
    if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port:
        raise ValueError('Prototype requires an explicit loopback HTTP port')
    if parts.port == 18790 or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment:
        raise ValueError('Production port, credentials and URL suffixes are not prototype targets')
    validate_isolation(Settings(port=parts.port, data_dir=data_dir))
    return url.rstrip('/')


async def serve(url: str, data_dir: Path) -> None:
    from prototypes.inbox.server import INBOX_TOOL_DEFINITIONS
    url = validate_target(url, data_dir)
    cfg = Settings(port=urlsplit(url).port, data_dir=data_dir)
    async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(40, connect=5)) as http:
        health = (await http.get(url+'/api/health')).raise_for_status().json()
        if Path(health['data_dir']).resolve() != data_dir.resolve():
            raise RuntimeError('Test service identity does not match the explicit data directory')

        async def list_tools(ctx, params):
            del ctx, params
            return types.ListToolsResult(tools=[types.Tool(name=x['name'],description=x['description'],
                input_schema=x['inputSchema']) for x in get_tool_definitions()+INBOX_TOOL_DEFINITIONS])

        async def call_tool(ctx, params):
            del ctx
            try:
                response = await http.post(url+'/api/rpc',json={'tool':params.name,'arguments':dict(params.arguments or {})})
            except httpx.HTTPError as exc:
                failed = True
                payload = {'error':type(exc).__name__,'detail':str(exc),
                           'action':'Inspect persisted message/receipt before retrying an uncertain write.'}
            else:
                try:
                    body = response.json()
                except json.JSONDecodeError as exc:
                    # Preserve the actual HTTP fault instead of hiding it behind a parser error.
                    failed = True
                    payload = {'status_code':response.status_code,'response_text':response.text,
                               'error':type(exc).__name__,'detail':str(exc),
                               'action':'Inspect persisted message/receipt before retrying an uncertain write.'}
                else:
                    failed = response.status_code >= 400 or not body.get('ok',False)
                    payload = {'status_code':response.status_code,'response':body} if failed else body['result']
            return await asyncio.to_thread(_deliver_tool_result,cfg,params.name,payload,is_error=failed)

        server = Server('tri-inbox-prototype',version='0.0.1',on_list_tools=list_tools,on_call_tool=call_tool,
            instructions='Isolated experiment. A message receipt is not an independent acceptance verdict. Never replay uncertain actions.')
        async with mcp.server.stdio.stdio_server() as (read,write):
            await server.run(read,write,server.create_initialization_options())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--data-dir',type=Path,required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.url,args.data_dir))
