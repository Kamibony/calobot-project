# -*- coding: utf-8 -*-
# Nome do arquivo: telegram_bot.py (v3 - Onboarding no /start refatorado, logs melhorados)

import logging
import asyncio
import calobot_core  # Importa nossa lógica principal (v20 ou superior)
import firestore_manager  # Importa para acesso direto a verificação de perfil
from telegram import Update, constants  # Importa constants para ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PicklePersistence,  # Opcional: para persistir dados do bot entre reinícios
)
import os  # Para token de ambiente (recomendado)

# --- CONFIGURAÇÃO ---
# É ALTAMENTE RECOMENDADO usar variáveis de ambiente para o token
TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", "7822556645:AAFolOnxLs6MsIRa-RZYBCCnPg30tkXXD84"
)  # <<< COLOQUE SEU TOKEN AQUI ou defina variável de ambiente
if TELEGRAM_TOKEN == "7822556645:AAFolOnxLs6MsIRa-RZYBCCnPg30tkXXD84":
    print(
        "AVISO: Usando token hardcoded. Considere usar variáveis de ambiente (TELEGRAM_BOT_TOKEN)."
    )

# Configuração básica de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# Silencia logs excessivos de libs de HTTP
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- Funções Handler ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler para o comando /start."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id
    user_name = user.first_name  # Usar primeiro nome é mais amigável

    logger.info(
        f"Comando /start recebido de user {user.mention_html()} ({user_id}) no chat {chat_id}."
    )

    # 1. Saudar o usuário
    await update.message.reply_html(
        rf"Olá {user.mention_html()}! Eu sou o CaloBot, seu parceiro digital para uma vida mais saudável! 💪",
        # reply_markup=ForceReply(selective=True), # Opcional: Forçar resposta
    )
    await context.bot.send_chat_action(
        chat_id=chat_id, action=constants.ChatAction.TYPING
    )

    # 2. Buscar/Criar usuário e verificar necessidade de Onboarding
    try:
        # Usamos asyncio.to_thread para chamar a função síncrona do firestore
        user_data = await asyncio.to_thread(
            firestore_manager.get_or_create_user, user_id, user_name
        )

        if not user_data:
            logger.error(f"Falha ao obter/criar dados para user {user_id} no /start.")
            await update.message.reply_text(
                "Tive um problema para acessar seus dados. 😟 Poderia tentar o comando /start novamente?"
            )
            return

        logger.info(f"Dados do usuário {user_id} carregados/criados.")

        # 3. Verificar se onboarding (perfil ou meta) está incompleto
        profile_incomplete, _ = calobot_core.is_profile_incomplete(user_data)
        goal_not_set = (
            user_data.get("diet_settings", {}).get("daily_calorie_goal") is None
        )

        if profile_incomplete or goal_not_set:
            if profile_incomplete:
                logger.info(f"Onboarding do perfil necessário para user {user_id}.")
                await update.message.reply_text(
                    "Para começar, preciso de algumas informações sobre você. Vamos lá?"
                )
            elif goal_not_set:
                logger.info(f"Onboarding da meta necessário para user {user_id}.")
                await update.message.reply_text(
                    "Seu perfil está completo! 🎉 Agora vamos definir sua meta diária de calorias."
                )
            else:  # Caso estranho, só por segurança
                logger.warning(
                    f"Condição de onboarding inconsistente para user {user_id}"
                )
                await update.message.reply_text("Verificando seu perfil...")

            await context.bot.send_chat_action(
                chat_id=chat_id, action=constants.ChatAction.TYPING
            )

            # Chama process_message com um sinal interno para obter a PRIMEIRA pergunta
            logger.info(
                f"Chamando process_message com '__INTERNAL_ONBOARDING_CHECK__' para user {user_id}."
            )
            resposta_onboarding = await asyncio.to_thread(
                calobot_core.process_message,
                user_id,
                user_name,
                "__INTERNAL_ONBOARDING_CHECK__",
            )

            if resposta_onboarding:
                logger.info(f"Enviando pergunta de onboarding para user {user_id}.")
                await update.message.reply_text(resposta_onboarding)
            else:
                # Isso pode acontecer se o check interno não gerar prompt (ex: erro ao setar awaiting)
                logger.warning(
                    f"Check interno de onboarding não retornou prompt para user {user_id}."
                )
                # Mensagem genérica aqui? Ou logar e seguir?
                # await update.message.reply_text("Estou pronto para começar quando você estiver!")
        else:
            # Onboarding completo
            logger.info(f"Onboarding já completo para user {user_id}.")
            await update.message.reply_text(
                "Seu perfil já está configurado! 😊 Me diga o que comeu, peça uma sugestão ou vamos conversar!"
            )

    except Exception as e:
        logger.error(
            f"Erro durante o processamento do /start para user {user_id}: {e}",
            exc_info=True,
        )
        await update.message.reply_text(
            "Opa, tive um probleminha ao verificar seu perfil agora. Tente novamente ou me mande uma mensagem!"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler para mensagens de texto normais."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_text = update.message.text
    user_id = user.id
    user_name = user.first_name

    # Ignora mensagens muito curtas ou vazias (pode acontecer)
    if not message_text or len(message_text.strip()) < 1:
        logger.info(f"Mensagem vazia recebida de {user_id}. Ignorando.")
        return

    logger.info(
        f"Mensagem '{message_text}' recebida de {user.name} ({user_id}). Processando com calobot_core..."
    )

    # Feedback visual para o usuário
    await context.bot.send_chat_action(
        chat_id=chat_id, action=constants.ChatAction.TYPING
    )

    try:
        # Chama a função síncrona principal em uma thread separada
        resposta_calobot = await asyncio.to_thread(
            calobot_core.process_message, user.id, user_name, message_text
        )

        # Verifica se process_message retornou None (indicando que não há resposta a enviar)
        if resposta_calobot is None:
            logger.info(
                f"process_message retornou None para user {user_id}. Nenhuma resposta enviada."
            )
            return  # Não envia nada

    except Exception as e:
        logger.error(
            f"Erro GERAL ao chamar calobot_core.process_message para user {user_id}: {e}",
            exc_info=True,
        )
        resposta_calobot = "Xiii, deu um bug aqui no meu processamento! 🤯 Tente de novo daqui a pouco, por favor?"

    # Envia a resposta do CaloBot de volta ao usuário
    if resposta_calobot:  # Garante que não é None ou vazia
        await update.message.reply_text(resposta_calobot)
        logger.info(f"Resposta enviada para {user.name} ({user_id})")
    else:
        logger.warning(
            f"Resposta final do CaloBot foi vazia ou None para user {user_id}. Nenhuma mensagem enviada."
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Loga os erros causados por Updates."""
    logger.error("Exceção ao lidar com uma atualização:", exc_info=context.error)

    # Opcional: Tentar notificar o usuário sobre o erro
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Desculpe, ocorreu um erro inesperado ao processar sua solicitação. 😕",
            )
        except Exception as e_notify:
            logger.error(f"Falha ao notificar usuário sobre erro: {e_notify}")


# --- Função Principal ---
def main() -> None:
    """Inicia o bot e o mantém rodando."""
    if (
        not TELEGRAM_TOKEN
        or TELEGRAM_TOKEN == "COLOQUE_SEU_TOKEN_AQUI_OBTIDO_DO_BOTFATHER"
    ):
        logger.critical("ERRO FATAL: Token do Telegram não configurado!")
        logger.critical(
            "Defina a variável de ambiente 'TELEGRAM_BOT_TOKEN' ou edite o arquivo telegram_bot.py."
        )
        return

    # Verifica dependências críticas antes de iniciar
    if not calobot_core.db:
        logger.critical(
            "ERRO FATAL: Conexão com Firestore não estabelecida em calobot_core. Bot não pode iniciar."
        )
        return
    if not calobot_core.model:
        logger.critical(
            "ERRO FATAL: Modelo Gemini não carregado em calobot_core. Bot não pode iniciar."
        )
        return

    logger.info("Verificações de dependência OK.")

    # Opcional: Persistência para dados do bot (ex: user_data, chat_data)
    # persistence = PicklePersistence(filepath="calobot_persistence.pkl")
    # application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    logger.info("Criando Application do bot Telegram...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    logger.info("Application criada.")

    # Registra os handlers
    application.add_handler(CommandHandler("start", start))
    # Handler principal para mensagens de texto que NÃO são comandos
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Registra o handler de erro
    application.add_error_handler(error_handler)
    logger.info("Handlers registrados (start, message, error).")

    # Inicia o Bot usando Polling
    logger.info("Iniciando o bot com polling...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(
            f"Erro fatal ao iniciar ou rodar o polling do bot: {e}", exc_info=True
        )

    logger.info("Bot encerrado.")


# --- Execução ---
if __name__ == "__main__":
    main()
