# -*- coding: utf-8 -*-
# Nome do arquivo: calobot_core.py (v30 - Simplificação try/except goal_confirmation)

import firestore_manager
import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig, SafetySetting, HarmCategory
import datetime
import re
import json # Para processar JSON da NLU
from google.cloud import firestore
import logging

# Configuração básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configurações e Inicializações Globais ---
PROJECT_ID = "gen-lang-client-0288576877"
LOCATION = "us-central1"
MODEL_NAME = "gemini-1.0-pro"

# Inicializa Firestore
db = firestore_manager.db
if not db: logger.critical("ERRO CRÍTICO: Cliente Firestore não inicializado.");
else: logger.info("Cliente Firestore carregado com sucesso.")

# Inicializa Vertex AI
model = None; generation_config = None; safety_settings = None
try:
    logger.info(f"Inicializando Vertex AI: Projeto={PROJECT_ID}, Local={LOCATION}")
    vertexai.init(project=PROJECT_ID, location=LOCATION); logger.info("Vertex AI inicializado.")
    model = GenerativeModel(MODEL_NAME); logger.info(f"Modelo {MODEL_NAME} carregado.")
    generation_config = GenerationConfig(temperature=0.7, top_p=0.95); logger.info(f"Config Geração definida (temp=0.7).")
    safety_settings = { HarmCategory.HARM_CATEGORY_HARASSMENT: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE, HarmCategory.HARM_CATEGORY_HATE_SPEECH: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE, HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE, HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE, }; logger.info(f"Config Segurança aplicadas.")
except Exception as e: logger.error(f"ERRO CRÍTICO inicializar Vertex AI: {e}", exc_info=True); model=None; generation_config=None; safety_settings=None

# --- Definição da Persona Base ---
BASE_PERSONA_PROMPT = """
Aja como o CaloBot: um coach nutricional digital parceiro e motivador. Use uma linguagem clara, positiva e encorajadora. Seu objetivo é ajudar o usuário com informações sobre calorias, dieta e hábitos saudáveis de forma prática e compreensível. Use emojis para tornar a conversa amigável (ex: 😊, 👍, 💪, 🍎, 🥗, 🏃‍♀️), mas evite sarcasmo ou excesso de informalidade. Responda sempre em português do Brasil (pt-br).
"""
# --- Definições para NLU ---
POSSIBLE_INTENTS = [ "LOG_FOOD", "ASK_SUGGESTION", "GET_STATUS", "GET_PROFILE", "UPDATE_PROFILE", "PROVIDE_INFO", "GREETING", "FAREWELL", "AFFIRMATION", "NEGATION", "HELP", "CHITCHAT", "OUT_OF_SCOPE", "UNCLEAR" ]
POSSIBLE_ENTITIES = [ "food_items", "quantity", "meal_time", "profile_field", "profile_value", "info_value", "dietary_constraint", "preference" ]

# --- Função NLU com Gemini ---
def get_nlu_understanding(user_message):
    """Usa Gemini para NLU. Retorna dict ou None."""
    logger.info(f"[NLU] Análise: '{user_message}'")
    if not model: logger.error("[NLU] Abortado: Modelo off."); return None
    nlu_prompt = f"""
Analise a mensagem do usuário e retorne um JSON VÁLIDO contendo a intenção principal ("intent") e as entidades relevantes ("entities").

Intenções Possíveis: {POSSIBLE_INTENTS}
Entidades Possíveis: {POSSIBLE_ENTITIES} (retorne apenas as encontradas: food_items[], quantity, meal_time, profile_field, profile_value, info_value, dietary_constraint[], preference).
Intents especiais: UNCLEAR, CHITCHAT, OUT_OF_SCOPE.

Mensagem do Usuário: "{user_message}"

JSON Result:
```json
{{
  "intent": "...",
  "entities": {{ ... }}
}}
```"""
    try: # TRY EXTERNO (Chamada API)
        nlu_config = GenerationConfig(temperature=0.2, top_p=0.95);
        response = model.generate_content(nlu_prompt, generation_config=nlu_config, safety_settings=safety_settings)

        if response.candidates and response.candidates[0].content.parts:
            raw = response.candidates[0].content.parts[0].text; logger.debug(f"[NLU] Raw: {raw}")
            try: # TRY INTERNO (Parse JSON)
                match = re.search(r'```json\s*(\{.*?\})\s*```', raw, re.DOTALL|re.IGNORECASE);
                json_str = match.group(1) if match else raw;
                data = json.loads(json_str)
                if isinstance(data, dict) and "intent" in data:
                     data.setdefault("entities",{});
                     logger.info(f"[NLU] OK: {data['intent']}, Ents:{data['entities']}")
                     return data
                else:
                     logger.error(f"[NLU] JSON inválido/sem intent: {data}");
                     return {"intent": "UNCLEAR", "entities": {}}
            except json.JSONDecodeError as json_err: # EXCEPT do TRY INTERNO
                logger.error(f"[NLU] Erro decode JSON: {json_err}. String: '{json_str if 'json_str' in locals() else raw}'");
                return {"intent": "UNCLEAR", "entities": {}}
            except Exception as parse_err: # EXCEPT do TRY INTERNO
                logger.error(f"[NLU] Erro inesperado parse NLU: {parse_err}", exc_info=True);
                return {"intent": "UNCLEAR", "entities": {}}
        else: # Caso de resposta vazia/bloqueada do Gemini
            reason=getattr(response.candidates[0],'finish_reason','?') if response.candidates else 'X';
            logger.error(f"[NLU] Resp Gemini vazia/bloq NLU. Razão:{reason}");
            return None
    except Exception as e: # EXCEPT do TRY EXTERNO (Erro API)
        logger.error(f"[NLU] Erro GERAL chamada Gemini NLU: {e}", exc_info=True);
        return None

# --- Função Auxiliar para Verificar Perfil ---
def is_profile_incomplete(user_data):
    profile = user_data.get('profile', {}); required = ['birth_year','gender','height_cm','current_weight_kg','activity_level','goal']
    missing = [f for f in required if profile.get(f) is None or profile.get(f) == ""]; is_inc = bool(missing)
    logger.debug(f"[Profile Check] {'Incompleto: '+str(missing) if is_inc else 'Completo.'}")
    return is_inc, missing

# --- Função Auxiliar para Extrair Calorias ---
def extract_calories(text):
    """Extrai a estimativa de calorias da resposta do Gemini."""
    if not text: logger.warning("[Extract Kcal] Texto vazio."); return None
    logger.debug(f"[Extract Kcal] Tentando: '{text[:100]}...'")
    match_specific = re.search(r'Estimativa\s+CaloBot:\s*(\d+)\s*kcal', text, re.IGNORECASE)
    if match_specific:
        try: cal = int(match_specific.group(1)); return cal if 0<cal<10000 else None
        except (ValueError, IndexError): logger.error(f"[Extract Kcal] Erro Específico"); return None
    match_general = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:kcal|calorias)', text, re.IGNORECASE)
    if match_general:
        try: cal = int(float(match_general.group(1).replace(',','.'))); return cal if 0<cal<10000 else None
        except (ValueError, IndexError): logger.error(f"[Extract Kcal] Erro Geral"); return None
    logger.info("[Extract Kcal] Não encontrado."); return None

# --- Funções Auxiliares para Prompts de Resposta ---
def get_onboarding_prompt(user_display_name, field_name):
    logger.debug(f"[Prompt] Onboarding '{field_name}' user {user_display_name}")
    prompts = {'birth_year':"Ano nascimento (AAAA)? 🎂", 'gender':"Gênero (masc/fem)? 🧍", 'height_cm':"Altura em cm (ex:175)? 📏", 'current_weight_kg':"Peso atual kg (ex:70.5)? ⚖️", 'activity_level':"Nível atividade? Opções:'sedentário','leve','moderado','ativo','muito ativo' 🏃", 'goal':"Objetivo? Opções:'perder peso','manter peso','ganhar massa' 💪"}
    q = prompts.get(field_name, f"'{field_name}'?")
    task = f"Tarefa: Onboarding '{user_display_name}'. Peça '{field_name}' usando: '{q}'"; return f"{BASE_PERSONA_PROMPT}\n\n{task}\n\nCaloBot:"

def get_reprompt(user_display_name, field_name, invalid_input=""):
    logger.debug(f"[Prompt] Re-prompt '{field_name}' (input:'{invalid_input}')")
    reprompts = {'birth_year':"Ano inválido(AAAA).",'gender':"Inválido(masc/fem).",'height_cm':"Inválido(cm, números).",'current_weight_kg':"Inválido(kg, números).",'activity_level':"Inválido. Opções: 'sedentário',...,'muito ativo'.",'goal':"Inválido. Opções:'perder','manter','ganhar'.",'goal_confirmation':"Inválido. Digite 'sim' ou número kcal(1000-10000)."}
    msg = reprompts.get(field_name, "Inválido. Tente de novo.")
    task = f"Tarefa: User '{user_display_name}' deu input inválido ('{invalid_input}') p/ '{field_name}'. Peça de novo: '{msg}'"; return f"{BASE_PERSONA_PROMPT}\n\n{task}\n\nCaloBot:"

# --- Função Principal de Processamento (v30 - Simplificação try/except goal_confirmation) ---
def process_message(user_id, user_name_from_telegram, message_text):
    if not db or not model: logger.critical(f"Abort {user_id}: Deps off."); return "Problemas técnicos internos 🤖💦."
    logger.info(f"\n--- Processando user:{user_id}, Msg:'{message_text}' ---")
    user_data = firestore_manager.get_or_create_user(user_id, user_name_from_telegram)
    if not user_data: logger.error(f"Falha get/create {user_id}."); return "Problema buscar/criar dados."

    current_user_data=user_data.copy(); user_display_name=current_user_data.get('user_name','Usuário'); profile_data=current_user_data.get('profile',{}).copy(); diet_settings=current_user_data.get('diet_settings',{}).copy(); daily_tracking=current_user_data.get('daily_tracking',{}).copy(); user_state=current_user_data.get('user_state',{'awaiting':None}).copy(); currently_awaiting=user_state.get('awaiting')
    user_id_str=str(user_id); user_doc_ref=db.collection('users').document(user_id_str)
    prompt_final=""; intent="UNKNOWN"; entities={}; run_normal_processing=True; data_to_update={}

    logger.info(f"Estado: awaiting='{currently_awaiting}'")

    # --- LÓGICA 1: CHECK INTERNO ---
    if message_text == "__INTERNAL_ONBOARDING_CHECK__":
        logger.info("Check interno onboarding."); run_normal_processing = False; intent = "INTERNAL_CHECK"
        profile_incomplete, missing = is_profile_incomplete(current_user_data)
        if profile_incomplete:
            first=missing[0]; logger.info(f"Onboarding perfil: {first}");
            try: user_doc_ref.update({'user_state.awaiting': first}); logger.info(f"State='{first}'"); prompt_final=get_onboarding_prompt(user_display_name, first)
            except Exception as e: logger.error(f"Erro set await {first}: {e}"); prompt_final="Erro iniciar perfil."
        elif diet_settings.get('daily_calorie_goal') is None: logger.info("Onboarding meta."); prompt_final=f"{BASE_PERSONA_PROMPT}\n\nTarefa: Perfil ok! Diga prox passo=meta.\n\nCaloBot:"
        else: logger.info("Onboarding OK."); prompt_final = ""
        if not prompt_final: return None

    # --- LÓGICA 2: PROCESSAR RESPOSTA ESPERADA (ONBOARDING) ---
    elif currently_awaiting:
        logger.info(f"Proc. resposta p/ awaiting='{currently_awaiting}'...")
        run_normal_processing = False; is_valid = False; value_to_save = None; dict_to_update_key = None; field_to_save = currently_awaiting
        input_value_from_nlu = None; nlu_result = get_nlu_understanding(message_text)
        if nlu_result and nlu_result.get('intent') == 'PROVIDE_INFO' and 'info_value' in nlu_result['entities']:
            input_value_from_nlu = nlu_result['entities']['info_value']; logger.info(f"NLU extraiu: '{input_value_from_nlu}'")
            text_input_to_validate = str(input_value_from_nlu)
        else: logger.warning(f"NLU não ajudou ({nlu_result.get('intent') if nlu_result else 'N/A'}). Usando texto."); text_input_to_validate = message_text.strip()
        logger.debug(f"Validando '{text_input_to_validate}' p/ '{currently_awaiting}'")

        # --- Bloco de Validação ---
        if currently_awaiting == 'birth_year':
            try: year = int(text_input_to_validate); current_year = datetime.datetime.now(datetime.timezone.utc).year;
                 if 1900 < year <= current_year: value_to_save=year; is_valid=True; dict_to_update_key='profile'
                 else: logger.warning(f"Input inválido (range): {year}")
            except ValueError: logger.warning(f"Input inválido (não número): {text_input_to_validate}")
        elif currently_awaiting == 'gender':
            text_lower=text_input_to_validate.lower();
            if text_lower in ['masculino','m','male']: value_to_save='male'; is_valid=True; dict_to_update_key='profile'
            elif text_lower in ['feminino','f','female']: value_to_save='female'; is_valid=True; dict_to_update_key='profile'
        elif currently_awaiting == 'height_cm':
            try: height=float(text_input_to_validate.lower().replace('cm','').replace(',','.').strip());
                 if 100<=height<=250: value_to_save=int(height); is_valid=True; dict_to_update_key='profile'
            except ValueError: pass
        elif currently_awaiting == 'current_weight_kg':
            try: weight=float(text_input_to_validate.lower().replace('kg','').replace(',','.').strip());
                 if 30<=weight<=300: value_to_save=weight; is_valid=True; dict_to_update_key='profile'
            except ValueError: pass
        elif currently_awaiting == 'activity_level':
            text_lower=text_input_to_validate.lower(); map_act={'sedentário':'sedentary','leve':'light','moderado':'moderate','ativo':'active','muito ativo':'extra_active'}; valid_en=['sedentary','light','moderate','active','extra_active']; matched=None;
            for k,v in map_act.items():
                 if k in text_lower or k==text_lower: matched=v; break
            if not matched and text_lower in valid_en: matched=text_lower
            if matched: value_to_save=matched; is_valid=True; dict_to_update_key='profile'
        elif currently_awaiting == 'goal':
            text_lower=text_input_to_validate.lower(); map_goal={'perder':'lose','emagrecer':'lose','manter':'maintain','ganhar':'gain','massa':'gain'}; valid_en=['lose','maintain','gain']; matched=None;
            for k,v in map_goal.items():
                if k in text_lower or k==text_lower: matched=v; break
            if not matched and text_lower in valid_en: matched=text_lower
            if matched: value_to_save=matched; is_valid=True; dict_to_update_key='profile'
        # ----- INÍCIO: BLOCO goal_confirmation SIMPLIFICADO v30 -----
        elif currently_awaiting == 'goal_confirmation':
            custom_goal = None; is_confirmation = False; is_valid = False; value_to_save = None;
            dict_to_update_key = None; field_to_save = 'daily_calorie_goal'; # Default field
            potential_goal_str = None; nlu_intent = None; potential_goal = None # Init potential_goal

            if nlu_result: nlu_intent = nlu_result.get('intent')

            # Tentativa 1: Obter string numérica (NLU ou Fallback)
            parsed_value_source = None
            if nlu_intent == 'PROVIDE_INFO' and 'info_value' in nlu_result['entities']:
                potential_goal_str = str(nlu_result['entities']['info_value'])
                parsed_value_source = "NLU"
            else: # Fallback para regex no texto original
                cal_match = re.search(r'\d+', message_text)
                if cal_match:
                    potential_goal_str = cal_match.group(0)
                    parsed_value_source = "REGEX_FALLBACK"
                else:
                    logger.warning("Nenhuma string numérica encontrada via NLU ou Regex.")

            # Tentativa 2: Converter string para int (se encontrada)
            if potential_goal_str:
                try: # TRY apenas para a conversão
                    cleaned_str = re.sub(r'[^\d]', '', potential_goal_str) # Limpa novamente
                    if cleaned_str:
                        potential_goal = int(cleaned_str)
                        logger.info(f"String convertida para int: {potential_goal}")
                    else:
                        logger.warning(f"String numérica estava vazia pós limpeza: '{potential_goal_str}'")
                        potential_goal = None # Garante que é None
                except (ValueError, TypeError): # EXCEPT apenas para a conversão
                    logger.warning(f"Erro ao converter '{potential_goal_str}' para int.")
                    potential_goal = None # Garante que é None se a conversão falhar

            # Tentativa 3: Validar número (se convertido) e Definir custom goal
            if potential_goal is not None: # Só valida se a conversão funcionou
                if 1000 <= potential_goal <= 10000:
                     custom_goal = potential_goal
                     is_valid = True # É um número válido e no range
                     value_to_save = custom_goal
                     dict_to_update_key = 'diet_settings'
                     logger.info(f"Meta custom ({parsed_value_source}) válida: {value_to_save}")
                else:
                     logger.warning(f"Meta numérica ({parsed_value_source}) fora do range: {potential_goal}")
                     custom_goal = potential_goal # Guarda para mensagem de erro

            # Tentativa 4: Checar confirmação (NLU ou fallback) se não for custom goal válido
            if not is_valid:
                text_lower_orig = message_text.strip().lower()
                yes_words = ['sim', 's', 'ok', 'k', 'aceito', 'confirmado', 'confirmo', 'yes', 'y']
                if (nlu_intent in ['AFFIRMATION', 'CONFIRMATION']) or (text_lower_orig in yes_words):
                    is_confirmation = True
                    logger.info(f"Confirmação detectada (NLU: {nlu_intent in ['AFFIRMATION', 'CONFIRMATION']}, Fallback: {text_lower_orig in yes_words})")
                    # Recalcula meta sugerida
                    age=firestore_manager.calculate_age(profile_data.get('birth_year')); bmr=firestore_manager.calculate_bmr_mifflin(profile_data.get('current_weight_kg'),profile_data.get('height_cm'), age, profile_data.get('gender')); tdee=firestore_manager.calculate_tdee(bmr, profile_data.get('activity_level')); suggested=firestore_manager.suggest_calorie_goal(tdee, profile_data.get('goal'))
                    if suggested:
                        value_to_save=suggested; is_valid=True; dict_to_update_key='diet_settings'; logger.info(f"Meta sugerida ({value_to_save}) aceita.")
                    else: logger.error("Erro recalcular meta.") # is_valid continua False

            # Gera reprompt final se nada deu certo
            if not is_valid:
                 logger.warning(f"Input goal_conf inválido final: {message_text}")
                 if custom_goal is not None and not (1000<=custom_goal<=10000): prompt_final=get_reprompt(user_display_name,currently_awaiting,f"{message_text}(Meta fora range)")
                 else: prompt_final=get_reprompt(user_display_name,currently_awaiting,message_text)
                 intent=f"REPROMPT_{currently_awaiting.upper()}"; run_normal_processing=False
        # ----- FIM: BLOCO goal_confirmation SIMPLIFICADO v30 -----
        # --- Fim da Validação ---

        if is_valid: logger.info(f"Input '{currently_awaiting}' OK:{value_to_save}")
        else: logger.warning(f"Input '{currently_awaiting}' Inválido(final):{text_input_to_validate}")

        # --- Ação Pós-Validação ---
        if is_valid and dict_to_update_key and field_to_save is not None and value_to_save is not None:
            logger.info("Validação OK. Preparando save..."); data_payload = None
            if dict_to_update_key == 'profile': profile_data[field_to_save]=value_to_save; data_payload=profile_data; logger.debug(f"Profile local:{profile_data}")
            elif dict_to_update_key == 'diet_settings': diet_settings[field_to_save]=value_to_save; data_payload=diet_settings; logger.debug(f"Diet local:{diet_settings}")
            else: logger.error(f"Chave inválida '{dict_to_update_key}'")
            if data_payload: user_state['awaiting']=None; data_to_update={dict_to_update_key:data_payload,'user_state':user_state}; currently_awaiting=None; run_normal_processing=True; logger.info("Pronto p/ salvar e continuar.")
            else: run_normal_processing=False; prompt_final="Erro preparar dados."; intent="ERROR_PREPARE_SAVE"
        elif not prompt_final: logger.warning("Input inválido, gerando reprompt."); prompt_final = get_reprompt(user_display_name, currently_awaiting, message_text); intent=f"REPROMPT_{currently_awaiting.upper()}"; run_normal_processing = False

    # --- LÓGICA 3: SALVAR DADOS ---
    if data_to_update:
        try: logger.info(f"Salvando:{data_to_update}"); user_doc_ref.update(data_to_update); logger.info("Salvo OK."); user_data=firestore_manager.get_or_create_user(user_id,None); current_user_data=user_data.copy(); profile_data=current_user_data.get('profile',{}).copy(); diet_settings=current_user_data.get('diet_settings',{}).copy(); daily_tracking=current_user_data.get('daily_tracking',{}).copy(); user_state=current_user_data.get('user_state',{'awaiting':None}).copy(); logger.info("Dados recarregados.")
        except Exception as e: logger.error(f"ERRO SAVE:{e}", exc_info=True); prompt_final="Problema ao salvar."; run_normal_processing=False; intent="ERROR_FIRESTORE_SAVE"

    # --- LÓGICA 4: PROCESSAMENTO NORMAL (via NLU) ---
    if run_normal_processing and not prompt_final:
        logger.info("Bloco proc. normal/pós-onboarding.")
        profile_incomplete, missing = is_profile_incomplete(current_user_data)
        if profile_incomplete: # Onboarding Perfil
            first=missing[0]; logger.info(f"Onboarding perfil:{first}."); intent=f"ONBOARDING_{first.upper()}"
            try: user_doc_ref.update({'user_state.awaiting':first}); logger.info(f"State='{first}'"); prompt_final=get_onboarding_prompt(user_display_name,first)
            except Exception as e: logger.error(f"Erro set await {first}:{e}"); prompt_final="Erro config perfil."; intent="ERROR_SET_AWAITING"
        elif diet_settings.get('daily_calorie_goal') is None: # Onboarding Meta
            logger.info("Onboarding meta."); intent="ONBOARDING_GOAL_SUGGESTION"
            age=firestore_manager.calculate_age(profile_data.get('birth_year')); bmr=firestore_manager.calculate_bmr_mifflin(profile_data.get('current_weight_kg'), profile_data.get('height_cm'), age, profile_data.get('gender')); tdee=firestore_manager.calculate_tdee(bmr, profile_data.get('activity_level')); suggested=firestore_manager.suggest_calorie_goal(tdee, profile_data.get('goal'))
            if suggested: logger.info(f"Meta sugerida:{suggested}");
                 try: user_doc_ref.update({'user_state.awaiting':'goal_confirmation'}); logger.info("State='goal_confirmation'")
                      prompt_tarefa=(f"Tarefa:Perfil ok! TDEE={tdee}, obj='{profile_data.get('goal')}'. Sugiro meta {suggested} kcal. Apresente, pergunte 'sim' ou número."); prompt_final=f"{BASE_PERSONA_PROMPT}\n\n{prompt_tarefa}\n\nCaloBot:"
                 except Exception as e: logger.error(f"Erro set await goal_conf:{e}", exc_info=True); prompt_final="Erro prep pergunta meta."; intent="ERROR_SET_AWAITING_GOAL"
            else: logger.error("Erro calc meta."); prompt_tarefa="Erro cálculo meta."; prompt_final=f"{BASE_PERSONA_PROMPT}\n\n{prompt_tarefa}\n\nCaloBot:"; intent="ERROR_CALC_SUGGESTION"
        else: # Onboarding Completo -> NLU
            logger.info("Onboarding OK. Usando NLU..."); nlu_result = get_nlu_understanding(message_text)
            if nlu_result:
                intent = nlu_result.get('intent', 'UNCLEAR'); entities = nlu_result.get('entities', {}); logger.info(f"NLU->Intent:{intent}, Entities:{entities}")
                calorie_goal=diet_settings.get('daily_calorie_goal'); cal_today=daily_tracking.get('calories_consumed',0); cal_rem=calorie_goal-cal_today if calorie_goal else None; status=f"Meta:{calorie_goal} Cons:{cal_today}";
                if cal_rem is not None: status += f" Restam:{cal_rem}"; logger.debug(f"Contexto:{status}")
                prompt_persona = f"{BASE_PERSONA_PROMPT}\n\nContexto User '{user_display_name}': {status}."
                # Roteamento NLU
                if intent=="LOG_FOOD": log_desc=entities.get('food_items',[message_text]); log_qty=entities.get('quantity'); log_meal=entities.get('meal_time'); log_ctx=f"Alim:{','.join(log_desc)}"; if log_qty:log_ctx+=f",Qtd:{log_qty}"; if log_meal:log_ctx+=f",Ref:{log_meal}"; task=(f"Tarefa:User registrou:'{message_text}'(Extr:{log_ctx}). 1.Estime kcal('Estimativa CaloBot: XXX kcal.'). 2.Comente. 3.Mencione status({status},+estimativa).")
                elif intent=="ASK_SUGGESTION": pref=entities.get('preference'); constr=entities.get('dietary_constraint'); sug_ctx=f"Restam {cal_rem if cal_rem is not None else 'Muitas'} kcal."; if pref:sug_ctx+=f" Pref:{pref}."; if constr:sug_ctx+=f" Restr:{','.join(constr)}."; task=(f"Tarefa:User pede sugestão:'{message_text}'. Contexto:{sug_ctx}. Sugira 2-3 opções c/ kcal.")
                elif intent=="GET_STATUS": task=(f"Tarefa:User perguntou status('{message_text}'). Responda c/ contexto:{status}.")
                elif intent=="GET_PROFILE": field=entities.get('profile_field','geral'); task=(f"Tarefa:User pediu perfil('{message_text}',campo:{field}). Apresente:{profile_data}. Foco no campo se esp.")
                elif intent=="UPDATE_PROFILE": logger.warning(f"Intent UPDATE_PROFILE não impl. Ents:{entities}"); task=f"Tarefa:User tentou atualizar perfil('{message_text}'). Informe não impl."
                elif intent in ["GREETING", "FAREWELL", "AFFIRMATION", "NEGATION", "HELP", "CHITCHAT"]: task=(f"Tarefa:User enviou '{intent}':'{message_text}'. Responda apropriadamente.")
                elif intent=="OUT_OF_SCOPE": task=(f"Tarefa:User fora do escopo('{message_text}'). Diga foco nutrição/saúde.")
                else: logger.warning(f"Intent não tratada/incerta:'{intent}'."); task=(f"Tarefa:User:'{message_text}'. Intenção incerta. Responda conversacionalmente.")
                prompt_final = f"{prompt_persona}\n\n{task}\n\nCaloBot:"
            else: logger.error("Falha NLU."); prompt_final=f"{BASE_PERSONA_PROMPT}\n\nTarefa:Erro entender:'{message_text}'.Peça desculpas/reformulaçao.\n\nCaloBot:"; intent="ERROR_NLU"

    # --- LÓGICA 5: CHAMAR GEMINI PARA RESPOSTA FINAL ---
    resposta_texto = "Eita! Cérebro engasgou 🧠💥 Tenta de novo?"; estimated_calories = None; update_success = False
    if prompt_final:
        logger.info(f"Enviando prompt final(Intent:{intent})..."); logger.debug(f"Prompt Final Completo:\n{prompt_final}")
        try:
            if not model or not generation_config or not safety_settings: logger.critical("Deps off p/ chamada final."); raise Exception("Modelo/Config não ok.")
            response = model.generate_content(prompt_final, generation_config=generation_config, safety_settings=safety_settings); logger.info("Resp final recebida.")
            if response.candidates:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    try: resposta_texto = candidate.content.parts[0].text.strip(); logger.info("Texto resposta OK.")
                         if intent == "LOG_FOOD": logger.info("Extraindo kcal p/ LOG_FOOD...")
                              estimated_calories = extract_calories(resposta_texto)
                              if estimated_calories and estimated_calories>0: logger.info(f"Kcal:{estimated_calories}. Salvando..."); update_success = firestore_manager.update_daily_calories(user_id, estimated_calories, message_text);
                                   if update_success: logger.info("DB update OK.")
                                   else: logger.error("Falha update DB LOG."); resposta_texto += "\n\n(Erro salvar 😟)"
                              else: logger.warning("Não extraiu kcal LOG."); resposta_texto += "\n\n(Não estimei kcal 🤔)"
                    except Exception as e: logger.error(f"Erro proc resp final:{e}",exc_info=True); resposta_texto="Erro proc resp."
                else: reason=getattr(candidate,'finish_reason','?'); safety=getattr(candidate,'safety_ratings','?'); logger.warning(f"Resp final vazia/bloq. Razão:{reason},Safety:{safety}"); resposta_texto=f"Não processei(Motivo:{reason})."; if reason=="SAFETY": logger.warning("BLOQUEIO SEG.")
            else: logger.error("Resp final sem candidates."); resposta_texto="Resp vazia inesperada."
        except Exception as e: logger.error(f"ERRO GERAL chamada final:{e}",exc_info=True); resposta_texto="Erro comunicação."
    else: logger.info("Nenhum prompt final gerado."); return None

    if intent == "LOG_FOOD": logger.info(f"LOG_FOOD:UpdOK?{update_success},Kcal?{estimated_calories}")
    logger.info(f"--- FIM user:{user_id}(Intent:{intent}).Resp:'{resposta_texto[:100]}...' ---")
    return resposta_texto

# --- Bloco de Teste (v30 - Usa código corrigido) ---
if __name__ == "__main__":
    if db and model and generation_config and safety_settings:
        print("\n--- INICIANDO TESTE DE INTEGRAÇÃO CALOBOT_CORE (v30 - NLU + Fix SyntaxError goal_conf) ---")
        test_user_id_nlu = 999999902; test_user_name_nlu = "Tester NLU V30"
        print(f"\n\n----- PREP: Resetando {test_user_id_nlu} -----"); user_doc_ref_reset = db.collection('users').document(str(test_user_id_nlu))
        try: user_doc_ref_reset.delete(); print(f"Doc {test_user_id_nlu} deletado.")
        except: print(f"Doc {test_user_id_nlu} não existia/erro delete.")
        print(f"\n----- PREP: Config user pré-onboarded -----")
        try: initial_data={'telegram_user_id':test_user_id_nlu, 'user_name':test_user_name_nlu, 'created_at':firestore.SERVER_TIMESTAMP,'last_interaction_at':firestore.SERVER_TIMESTAMP,'profile':{'birth_year':1990,'gender':'male','height_cm':180,'current_weight_kg':80,'activity_level':'light','goal':'maintain'},'diet_settings':{'daily_calorie_goal':2000,'diet_type':'standard'},'daily_tracking':{'date':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),'calories_consumed':0,'log_today':[]},'user_state':{'awaiting':None}}; user_doc_ref_reset.set(initial_data); print(f"User {test_user_id_nlu} config OK.")
        except Exception as e: print(f"ERRO config user: {e}"); exit()
        conversa_nlu = [ ("Oi CaloBot", "GREETING"), ("Comi um pão na chapa e café com leite no café da manhã", "LOG_FOOD"), ("Qual meu status de calorias hoje?", "GET_STATUS"), ("Sugere algo leve pro almoço, sem carne vermelha", "ASK_SUGGESTION"), ("Valeu!", "AFFIRMATION/FAREWELL?"), ("Qual minha altura mesmo?", "GET_PROFILE"), ("quem descobriu o brasil?", "OUT_OF_SCOPE"), ]
        print("\n--- Iniciando seq teste NLU ---")
        for i, (message, expected) in enumerate(conversa_nlu):
            print(f"\n\n----- TESTE NLU {i+1}: User:'{message}' (Esperado:~{expected}) -----")
            response = process_message(test_user_id_nlu, None, message)
            if response: print(f"\n--- Resp Final NLU {i+1} (CaloBot): ---\n{response}")
            else: print(f"\n--- Resp Final NLU {i+1} (CaloBot): --- (Sem resp)")
            import time; time.sleep(4)
        print("\n\n[TESTE NLU FINAL] Lendo dados..."); final_data = firestore_manager.get_or_create_user(test_user_id_nlu, None)
        if final_data: print(f"\n[VERIFICAÇÃO NLU FINAL]\n  Diet Settings:{final_data.get('diet_settings')}\n  Daily Tracking:{final_data.get('daily_tracking')}\n  User State:{final_data.get('user_state')}")
        else: print(f"[VERIF NLU FINAL] Falha ler dados.")
        print("\n--- TESTE INTEGRAÇÃO CALOBOT_CORE (NLU) CONCLUÍDO ---")
    else: print("\nERRO CRÍTICO: Deps não ok. Teste abortado.")
