import os
import streamlit as st
from openai import OpenAI
from groq import Groq

class AIEngine:
    def __init__(self):
        self.openai_key = st.secrets.get("OPENAI_API_KEY", "").strip()
        self.grok_key = st.secrets.get("GROK_API_KEY", "").strip()

        self.active_model = None

        # Initialize clients conditionally
        self.openai_client = OpenAI(api_key=self.openai_key) if self.openai_key else None
        self.grok_client = Groq(api_key=self.grok_key) if self.grok_key else None

    def generate(self, prompt: str):
        """
        Returns best AI response using fallback priority:
        1. OpenAI GPT
        2. Grok (xAI)
        """

        # ---- Try OPENAI First ----
        if self.openai_client:
            try:
                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}]
                )
                self.active_model = "OpenAI (GPT-4o-mini)"
                return response.choices[0].message.content
            
            except Exception as e:
                if "quota" not in str(e).lower():
                    raise e  # Real error → stop
                # else fall through to Grok

        # ---- Fallback → GROK ----
        if self.grok_client:
            try:
                response = self.grok_client.chat.completions.create(
                    model="grok-2",
                    messages=[{"role": "user", "content": prompt}]
                )
                self.active_model = "Grok-2 (xAI)"
                return response.choices[0].message.content
            except Exception as e:
                return f"⚠️ AI error: {e}"

        return "❌ No valid AI model available. Please configure at least one API key."
