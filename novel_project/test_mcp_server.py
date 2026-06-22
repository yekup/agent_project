#!/usr/bin/env python3
"""MCP Server 集成测试"""
import sys, json, asyncio

async def test():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, 'mcp_server.py',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=2**20,  # 1MB buffer for large responses
    )

    async def send_and_recv(msg):
        proc.stdin.write((json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8'))
        await proc.stdin.drain()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        return json.loads(line)

    async def send(msg):
        proc.stdin.write((json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8'))
        await proc.stdin.drain()

    # 1. initialize
    resp = await send_and_recv({'jsonrpc':'2.0','id':1,'method':'initialize','params':{
        'protocolVersion':'0.1.0','capabilities':{},'clientInfo':{'name':'test','version':'1'}
    }})
    assert 'result' in resp
    print('[PASS] initialize')
    await send({'jsonrpc':'2.0','method':'notifications/initialized'})

    # 2. tools/list
    resp = await send_and_recv({'jsonrpc':'2.0','id':2,'method':'tools/list'})
    tools = resp['result']['tools']
    assert len(tools) == 5
    print('[PASS] tools/list: 5 tools')

    # 3. search_novel_graph
    resp = await send_and_recv({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{
        'name':'search_novel_graph','arguments':{'character':'赵玖','novel':'shaosong'}
    }})
    text = resp['result']['content'][0]['text']
    assert len(text) > 200
    print(f'[PASS] search_novel_graph(赵玖): {len(text)} chars')

    # 4. get_character_timeline - may be large, read with bigger timeout
    resp = await send_and_recv({'jsonrpc':'2.0','id':4,'method':'tools/call','params':{
        'name':'get_character_timeline','arguments':{'character':'赵玖','novel':'shaosong'}
    }})
    text = resp['result']['content'][0]['text']
    assert len(text) > 200
    print(f'[PASS] get_character_timeline(赵玖): {len(text)} chars')

    # 5. analyze_chapter
    resp = await send_and_recv({'jsonrpc':'2.0','id':5,'method':'tools/call','params':{
        'name':'analyze_chapter','arguments':{'chapter':'1','novel':'shaosong'}
    }})
    text = resp['result']['content'][0]['text']
    assert len(text) > 50
    print(f'[PASS] analyze_chapter(1): {len(text)} chars')

    # 6. search_wiki
    resp = await send_and_recv({'jsonrpc':'2.0','id':6,'method':'tools/call','params':{
        'name':'search_wiki','arguments':{'query':'岳飞','novel':'shaosong'}
    }})
    text = resp['result']['content'][0]['text']
    assert len(text) > 100
    print(f'[PASS] search_wiki(岳飞): {len(text)} chars')

    # 7. list_novels
    resp = await send_and_recv({'jsonrpc':'2.0','id':7,'method':'tools/call','params':{
        'name':'list_novels','arguments':{}
    }})
    text = resp['result']['content'][0]['text']
    assert 'shaosong' in text
    print(f'[PASS] list_novels: {len(text)} chars')

    proc.terminate()
    await proc.wait()
    print('\n' + '='*50)
    print(' MCP Server: ALL TESTS PASSED')
    print('='*50)

asyncio.run(test())
