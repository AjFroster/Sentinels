from google.genai import Client


client = Client(api_key="DUMMY_KEY_FOR_NOW")

response = client.models.generate_content(
    model="gemini-1.5-flash",
    contents="Hello from ADK!"
)

print("ADK response:")
print(response.text)
