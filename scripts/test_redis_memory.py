import time
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from memory.redis_memory import RedisChatMessageHistory
from langchain_core.messages import HumanMessage, AIMessage

def test_redis_session():
    phone = "5511999998888"
    history = RedisChatMessageHistory(session_id=phone, ttl=2)  # Short TTL for testing
    
    print("🧹 Cleaning old session...")
    history.clear()
    
    print("📝 Adding messages...")
    history.add_message(HumanMessage(content="Hello"))
    history.add_message(AIMessage(content="Hi there"))
    
    # Check immediate retrieval
    msgs = history.messages
    print(f"✅ Retrieved {len(msgs)} messages.")
    if len(msgs) != 2:
        print("❌ ERROR: Expected 2 messages.")
        sys.exit(1)
        
    print("⏳ Waiting for TTL (3s)...")
    time.sleep(3)
    
    # Check expiration
    msgs_after = history.messages
    print(f"✅ Retrieved {len(msgs_after)} messages after TTL.")
    if len(msgs_after) != 0:
        print("❌ ERROR: Session should be empty!")
        sys.exit(1)
        
    print("🎉 Redis Session Memory Test Passed!")

if __name__ == "__main__":
    test_redis_session()
