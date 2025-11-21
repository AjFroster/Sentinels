from ollama import Client

# Connect to the local Ollama server
client = Client(host="http://localhost:11434")

# Send a simple message to model
response = client.chat(
    model="llama3.2:1b",
    messages=[
        {
         "role":"user",
         "content": "Hello from python"
         }
    ]
)
print("test")
print(f"Assistant: {response['message']['content']}")