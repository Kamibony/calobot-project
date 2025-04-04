# -*- coding: utf-8 -*-
# Nome do arquivo: test_gemini.py (v2 - Persona Atualizada)

import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    Part,
    GenerationConfig,
    SafetySetting,
    HarmCategory,
)
import logging

# Configuração básica de logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Configurações ---
PROJECT_ID = "gen-lang-client-0288576877"  # <<< SEU PROJECT ID AQUI
LOCATION = "us-central1"
MODEL_NAME = "gemini-1.0-pro"

# --- Definição da Nova Persona Base ---
BASE_PERSONA_PROMPT_TESTE = """
Aja como o CaloBot: um coach nutricional digital parceiro e motivador.
Use uma linguagem clara, positiva e encorajadora.
Seu objetivo é ajudar o usuário com informações sobre calorias, dieta e hábitos saudáveis de forma prática e compreensível.
Use emojis para tornar a conversa amigável (ex: 😊, 👍, 💪, 🍎, 🥗, 🏃‍♀️), mas evite sarcasmo ou excesso de informalidade.
Responda sempre em português do Brasil (pt-br).
"""

logger.info(
    f"Inicializando Vertex AI para Projeto: {PROJECT_ID}, Localização: {LOCATION}"
)
try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    logger.info("Vertex AI inicializado com sucesso.")

    # Carrega o modelo generativo
    model = GenerativeModel(MODEL_NAME)
    logger.info(f"Modelo {MODEL_NAME} carregado.")

    # Configurações de geração (pode espelhar as de calobot_core)
    generation_config = GenerationConfig(temperature=0.7, top_p=0.95)
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    }

    # --- Definição do Prompt de Teste ---

    # Contexto simulado (opcional, mas útil)
    user_name_teste = "Maria"
    contexto_teste = "Meta: 1800 kcal. Consumido hoje: 1200 kcal. Restam: 600 kcal."

    # Pergunta/Input do usuário simulado
    input_usuario_teste = "CaloBot, comi um pão de queijo e um café com leite agora de tarde. Quantas calorias tem mais ou menos?"

    # Monta o prompt final
    prompt_final = (
        f"{BASE_PERSONA_PROMPT_TESTE}\n\n"
        f"Contexto do Usuário '{user_name_teste}': {contexto_teste}\n\n"
        f"Tarefa: Usuário '{user_name_teste}' registrou: '{input_usuario_teste}'.\n"
        "1. Estime as calorias consumidas de forma realista e inclua a frase 'Estimativa CaloBot: XXX kcal.' na sua resposta.\n"
        "2. Comente o registro de forma motivadora ou neutra.\n"
        "3. Mencione o status atualizado das calorias do dia, considerando a estimativa feita."
        f"\n\nCaloBot:"
    )

    logger.info("\n--- Enviando prompt para o Gemini ---")
    # logger.debug(f"Prompt completo:\n{prompt_final}") # Descomente para ver o prompt exato
    logger.info(f"Prompt (início): {prompt_final[:200]}...")  # Mostra só o início

    # Envia o prompt para o modelo gerar conteúdo
    response = model.generate_content(
        prompt_final,
        generation_config=generation_config,
        safety_settings=safety_settings,
    )

    logger.info("\n--- Resposta do Gemini (como CaloBot) ---")
    if response.candidates:
        if response.candidates[0].content and response.candidates[0].content.parts:
            print(response.candidates[0].content.parts[0].text)
        else:
            finish_reason = response.candidates[0].finish_reason
            safety_ratings = response.candidates[0].safety_ratings
            print(
                f"AVISO: Resposta vazia ou bloqueada. Razão: {finish_reason}, Safety: {safety_ratings}"
            )
    else:
        print("ERRO: Resposta do Gemini não contém 'candidates'.")
        # print(f"Resposta completa: {response}") # Descomente para depurar

except Exception as e:
    logger.error(f"ERRO ao interagir com Vertex AI / Gemini: {e}", exc_info=True)

logger.info("\n--- Teste Gemini concluído ---")
