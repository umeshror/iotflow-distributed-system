"""
IoT Device Simulation Script.
Generates realistic sensor data and publishes to MQTT for end-to-end testing.
"""

import asyncio
import json
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# Configuration
BROKER_HOST = "localhost"
BROKER_PORT = 11883  # Remapped host port
TOPIC_TEMPLATE = "devices/{device_id}/events"
NUM_DEVICES = 5
EVENTS_PER_DEVICE = 3

def generate_ulid() -> str:
    """Simple ULID-like string for testing."""
    chars = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(random.choices(chars, k=26))

async def simulate_device(device_id: str) -> None:
    client = mqtt.Client()
    client.connect(BROKER_HOST, BROKER_PORT)
    
    for i in range(EVENTS_PER_DEVICE):
        event_id = generate_ulid()
        payload = {
            "event_id": event_id,
            "device_id": device_id,
            "event_type": "telemetry",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "temperature": round(random.uniform(20.0, 30.0), 2),
                "humidity": round(random.uniform(40.0, 60.0), 2),
                "battery": random.randint(70, 100)
            },
            "metadata": {"firmware": "v2.1.0"},
            "schema_version": "1.0"
        }
        
        topic = TOPIC_TEMPLATE.format(device_id=device_id)
        client.publish(topic, json.dumps(payload), qos=1)
        print(f"[{device_id}] Published event {event_id}")
        await asyncio.sleep(1)
        
    client.disconnect()

async def main() -> None:
    print(f"Starting simulation for {NUM_DEVICES} devices...")
    tasks = [simulate_device(f"sensor-{i:03d}") for i in range(NUM_DEVICES)]
    await asyncio.gather(*tasks)
    print("Simulation complete.")

if __name__ == "__main__":
    asyncio.run(main())
