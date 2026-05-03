import google.generativeai as genai

api_key = "AIzaSyCJBWM9AVxfLzS4-DJGWi--gW62AZJlUf0"
genai.configure(api_key=api_key)

print("Available models:")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)
