import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from translation_engine import TranslationEngine

print("Instantiating TranslationEngine...")
te = TranslationEngine()

print("Testing translate_to_simulation...")
res = te.translate_to_simulation("I stabbed him in the chest", {})
print(f"Result: {res}")

print("Testing translate_to_user...")
res = te.translate_to_user("He applied a kinetic vector disruption to his core chassis", {})
print(f"Result: {res}")

print("Testing translate_history_to_simulation...")
messages = [{"role": "user", "content": "I stabbed him"}]
res = te.translate_history_to_simulation(messages, {})
print(f"Result: {res}")
