# -*- coding: utf-8 -*-
# Nome do arquivo: firestore_manager.py (v4 - Sem mudanças funcionais, consistência)

# Importar as bibliotecas necessárias
from google.cloud import firestore
import datetime
import logging  # Adicionado para consistência de logging

# Configuração básica de logging (opcional, mas útil)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

logger.info("Tentando inicializar o cliente Firestore...")
# Inicializar o cliente Firestore
db = None
try:
    project_id = "gen-lang-client-0288576877"  # <<< SEU PROJECT ID AQUI
    db = firestore.Client(project=project_id)
    logger.info(
        f"Cliente Firestore inicializado com sucesso para o projeto: {project_id}."
    )
except Exception as e:
    logger.error(f"ERRO CRÍTICO ao inicializar cliente Firestore: {e}", exc_info=True)
    db = None


# --- Função para buscar ou criar dados do usuário ---
def get_or_create_user(telegram_user_id, user_name=None):
    """Busca dados do usuário no Firestore ou cria um novo documento se não existir."""
    if not db:
        logger.error("Erro: Cliente Firestore não está inicializado.")
        return None

    user_id_str = str(telegram_user_id)
    user_doc_ref = db.collection("users").document(user_id_str)
    logger.info(f"Buscando/Criando usuário: {user_id_str}")

    try:
        doc_snapshot = user_doc_ref.get()

        if doc_snapshot.exists:
            logger.info(f"Usuário {user_id_str} encontrado no Firestore.")
            user_data = doc_snapshot.to_dict()
            # Garante que estruturas aninhadas existam para usuários antigos ou com dados incompletos
            user_data.setdefault("profile", {})
            user_data.setdefault("diet_settings", {})
            user_data.setdefault("daily_tracking", {})
            user_data.setdefault("user_state", {"awaiting": None})

            # Garante campos padrão dentro das estruturas se ausentes
            user_data["profile"].setdefault("activity_level", "light")
            user_data["profile"].setdefault("goal", "maintain")
            user_data["diet_settings"].setdefault("daily_calorie_goal", None)
            user_data["diet_settings"].setdefault("diet_type", "standard")

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            today_str = now_utc.strftime("%Y-%m-%d")
            if user_data["daily_tracking"].get("date") != today_str:
                logger.info(
                    f"Resetando daily_tracking para novo dia ({today_str}) para usuário {user_id_str}."
                )
                user_data["daily_tracking"] = {
                    "date": today_str,
                    "calories_consumed": 0,
                    "log_today": [],
                }
                user_doc_ref.set(
                    {
                        "daily_tracking": user_data["daily_tracking"],
                        "last_interaction_at": firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )
            else:
                user_doc_ref.set(
                    {"last_interaction_at": firestore.SERVER_TIMESTAMP}, merge=True
                )

            return user_data
        else:
            logger.info(
                f"Usuário {user_id_str} não encontrado. Criando novo registro..."
            )
            now_creation = datetime.datetime.now(datetime.timezone.utc)
            today_str = now_creation.strftime("%Y-%m-%d")

            # --- ESTRUTURA DE DADOS ATUALIZADA PARA NOVO USUÁRIO ---
            new_user_data = {
                "telegram_user_id": telegram_user_id,
                "user_name": user_name if user_name else f"Usuário {user_id_str}",
                "created_at": firestore.SERVER_TIMESTAMP,
                "profile": {
                    "height_cm": None,
                    "initial_weight_kg": None,  # Pode ser útil no futuro
                    "current_weight_kg": None,
                    # "goal_weight_kg": None, # Removido por simplicidade, foco na meta calórica
                    "birth_year": None,
                    "gender": None,  # male / female
                    "activity_level": None,  # sedentary, light, moderate, active, extra_active
                    "goal": None,  # lose, maintain, gain
                },
                "diet_settings": {
                    "daily_calorie_goal": None,  # Começa como None
                    "diet_type": "standard",  # Pode ser expandido (low_carb, etc.)
                    # "intermittent_fasting": { # Removido por simplicidade inicial
                    #     "enabled": False,
                    #     "window_start_hour": None,
                    #     "window_end_hour": None,
                    # },
                },
                "daily_tracking": {
                    "date": today_str,
                    "calories_consumed": 0,
                    "log_today": [],  # Lista de dicionários {description, estimated_kcal, time}
                },
                "user_state": {
                    "awaiting": None  # Indica o que o bot está esperando (None = nada específico)
                },
                "last_interaction_at": firestore.SERVER_TIMESTAMP,
            }
            # --- FIM DA ESTRUTURA ATUALIZADA ---

            user_doc_ref.set(new_user_data)
            logger.info(f"Novo usuário {user_id_str} criado com estrutura padrão.")
            # Retorna os dados criados (sem o ID do documento explicitamente, pois já o temos)
            return new_user_data

    except Exception as e:
        logger.error(
            f"ERRO CRÍTICO ao acessar Firestore para usuário {user_id_str}: {e}",
            exc_info=True,
        )
        return None


# --- Função para atualizar calorias (com logging melhorado) ---
def update_daily_calories(telegram_user_id, calories_to_add, food_description=""):
    """Adiciona calorias ao total diário do usuário e lida com a troca de dia."""
    if not db:
        logger.error(
            "Erro: Cliente Firestore não está inicializado para update_daily_calories."
        )
        return False
    user_id_str = str(telegram_user_id)
    user_doc_ref = db.collection("users").document(user_id_str)
    logger.info(
        f"Iniciando transação para adicionar {calories_to_add} kcal para user {user_id_str}."
    )

    try:

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref, calories_add, description):
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                logger.warning(
                    f"Usuário {user_id_str} não encontrado durante transação."
                )
                return False  # Usuário não existe

            user_data = snapshot.to_dict()
            daily_tracking = user_data.get("daily_tracking", {})
            saved_date_str = daily_tracking.get("date")
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            today_str = now_utc.strftime("%Y-%m-%d")

            log_entry = {
                "description": description if description else "Registro sem descrição",
                "estimated_kcal": calories_add,
                "time": now_utc,  # Usar timestamp do servidor seria mais robusto se a latência for alta
            }
            current_log = daily_tracking.get("log_today", [])

            if saved_date_str == today_str:
                logger.info(f"Mesmo dia ({today_str}). Adicionando calorias.")
                new_calories = daily_tracking.get("calories_consumed", 0) + calories_add
                current_log.append(log_entry)
                transaction.update(
                    doc_ref,
                    {
                        "daily_tracking.calories_consumed": new_calories,
                        "daily_tracking.log_today": current_log,
                        "last_interaction_at": firestore.SERVER_TIMESTAMP,
                    },
                )
            else:  # Novo dia
                logger.info(
                    f"Novo dia detectado ({today_str}, anterior: {saved_date_str}). Resetando calorias e log."
                )
                new_calories = calories_add
                current_log = [log_entry]
                transaction.update(
                    doc_ref,
                    {
                        "daily_tracking.date": today_str,
                        "daily_tracking.calories_consumed": new_calories,
                        "daily_tracking.log_today": current_log,
                        "last_interaction_at": firestore.SERVER_TIMESTAMP,
                    },
                )
            logger.info(f"Transação preparada para user {user_id_str}.")
            return True  # Transação bem sucedida (a ser commitada)

        transaction = db.transaction()
        update_result = update_in_transaction(
            transaction, user_doc_ref, calories_to_add, food_description
        )

        if update_result:
            logger.info(
                f"Sucesso na transação de update para {user_id_str}. Calorias adicionadas: {calories_to_add}."
            )
        else:
            logger.warning(
                f"Falha na transação de update para {user_id_str} (usuário não encontrado ou outro erro)."
            )
        return update_result
    except Exception as e:
        logger.error(
            f"ERRO GERAL na transação de update para {user_id_str}: {e}", exc_info=True
        )
        return False


# --- FUNÇÕES DE CÁLCULO (com logging) ---
def calculate_age(birth_year):
    if not birth_year:
        logger.warning("[Cálculo Idade] Ano de nascimento ausente.")
        return None
    try:
        birth_year_int = int(birth_year)
        this_year = datetime.datetime.now(datetime.timezone.utc).year
        age = this_year - birth_year_int
        if 0 < age < 120:
            return age
        else:
            logger.warning(
                f"[Cálculo Idade] Idade calculada fora do range esperado: {age} (ano: {birth_year})"
            )
            return None
    except (ValueError, TypeError):
        logger.warning(f"[Cálculo Idade] Ano inválido ou tipo incorreto: {birth_year}")
        return None


def calculate_bmr_mifflin(weight_kg, height_cm, age, gender):
    required_data = {
        "weight_kg": weight_kg,
        "height_cm": height_cm,
        "age": age,
        "gender": gender,
    }
    missing = [k for k, v in required_data.items() if v is None]
    if missing:
        logger.warning(f"[Cálculo BMR] Dados ausentes: {missing}")
        return None
    try:
        weight_kg_f = float(weight_kg)
        height_cm_f = float(height_cm)
        age_int = int(age)
        gender_processed = str(gender).lower().strip()

        if gender_processed == "male":
            bmr = (10 * weight_kg_f) + (6.25 * height_cm_f) - (5 * age_int) + 5
        elif gender_processed == "female":
            bmr = (10 * weight_kg_f) + (6.25 * height_cm_f) - (5 * age_int) - 161
        else:
            logger.warning(f"[Cálculo BMR] Gênero inválido fornecido: {gender}")
            return None
        result = round(bmr)
        logger.info(
            f"[Cálculo BMR] Sucesso: Peso={weight_kg_f}, Altura={height_cm_f}, Idade={age_int}, Gênero={gender_processed} -> BMR={result}"
        )
        return result
    except (ValueError, TypeError) as e:
        logger.error(
            f"[Cálculo BMR] Erro de tipo ou valor nos dados: {required_data} - Erro: {e}"
        )
        return None
    except Exception as e:
        logger.error(f"[Cálculo BMR] Erro inesperado: {e}", exc_info=True)
        return None


def calculate_tdee(bmr, activity_level):
    if bmr is None or activity_level is None:
        logger.warning(
            f"[Cálculo TDEE] BMR ({bmr}) ou Nível de Atividade ({activity_level}) ausente."
        )
        return None
    multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "active": 1.725,
        "extra_active": 1.9,
    }
    activity_level_processed = str(activity_level).lower().strip()
    multiplier = multipliers.get(activity_level_processed)

    if not multiplier:
        logger.warning(
            f"[Cálculo TDEE] Nível de atividade inválido: '{activity_level}'. Válidos: {list(multipliers.keys())}"
        )
        return None
    try:
        bmr_f = float(bmr)
        tdee = round(bmr_f * multiplier)
        logger.info(
            f"[Cálculo TDEE] Sucesso: BMR={bmr_f}, Nível={activity_level_processed}, Multiplicador={multiplier} -> TDEE={tdee}"
        )
        return tdee
    except (ValueError, TypeError) as e:
        logger.error(
            f"[Cálculo TDEE] Erro de tipo ou valor (BMR inválido?): {bmr} - Erro: {e}"
        )
        return None
    except Exception as e:
        logger.error(f"[Cálculo TDEE] Erro inesperado: {e}", exc_info=True)
        return None


def suggest_calorie_goal(tdee, goal):
    if tdee is None or goal is None:
        logger.warning(f"[Sugestão Meta] TDEE ({tdee}) ou Objetivo ({goal}) ausente.")
        return None
    try:
        tdee_f = float(tdee)
        goal_processed = str(goal).lower().strip()
        suggested_goal = tdee_f  # Default para manter

        if goal_processed == "lose":
            # Déficit entre 15-25% do TDEE, com mínimo de 300 e máximo de 750, sem baixar de 1200 kcal
            deficit = min(750, max(300, round(tdee_f * 0.20)))
            suggested_goal = max(1200, tdee_f - deficit)
            logger.info(
                f"[Sugestão Meta] Objetivo 'perder'. TDEE={tdee_f}, Déficit={deficit}"
            )
        elif goal_processed == "gain":
            # Superávit entre 10-20% do TDEE, com mínimo de 250 e máximo de 500
            surplus = min(500, max(250, round(tdee_f * 0.15)))
            suggested_goal = tdee_f + surplus
            logger.info(
                f"[Sugestão Meta] Objetivo 'ganhar'. TDEE={tdee_f}, Superávit={surplus}"
            )
        elif goal_processed == "maintain":
            logger.info(f"[Sugestão Meta] Objetivo 'manter'. TDEE={tdee_f}")
        else:
            logger.warning(
                f"[Sugestão Meta] Objetivo inválido: '{goal}'. Válidos: lose, maintain, gain"
            )
            return None

        # Arredonda para o múltiplo de 50 mais próximo
        final_goal = round(suggested_goal / 50) * 50
        logger.info(
            f"[Sugestão Meta] Meta sugerida: {final_goal} kcal (arredondado de {suggested_goal:.2f})"
        )
        return final_goal
    except (ValueError, TypeError) as e:
        logger.error(
            f"[Sugestão Meta] Erro de tipo ou valor (TDEE inválido?): {tdee} - Erro: {e}"
        )
        return None
    except Exception as e:
        logger.error(f"[Sugestão Meta] Erro inesperado: {e}", exc_info=True)
        return None


# --- Bloco de Teste ---
if __name__ == "__main__":
    if db:
        logger.info("\n--- INICIANDO TESTE DE FIRESTORE_MANAGER (v4) ---")
        test_user_id = 999999997  # ID para testar criação/leitura
        test_user_name = "Usuário Teste Firestore v4"

        # Forçar recriação para testar estrutura
        logger.info(f"\n[TESTE] Resetando/Criando usuário {test_user_id}...")
        db.collection("users").document(
            str(test_user_id)
        ).delete()  # Garante que não existe
        user_data = get_or_create_user(test_user_id, test_user_name)
        if user_data:
            logger.info(f"[TESTE] Dados recuperados/criados: {user_data}")
        else:
            logger.error(f"[TESTE] Falha ao obter/criar dados.")
            exit()

        # Testar cálculos (simulando dados do usuário)
        logger.info("\n[TESTE] Testando cálculos...")
        profile_test = {
            "birth_year": 1990,
            "gender": "male",
            "height_cm": 180,
            "current_weight_kg": 80,
            "activity_level": "moderate",
            "goal": "lose",
        }
        age = calculate_age(profile_test["birth_year"])
        logger.info(f"Idade calculada: {age}")
        bmr = calculate_bmr_mifflin(
            profile_test["current_weight_kg"],
            profile_test["height_cm"],
            age,
            profile_test["gender"],
        )
        logger.info(f"BMR calculado: {bmr}")
        tdee = calculate_tdee(bmr, profile_test["activity_level"])
        logger.info(f"TDEE calculado: {tdee}")
        goal_kcal = suggest_calorie_goal(tdee, profile_test["goal"])
        logger.info(f"Meta de calorias sugerida: {goal_kcal}")

        # Testar update de calorias
        logger.info("\n[TESTE] Adicionando calorias...")
        success1 = update_daily_calories(test_user_id, 500, "Almoço Teste 1")
        logger.info(f"Update 1 sucesso: {success1}")
        success2 = update_daily_calories(test_user_id, 250, "Lanche Teste 1")
        logger.info(f"Update 2 sucesso: {success2}")

        # Ler dados finais
        final_data = get_or_create_user(test_user_id)
        if final_data:
            logger.info(
                f"[TESTE] Dados finais do usuário: {final_data.get('daily_tracking')}"
            )
        else:
            logger.error("[TESTE] Falha ao ler dados finais.")

        logger.info("\n--- TESTE DE FIRESTORE_MANAGER CONCLUÍDO ---")
    else:
        logger.critical(
            "\nCliente Firestore não inicializado. Testes não podem ser executados."
        )
