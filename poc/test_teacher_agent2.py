import asyncio
import json
import websockets

async def test_teacher_agent():
    """Test teacher agent with different query to verify tool detection."""
    uri = "ws://localhost:8000/teacher/ws/1/chat"
    async with websockets.connect(uri) as websocket:
        print("✓ Connected to teacher agent\n")

        # Test 2: Ask about class analytics
        test_queries = [
            ("Show me class analytics", "analytics detection"),
            ("What content should I cover on the history of Rome?", "search lesson content"),
            ("What do I know about my class?", "class memory recall"),
        ]

        for query, description in test_queries:
            print(f"🔍 Test: {description}")
            print(f'📝 Query: "{query}"\n')
            
            await websocket.send(query)
            
            response_parts = []
            while True:
                try:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=15.0)
                    data = json.loads(msg)
                    if data.get("type") == "token":
                        token = data.get("content", "")
                        response_parts.append(token)
                        print(token, end="", flush=True)
                    elif data.get("type") == "done":
                        break
                except asyncio.TimeoutError:
                    break
            
            print(f"\n✓ Response received ({len(''.join(response_parts))} chars)\n")
            print("=" * 70 + "\n")

asyncio.run(test_teacher_agent())
