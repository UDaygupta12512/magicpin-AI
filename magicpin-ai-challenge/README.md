# Vera — magicpin Merchant AI Assistant (v2.0)

![Vera Hero](https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&q=80&w=1200)

**Vera** is a sophisticated AI-powered merchant assistant designed for magicpin. It automates high-quality, contextually-grounded communication between merchants and their customers via WhatsApp, leveraging the power of Claude 3.5/4.5 Sonnet to drive business growth and customer retention.

## 🚀 Features

- **Context-Grounded Composition**: Extracts facts from category, merchant, and customer data to ensure message accuracy and eliminate hallucinations.
- **Dynamic Intent Routing**: Intelligent handling of merchant replies (Join, Accept, Stop, Greet, Off-topic).
- **Category-Specific Voices**: Adaptive communication styles for Dentists, Salons, Restaurants, Gyms, and Pharmacies.
- **Anti-Hallucination Guardrails**: Strictly adheres to provided facts, avoiding invented numbers or competitor mentions.
- **Smart Trigger System**: Responds to real-time events (Digest shares, performance drops, new offers).
- **Auto-Reply Protection**: Built-in detection for automated messages to prevent infinite loops.

## 🛠️ Technology Stack

- **Backend**: Python 3.10+ / FastAPI
- **LLM**: Anthropic Claude (Claude 3.5 Sonnet / 4.5 Sonnet)
- **Frontend**: Vanilla HTML5, CSS3 (Glassmorphism), JavaScript
- **Validation**: Pydantic for robust API request/response modeling

## 📦 Getting Started

### Prerequisites

- Python 3.10+
- Anthropic API Key

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/UDaygupta12512/magicpin-AI.git
   cd magicpin-AI
   ```

2. Install dependencies:
   ```bash
   pip install fastapi uvicorn anthropic pydantic
   ```

3. Set your API Key:
   ```bash
   export ANTHROPIC_API_KEY='your-api-key-here'
   ```

4. Run the application:
   ```bash
   python bot.py
   # OR
   uvicorn bot:app --reload
   ```

5. Access the dashboard:
   Open `http://localhost:8000` in your browser.

## 📡 API Endpoints

- `GET /v1/metadata`: Project information and team details.
- `POST /v1/context`: Push merchant/category/trigger context data.
- `POST /v1/tick`: Process pending triggers and generate actions.
- `POST /v1/reply`: Handle merchant replies to AI messages.
- `GET /v1/healthz`: System status and uptime.

## 🎨 Design System

Vera features a "Premium Dark" design system with:
- **Glassmorphic UI**: Translucent panels with backdrop filters.
- **Fluid Layout**: Responsive three-column dashboard.
- **Micro-animations**: Smooth transitions and pulsing indicators for real-time feedback.

---
Developed by **Uday** for the Vera Challenge.
