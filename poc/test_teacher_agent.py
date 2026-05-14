#!/usr/bin/env python3
"""
Test script to verify teacher agent can access persisted lesson summaries.
"""
import asyncio
import json
import websockets
from typing import AsyncGenerator

async def test_teacher_agent():
    """Connect to teacher agent WebSocket and ask about lessons."""
    
    # Teacher ID 1 is the default teacher created in the system
    teacher_id = 1
    ws_url = f"ws://localhost:8000/teacher/ws/{teacher_id}/chat"
    
    print(f"Connecting to teacher agent at {ws_url}...")
    
    async with websockets.connect(ws_url) as websocket:
        print("✓ Connected to teacher agent WebSocket")
        
        # Test 1: More direct question
        test_questions = [
            "Tell me about the uploaded lessons",
            "Show me all lesson summaries",
            "What materials have I uploaded?",
        ]
        
        for i, user_message in enumerate(test_questions):
            print(f"\n{'='*60}")
            print(f"Test {i+1}: Asking '{user_message}'")
            print('='*60)
            
            payload = {
                "message": user_message,
                # No conversation_id yet - server will create one
            }
            
            print(f"📝 Sending message...")
            await websocket.send(json.dumps(payload))
            
            # Collect response
            response_text = []
            conversation_id = None
            
            print("\n🤖 Teacher Agent Response:")
            print("-" * 60)
            
            async for message in websocket:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    print(f"Failed to parse: {message}")
                    continue
                
                # Check for conversation creation
                if data.get("type") == "conversation_created":
                    conversation_id = data.get("conversation_id")
                    print(f"[System] Conversation created: {conversation_id}")
                    continue
                
                # Collect tokens
                token = data.get("token", "")
                done = data.get("done", False)
                
                if token:
                    response_text.append(token)
                    print(token, end="", flush=True)
                
                if done:
                    print("\n" + "-" * 60)
                    break
            
            full_response = "".join(response_text)
            
            # Check if the agent actually called the get_lesson_summaries tool
            print(f"\n✓ Response length: {len(full_response)} characters")
            
            # Look for indicators that the tool was used
            if any(keyword in full_response.lower() for keyword in ["additional materials", "catholic", "church", "schism", "chapter", "key concepts", "learning objectives"]):
                print(f"✅ SUCCESS (Test {i+1}): Agent referenced specific lesson content!")
                return True
            elif "lesson" in full_response.lower() or "material" in full_response.lower():
                print(f"⚠️  (Test {i+1}): Agent mentioned lessons but may not have provided specific content")
            else:
                print(f"❌ (Test {i+1}): Agent did not reference lesson content")
                print(f"   Response preview: {full_response[:150]}...")

if __name__ == "__main__":
    result = asyncio.run(test_teacher_agent())
    exit(0 if result else 1)
