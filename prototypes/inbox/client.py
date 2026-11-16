"""Real SDK stdio client used by native-agent terminal tools in the isolated test."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from prototypes.inbox.mcp_bridge import validate_target

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def session(url: str, data_dir: Path):
    url = validate_target(url,data_dir)
    env = dict(os.environ)
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'plugin/src')]))
    params = StdioServerParameters(command=sys.executable,args=['-B','-m','prototypes.inbox.mcp_bridge',
        '--url',url,'--data-dir',str(data_dir.resolve())],cwd=str(ROOT),env=env)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=45) as sdk:
            initialized = await sdk.initialize()
            if initialized.server_info.name != 'tri-inbox-prototype':
                raise RuntimeError('Unexpected MCP bridge identity')
            yield sdk


async def call(sdk, tool: str, arguments: dict, receipt: Path | None = None):
    started = time.time()
    result = await sdk.call_tool(tool,arguments)
    record = {'tool':tool,'arguments':arguments,'started_at':started,'elapsed_seconds':time.time()-started,
              'result':result.model_dump(mode='json',by_alias=True),'transport':'actual stdio MCP via native terminal tool'}
    if receipt is not None:
        receipt.parent.mkdir(parents=True,exist_ok=True)
        with receipt.open('x',encoding='utf-8') as out:
            json.dump(record,out,ensure_ascii=False,indent=2)
    if result.is_error:
        raise RuntimeError(json.dumps(record['result'],ensure_ascii=False))
    text = [item.text for item in result.content if item.type == 'text']
    if len(text) != 1:
        raise RuntimeError('Expected exactly one full JSON tool result')
    payload = json.loads(text[0])
    if isinstance(payload,dict) and payload.get('delivery') == 'file':
        import hashlib
        raw = Path(payload['result_file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != payload['sha256']:
            raise RuntimeError('MCP result file hash mismatch')
        payload = json.loads(raw)
    return payload


async def main(args):
    arguments = json.loads(args.arguments.read_text(encoding='utf-8-sig'))
    async with session(args.url,args.data_dir) as sdk:
        result = await call(sdk,args.tool,arguments,args.receipt)
        print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--tool',required=True)
    parser.add_argument('--arguments',required=True,type=Path)
    parser.add_argument('--receipt',type=Path)
    asyncio.run(main(parser.parse_args()))
