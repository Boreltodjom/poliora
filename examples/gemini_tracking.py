"""Track a Gemini API call made with Google's google-genai client."""

from google import genai

from poliora.cost import track_gemini_client

client = track_gemini_client(genai.Client(), project="gemini-pilot")
response = client.models.generate_content(
    model="gemini-3.5-flash",
    contents="Explain this repository in three concise bullets.",
)
print(response.text)
