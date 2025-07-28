from langchain_openai.chat_models import ChatOpenAI
from langchain.schema import HumanMessage
import os
import re
import traceback
import tiktoken  # Para manejar tokens
from utils import generate_unique_filename, log_message
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import openai
from openai import OpenAI
import streamlit as st

# Configuración de OpenAI

openai.api_key = st.secrets["OPENAI_API_KEY"]
openai.organization = st.secrets["OPENAI_API_ORGANIZATION"]

chat = ChatOpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    organization=os.environ["OPENAI_API_ORGANIZATION"],
    model="gpt-4o-mini",  # Cambia al modelo
    temperature=0.5,
    max_retries=3
)

MAX_TOKENS = 8192  # Límite del modelo
RESERVED_OUTPUT_TOKENS = 3000  # Reservar espacio para la respuesta del modelo
def get_tokenizer(model: str):
    """
    Devuelve el tokenizador adecuado para el modelo, usando un fallback si no está mapeado.
    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # Usa el codificador base si el modelo no tiene un mapeo explícito
        return tiktoken.get_encoding("cl100k_base")

def split_text_by_tokens(text: str, model: str, max_tokens: int) -> list:
    """
    Divide el texto en fragmentos que no superen el límite de tokens.
    """
    encoding=get_tokenizer(model)
    #encoding = tiktoken.encoding_for_model(model)
    tokens = encoding.encode(text)
    fragment_size = max_tokens - RESERVED_OUTPUT_TOKENS
    fragments = [encoding.decode(tokens[i:i + fragment_size]) for i in range(0, len(tokens), fragment_size)]
    return fragments

# Tarifas estimadas de OpenAI por 1,000 tokens (USD) para modelos comunes
COST_PER_1M_INPUT_TOKENS = 0.15  # Ajusta según el modelo 
COST_PER_1M_OUTPUT_TOKENS = 0.6  # Ajusta según el modelo

# Variables globales para acumular tokens y costos
total_input_tokens = 0
total_output_tokens = 0
total_cost = 0.0

def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Calcula la cantidad de tokens en un texto dado."""
    encoding=get_tokenizer(model)
    #encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(text))

def ask_openai(prompt: str, max_retries: int = 5) -> str:
    """Envía una solicitud a OpenAI e informa sobre tokens y costo, incluyendo acumulado."""
    global total_input_tokens, total_output_tokens, total_cost  # Habilitar modificación global

    retries = 0
    input_tokens = count_tokens(prompt)  # Calcular tokens de entrada

    while retries < max_retries:
        try:
            response = chat.invoke([HumanMessage(content=prompt)])
            output_content = response.content if response else ""

            # Calcular tokens de salida
            output_tokens = count_tokens(output_content)

            # Calcular costo estimado
            input_cost = (input_tokens / 1000000) * COST_PER_1M_INPUT_TOKENS
            output_cost = (output_tokens / 1000000) * COST_PER_1M_OUTPUT_TOKENS
            request_cost = input_cost + output_cost

            # Actualizar acumulados
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_cost += request_cost

            # Log de la solicitud actual
            log_message(f"✅ Solicitud exitosa a OpenAI.")
            log_message(f"📊 Tokens usados - Entrada: {input_tokens}, Salida: {output_tokens}, Total: {input_tokens + output_tokens}")
            log_message(f"💲 Costo estimado de esta solicitud: ${request_cost:.6f} USD")

            # Log del total acumulado
            log_message(f"📈 Total acumulado - Tokens Entrada: {total_input_tokens}, Tokens Salida: {total_output_tokens}, Total Tokens: {total_input_tokens + total_output_tokens}")
            log_message(f"💰 Costo total acumulado: ${total_cost:.6f} USD")

            return output_content

        except Exception as e:
            log_message(f"⚠️ Error al interactuar con OpenAI: {str(e)}")
            if "rate_limit_exceeded" in str(e):
                wait_time = 10 + (retries * 5)  # Incrementar el tiempo de espera en cada intento
                log_message(f"⏳ Límite alcanzado. Reintentando en {wait_time} segundos...")
                time.sleep(wait_time)
                retries += 1
            else:
                log_message(f"❌ Error de solicitud inválida a OpenAI: {str(e)}")
                return None

    return None  # Devuelve None si se agotaron los reintentos

def ask_openai_concurrent(prompt):
    """
    Envolver la función de llamada a OpenAI para manejo concurrente.
    """
    try:
        response = ask_openai(prompt)
        return response
    except Exception as e:
        print(f"Error en la llamada a OpenAI: {e}")
        return None

def extract_modules_and_chapters_with_openai(content: str) -> list:
    """
    Usa OpenAI para identificar capítulos o módulos en el contenido del curso.

    :param content: Texto completo del temario extraído del PDF.
    :return: Lista de títulos de capítulos o módulos.
    """
    prompt = f"""
    Tienes el contenido de un temario de curso. Extrae y lista únicamente los nombres de los módulos o capítulos principales.
    Los módulos/ capítulos están organizados después de encabezados como "Esquema del curso" o "Course Outline".
    Extrae el titulo del pdf que esta en la primer linea y agregalo al principio.
    
    Contenido del temario:
    {content}

    Devuelve la lista en este formato:
    - [Curso: Titulo]
    - [Título del módulo 1 o capítulo 1]
    - [Título del módulo 2 o capítulo 2]
    .
    .
    .
    """
    try:
        response = ask_openai(prompt)
        if response:
            # Procesar la respuesta para obtener una lista
            chapters = [line.strip("- ").strip("[]").strip() for line in response.split("\n") if line.startswith("-")]
            return chapters
        else:
            print("OpenAI no devolvió una respuesta válida.")
            return []
    except Exception as e:
        print(f"Error al extraer capítulos con OpenAI: {e}")
        return []

def segment_content_with_openai(content: str, chapters: list, output_dir: str, identifier: str, curso: list, subchapters:list) -> dict:
    """
    Segmenta el contenido por temas usando OpenAI en paralelo y guarda el resultado en un archivo .txt.
    Maneja fragmentos si el contenido supera el límite de tokens.
    """
    fragments = split_text_by_tokens(content, "gpt-4o-mini", MAX_TOKENS)  # Especifica el modelo aquí
    segments = {}
    output_text = []  # Almacena los segmentos para guardar en archivo

    prompts = [
        f"""
        A continuación se proporciona un contenido y una lista de temas y subtemas del {curso}'. 
        Clasifica el contenido por cada tema y subtema y devuelve fragmentos relevantes neta y exclusivamente del Contenido. 
        Los fragmentos deben ser texto literal del contenido, no debes hacer modificaciones a los fragmentos como por ejemplo juntar fragmentos de diferentes partes. 
        Los fragmentos deben ser estrictamente técnicos, conceptuales y relacionados con procesos clave del tema o subtema.
        Evita fragmentos triviales.
        Los fragmentos que vengan en varias líneas debes unirlos en una sola linea sin cambiar la gramática, solo quita los saltos de línea.
        Debes colocar al final de cada fragmento la fuente seguido de source, la fuente es el nombre del archivo bien sea un txt, pdf, xlsx, docx.
        En caso de no encontrar fragmentos relevantes comenta "No se encontraron fragmentos relevantes" y no inventes fragmentos.

        Temas:
        {chr(10).join(chapters)}
        Subtemas:
        {chr(10).join(subchapters)}

        Contenido:
        '{fragment}'

        Responde en este formato:
        Tema: [Nombre del tema]
        Subtema: [Nombre del subtema]
        Fragmento: [Texto relevante del contenido [Nombre del archivo]]
        Fragmento: [Texto relevante del contenido [Nombre del archivo]]
        .
        .
        .
        """
        for fragment in fragments
    ]

    # Usar ThreadPoolExecutor para manejar concurrencia
    with ThreadPoolExecutor(max_workers=3) as executor:  # Ajusta max_workers según las necesidades y la API rate limit
        future_to_prompt = {executor.submit(ask_openai_concurrent, prompt): prompt for prompt in prompts}

        for future in as_completed(future_to_prompt):
            try:
                response = future.result()
                if not response:
                    print("No se recibió respuesta de OpenAI.")
                    continue

                print("Respuesta recibida de OpenAI:")
                print(response)

                # Procesar la respuesta en un diccionario
                current_topic = None
                for line in response.split("\n"):
                    line = line.strip()
                    if line == "No se encontraron fragmentos relevantes":
                        line = line.replace("No se encontraron fragmentos relevantes", " ").strip()
                    if line == "No se encontraron fragmentos relevantes.":
                        line = line.replace("No se encontraron fragmentos relevantes.", " ").strip()
                    if line.startswith("Tema:"):
                        current_topic = line.replace("Tema:", "").strip()
                        if current_topic not in segments:
                            segments[current_topic] = []
                    elif line.startswith("Fragmento:") and current_topic:
                        fragment = line.replace("Fragmento:", "Fragmento: ").strip()
                        segments[current_topic].append(fragment)
                    elif current_topic and line:
                        # Si la línea no empieza con "Fragmento:" pero pertenece a un tema, se añade
                        segments[current_topic].append(line)

            except Exception as e:
                print(f"Error al procesar un fragmento: {e}")
                continue

    # Combinar fragmentos por tema en un solo string
    for topic in segments:
        segments[topic] = "\n".join(segments[topic])
   

    # Guardar los segmentos en un archivo .txt
    output_file_path = generate_unique_filename(os.path.join(output_dir, f"segmentos_{identifier}"), ".txt")
    try:
        with open(output_file_path, "w", encoding="utf-8") as file:
            for topic, fragment in segments.items():
                file.write(f"Tema: {topic}\n")
                file.write(f"\n{fragment}\n")
                file.write("-" * 50 + "\n")  # Separador entre capítulos
        print(f"Segmentos guardados en: {output_file_path}")
    except Exception as e:
        print(f"Error al guardar los segmentos en archivo: {e}")

    return segments

def generate_matching_questions(topic: str, fragment: str, num_preg_tip: int, curso: str,formato) -> str:
    """
    Genera preguntas de matching basadas en el fragmento.
    """
    if formato.lower()=='quizz':    
        instrucciones = f"""
        Genera **exactamente** {num_preg_tip} preguntas de tipo *Matching* relacionadas con el tema '{topic}' del '{curso}'.
        Usa exlusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        Asegúrate de que las preguntas sean técnicas, conceptuales y relevantes.

        **Restricciones adicionales:**
            1. Incluye solo información relevante al tema sin preguntas triviales o de introducción.
            2. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
            3. Cada pregunta debe presentar una lista de conceptos (A, B, C...) seguidos de su respectiva descripción como respuesta correcta, en el mismo orden.
            4. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omíte el fragmento y pregunta.
            5. Las preguntas deben cubrir tres niveles de dificultad: Básico, Intermedio y Avanzado.
            6. Las definiciones NO pueden ser iguales a los conceptos.

        **Formato esperado:**
        - Relaciona los conceptos con su definición correspondiente.
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:".

        **Ejemplo de formato:**
        1. Pregunta: [Texto de la afirmación]
            A) Opción 1
            B) Opción 2
            C) Opción 3
            D) Opción 4
            E) Opción 5 (opcional)
            Tipo: Relación de columnas
            Respuesta correcta: Descripción que corresponde a A); Descripción que corresponde a B); Descripción que corresponde a C); Descripción que corresponde a D); Descripción que corresponde a E
            Fragmento: [Texto relevante del contenido [Nombre del archivo]
"""


    else:    
        instrucciones = f"""
        Genera **exactamente** {num_preg_tip} preguntas de tipo *Matching* relacionadas con el tema '{topic}' del '{curso}'.
        Usa exlusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        Asegúrate de que las preguntas sean técnicas, conceptuales y relevantes.

        **Restricciones adicionales:**
            1. Incluye solo información relevante al tema sin preguntas triviales o de introducción.
            2. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
            3. Cada pregunta debe presentar una lista de conceptos (A, B, C...) seguidos de su respectiva descripción como respuesta correcta, en el mismo orden.
            4. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omíte el fragmento y pregunta.
            5. Las preguntas deben cubrir tres niveles de dificultad: Básico, Intermedio y Avanzado.
            6. Las definiciones NO pueden ser iguales a los conceptos.

        **Formato esperado:**
        - Relaciona los conceptos con su definición correspondiente.
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:".
        - Al final de cada concepto añade un punto final.
        - Al final de cada descripción añade un punto final.

        **Ejemplo de formato:**
        1. Pregunta: [Texto de la afirmación.]
            A) Opción 1.
            B) Opción 2.
            C) Opción 3.
            D) Opción 4.
            E) Opción 5. (opcional)
            Tipo: Matching
            Respuesta correcta: Descripción que corresponde a A.; Descripción que corresponde a B.; Descripción que corresponde a C.; Descripción que corresponde a D.; Descripción que corresponde a E.
            Fragmento: [Texto relevante del contenido [Nombre del archivo]

    Contenido:
    '{fragment}'
    """
    try:
        response = ask_openai(instrucciones)
        return response.strip() if response else "No se pudieron generar preguntas de verdadero/falso."
    except Exception as e:
        print(f"Error al generar preguntas de verdadero/falso: {e}")
        return "No se pudieron generar preguntas de verdadero/falso."


def generate_multiple_choice_questions(topic: str, fragment: str, num_preg_tip: int, curso: list,formato) -> str:
    """
    Genera preguntas de multiple choice basadas en el fragmento.
    """
    if formato.lower()=='quizz':
        instrucciones=f"""
        Estás generando preguntas de multiple choice para un examen en formato **quizz** del curso '{curso}', tema '{topic}'.
        IMPORTANTE:
        - El sistema ya calculó que debes generar exactamente {num_preg_tip} preguntas.
        - No debes generar más ni menos.
        - No calcules tú la cantidad de preguntas.
        - Debes numerar las preguntas del 1 al {num_preg_tip}, sin repetir ni agregar extras.
        - Usa exlusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        - Asegúrate de incluir preguntas estrictamente técnicas, conceptuales y relacionadas con procesos clave del tema.
        - Asegúrate de incluir de 1-2 preguntas de 'fill in the blanks' en la categoría 'Multiple Choice'.
        - Si la respuesta de un 'fill in the blanks' queda en medio de la oración, las opciones de respuesta deberán empezar en minúscula. En otro caso, que comiencen con mayúscula.

        **Restricciones:**
    
            1. Evita preguntas triviales o de introducción.
            2. Asegúrate de que las opciones incorrectas sean técnicamente plausibles, pero incorrectas en el contexto de la pregunta.
            3. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
            4. No generes preguntas a partir de ejemplos del contenido.
            5. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omítela.
            6. NO generes todas las preguntas como 'fill in the blanks'

        **Formato esperado:**
        - Cada pregunta debe tener 5 opciones (A, B, C, D, E), con una marcada como correcta.
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:".

            **Ejemplo de formato:**
            1. Pregunta: [Texto de la pregunta] (Selecciona la opción correcta)
            A) Opción 1
            B) Opción 2
            C) Opción 3
            D) Opción 4
            
            Tipo: Multiple Choice
            Respuesta correcta: [Letra]
            Fragmento: [Texto relevante del contenido [Nombre del archivo]]

    
        **Ejemplo de preguntas:**
        1. Pregunta:  ¿Cuál se recomienda como parte del principio rector 'progresar iterativamente con retroalimentación'?
        A) Analizar toda la situación en detalle antes de tomar cualquier acción.
        B) Reducir el número de pasos que producen resultados tangibles.
        C) Prohibir cambios a los planes después de que se hayan finalizado.
        D) Organizar el trabajo en unidades pequeñas y manejables.
        E) Dividir en pequeñas tareas para dar mayor detalle
        
        Tipo: Multiple Choice
        Respuesta correcta: D
        Fragmento: El principio "progresar iterativamente con retroalimentación" recomienda dividir el trabajo en unidades pequeñas y manejables, lo que facilita la obtención de resultados tangibles y permite la incorporación de retroalimentación continua para mejoras incrementales.[Documento del que se extrajo]

"""
    else:

        instrucciones = f"""
        Genera **exactamente** {num_preg_tip} preguntas de multiple choice para un examen técnico sobre el tema '{topic}' del '{curso}'.
        Usa exlusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        Asegúrate de incluir preguntas estrictamente técnicas, conceptuales y relacionadas con procesos clave del tema.
        Asegúrate de incluir preguntas de 'fill in the blanks' en la categoría 'Multiple Choice'
        Si la respuesta de un 'fill in the blanks' queda en medio de la oración, las opciones de respuesta deberán empezar en minúscula, en otro caso, que comiencen con mayúscula.
        Al final de cada opción de respuesta agrega un punto final, menos para las opciones 'fill in the blanks'.
        El 30% de las preguntas deben requerir seleccionar la opción incorrecta, como por ejemplo:
        - ¿Cuál de las siguientes opciones NO es correcta?
        - ¿Cuál de los siguientes pertenece al tema?
        - ¿Cuál de estas es una práctica recomendada?

        **Restricciones:**
        1. Las preguntas deben cubrir tres niveles de dificultad: Básico, Intermedio y Avanzado.
        2. Evita preguntas triviales o de introducción.
        3. Asegúrate de que las opciones incorrectas sean técnicamente plausibles, pero incorrectas en el contexto de la pregunta.
        4. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
        5. No generes preguntas a partir de ejemplos del contenido.
        6. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omítela.


        **Formato esperado:**
        - Cada pregunta debe tener 5 opciones (A, B, C, D, E), con una marcada como correcta.
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:".

        **Ejemplo de formato:**
        1. Pregunta: [Texto de la pregunta] (Selecciona la opción correcta)
            A) Opción 1.
            B) Opción 2.
            C) Opción 3.
            D) Opción 4.
            E) Opción 5.
        
            Tipo: Multiple Choice
            Respuesta correcta: [Letra]
            Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **Ejemplo de preguntas:**
        1. Pregunta:  ¿Cuál se recomienda como parte del principio rector 'progresar iterativamente con retroalimentación'?
            A) Analizar toda la situación en detalle antes de tomar cualquier acción.
            B) Reducir el número de pasos que producen resultados tangibles.
            C) Prohibir cambios a los planes después de que se hayan finalizado.
            D) Organizar el trabajo en unidades pequeñas y manejables.
            E) Dividir en pequeñas tareas para dar mayor detalle    
            Tipo: Multiple Choice
            Respuesta correcta: D
            Fragmento: El principio "progresar iterativamente con retroalimentación" recomienda dividir el trabajo en unidades pequeñas y manejables, lo que facilita la obtención de resultados tangibles y permite la incorporación de retroalimentación continua para mejoras incrementales.[Documento del que se extrajo]

    Contenido:
    '{fragment}'
    """
    try:
        response = ask_openai(instrucciones)
        return response.strip() if response else "No se pudieron generar preguntas de multiple choice."
    except Exception as e:
        print(f"Error al generar preguntas de multiple choice: {e}")
        return "No se pudieron generar preguntas de multiple choice."


def generate_checkboxes_questions(topic: str, fragment: str, num_preg_tip: int, curso: str, formato) -> str:
    """
    Genera preguntas tipo checkboxes. 
    """
    if formato.lower()=='quizz':
        instrucciones=f"""
        Estás generando preguntas en formato checkboxes, donde más de una opción es correcta, para un examen en formato **quizz** del curso '{curso}', tema '{topic}'.
        IMPORTANTE:
        - El sistema ya calculó que debes generar exactamente {num_preg_tip} preguntas.
        - No debes generar más ni menos.
        - No calcules tú la cantidad de preguntas.
        - Debes numerar las preguntas del 1 al {num_preg_tip}, sin repetir ni agregar extras.
        - Usa exclusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        - Asegúrate de incluir preguntas estrictamente técnicas, conceptuales y relacionadas con procesos clave del tema.
        - EN PREGUNTAS TIPO CHECKBOX SIEMPRE debe haber MÁS DE UNA RESPUESTA CORRECTA (mínimo 2).
        - NO PUEDES generar preguntas donde todas las opciones sean correctas.
        - NUNCA generes preguntas con solo UNA respuesta correcta (eso es multiple choice).

        **Restricciones adicionales:**
        1. Las preguntas tipo checkbox requieren MÚLTIPLES respuestas correctas.
        2. El enunciado debe ser claro: "¿Cuáles de las siguientes opciones..." o "Selecciona todas las opciones que..."
        3. Las opciones incorrectas deben ser técnicamente posibles, pero no la respuesta correcta en el contexto del fragmento.
        4. Incluye solo información relevante al tema sin preguntas triviales o de introducción.
        5. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
        6. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omítela.
        

        **Formato esperado:**
        - Texto de la pregunta que indique claramente que se pueden seleccionar múltiples opciones
        - 5 opciones (A, B, C, D, E)
        - SIEMPRE debe haber entre 2 y 4 respuestas correctas
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:"

        **Ejemplo de formato:**
        1. Pregunta: ¿Cuáles de las siguientes opciones son características de ITIL 4? (Selecciona todas las que correspondan)
            A) Se enfoca en la cadena de valor del servicio
            B) Elimina completamente los procesos de ITIL v3
            C) Incluye prácticas de gestión de servicios
            D) Se basa en principios rectores
            E) Solo aplica para empresas de tecnología
            
            Tipo: Checkboxes
            Respuesta correcta: [Letras]
            Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **IMPORTANTE**: NO generes preguntas como "Verdadero o Falso" - esas son preguntas diferentes. Las preguntas checkbox requieren selección múltiple.
        """
    else:
        instrucciones = f"""
        Genera **exactamente** {num_preg_tip} preguntas tipo checkboxes relacionadas con el tema '{topic}' del '{curso}'.
        Usa exclusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        Asegúrate de que sean preguntas técnicas, conceptuales y relevantes basadas en el fragmento proporcionado.

        **IMPORTANTE PARA CHECKBOXES:**
        - Las preguntas tipo checkbox SIEMPRE deben permitir MÚLTIPLES respuestas correctas (mínimo 2, máximo 4).
        - NUNCA generes preguntas con solo UNA respuesta correcta.
        - NO PUEDES generar preguntas donde todas las opciones sean correctas.
        - El enunciado debe indicar claramente que se pueden seleccionar múltiples opciones.

        **Restricciones adicionales:**
        1. Usa enunciados como: "¿Cuáles de las siguientes opciones...", "Selecciona todas las que correspondan", "Identifica las características que..."
        2. Debe haber siempre entre 2 y 4 respuestas correctas por pregunta.
        3. Las opciones incorrectas deben ser técnicamente posibles, pero no la respuesta correcta en el contexto del fragmento.
        4. Incluye solo información relevante al tema sin preguntas triviales o de introducción.
        5. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
        6. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omítela.
        7. Las preguntas deben cubrir tres niveles de dificultad: Básico, Intermedio y Avanzado.

        **Formato esperado:**
        - Texto con pregunta que indique selección múltiple y 5 opciones
        - Entre 2 y 4 respuestas correctas
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:"

        **Ejemplo de formato:**
        1. Pregunta: ¿Cuáles de las siguientes son prácticas de gestión incluidas en ITIL 4? (Selecciona todas las que correspondan)
            A) Gestión de incidentes.
            B) Desarrollo de aplicaciones web.
            C) Mejora continua.
            D) Gestión de problemas.
            E) Marketing digital.
            
            Tipo: Checkboxes
            Respuesta correcta: [Letras de opciones correctas]
            Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **RECUERDA**: Las preguntas checkbox son de selección múltiple, NO de verdadero/falso.

    Contenido:
    '{fragment}'
    """
    try:
        response = ask_openai(instrucciones)
        return response.strip() if response else "No se pudieron generar preguntas para checkboxes."
    except Exception as e:
        print(f"Error al generar preguntas para checkboxes: {e}")
        return "No se pudieron generar preguntas para checkboxes."


def generate_true_false_questions(topic: str, fragment: str, num_preg_tip: int, curso: str,formato) -> str:
    """
    Genera preguntas de verdadero/falso basadas en el fragmento.
    """
    if formato.lower()=='quizz':
        instrucciones=f"""
        Estás generando preguntas de checkboxes para un examen en formato **quizz** del curso '{curso}', tema '{topic}'.
        IMPORTANTE:
        - El sistema ya calculó que debes generar exactamente {num_preg_tip} preguntas.
        - No debes generar más ni menos.
        - No calcules tú la cantidad de preguntas.
        - Debes numerar las preguntas del 1 al {num_preg_tip}, sin repetir ni agregar extras.
        - Usa exlusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        - Asegúrate de incluir preguntas estrictamente técnicas, conceptuales y relacionadas con procesos clave del tema.

        **Restricciones adicionales:**
        1. Incluye solo información relevante al tema sin preguntas triviales o de introducción.
        2. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
        3. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omíte el fragmento y pregunta.
        4. Las preguntas deben cubrir tres niveles de dificultad: Básico, Intermedio y Avanzado.

        **Formato esperado:**
        - Texto de la afirmación con opciones Verdadero y Falso o True y False.
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:".

        **Ejemplo de formato:**
        1. Pregunta: [Texto de la afirmación]
            A) Verdadero
            B) Falso
            Tipo: True / False
            Respuesta correcta: [Letra]
            Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        """

    else:
        instrucciones = f"""
        Genera **exactamente** {num_preg_tip} preguntas de verdadero/falso relacionadas con el tema '{topic}' del '{curso}'.
        Usa exlusivamente los fragmentos de mayor valor técnico del **contenido** para generar las preguntas.
        Asegúrate de que las preguntas sean técnicas, conceptuales y relevantes.

        **Restricciones adicionales:**
            1. Incluye solo información relevante al tema sin preguntas triviales o de introducción.
            2. Cada pregunta DEBE incluir explícitamente el fragmento del texto en el que se basó como referencia, al final.
            3. No generes preguntas sin fragmento de referencia. Si no puedes generar una pregunta adecuada para un fragmento, omíte el fragmento y pregunta.
            4. Las preguntas deben cubrir tres niveles de dificultad: Básico, Intermedio y Avanzado.

        **Formato esperado:**
        - Texto de la afirmación con opciones Verdadero y Falso o True y False.
        - Incluye el fragmento relacionado con la pregunta al final, precedido por "Fragmento:".

        **Ejemplo de formato:**
        1. Pregunta: [Texto de la afirmación]
            A) Verdadero.
            B) Falso.
            Tipo: True / False
            Respuesta correcta: [Letra]
            Fragmento: [Texto relevante del contenido [Nombre del archivo]]

    Contenido:
    '{fragment}'
    """
    try:
        response = ask_openai(instrucciones)
        return response.strip() if response else "No se pudieron generar preguntas de verdadero/falso."
    except Exception as e:
        print(f"Error al generar preguntas de verdadero/falso: {e}")
        return "No se pudieron generar preguntas de verdadero/falso."

def generate_questions_by_topic(topic: str, fragment: str, num_preg_tip: dict, curso: str, formato: str = "Prueba") -> str:
    """
    Genera preguntas de diferentes tipos (multiple choice, checkboxes, verdadero/falso) llamando a funciones específicas.
    
    :param topic: Nombre del tema
    :param fragment: Fragmento de contenido
    :param num_preg_tip: Diccionario con cantidad de preguntas por tipo
    :param curso: Nombre del curso
    :param formato: Formato de las preguntas ("Prueba" o "Quizz") 
    :return: String con todas las preguntas generadas
    """
    
    # DEBUG: Verificar qué está recibiendo
    print(f"🔍 generate_questions_by_topic - Tema: {topic}")
    print(f"🔍 generate_questions_by_topic - Formato: {formato}")
    print(f"🔍 generate_questions_by_topic - num_preg_tip: {num_preg_tip}")
    

    if formato.lower() == "quizz" and 'relacion_col' in num_preg_tip:
        num_preg_tip = {
            'multiple_choice': num_preg_tip.get('multiple_choice', 0),
            'verdadero_falso': num_preg_tip.get('verdadero_falso', 0),
            'checkboxes': 0,  # No se usa en Quizz
            'matching': num_preg_tip.get('relacion_col', 0)
        }
    # Verificar que num_preg_tip tenga la estructura correcta
    required_keys = ['multiple_choice', 'checkboxes', 'verdadero_falso','matching']
    missing_keys = [key for key in required_keys if key not in num_preg_tip]
    if missing_keys:
        print(f"❌ ERROR: Faltan claves en num_preg_tip: {missing_keys}")
        return f"Error: Estructura incorrecta de num_preg_tip para {topic}"
    
    # Verificar que los valores sean números enteros positivos
    for key, value in num_preg_tip.items():
        if not isinstance(value, int) or value < 0:
            print(f"❌ ERROR: Valor inválido para {key}: {value}")
            return f"Error: Valor inválido en num_preg_tip[{key}] = {value}"
    
    def generate_multiple_choice():
        cantidad = num_preg_tip['multiple_choice']
        print(f"  → Generando {cantidad} preguntas de multiple choice")
        if cantidad > 0:
            return generate_multiple_choice_questions(topic, fragment, cantidad, curso,formato)
        return ""

    def generate_checkboxes():
        cantidad = num_preg_tip['checkboxes']
        print(f"  → Generando {cantidad} preguntas para checkboxes")
        if cantidad > 0:
            return generate_checkboxes_questions(topic, fragment, cantidad, curso,formato)
        return ""

    def generate_true_false():
        cantidad = num_preg_tip['verdadero_falso']
        print(f"  → Generando {cantidad} preguntas de verdadero/falso")
        if cantidad > 0:
            return generate_true_false_questions(topic, fragment, cantidad, curso,formato)
        return ""

    def generate_matching():
        cantidad=num_preg_tip['matching']
        print(f"  → Generando {cantidad} preguntas para matching")
        if cantidad > 0:
            return generate_matching_questions(topic,fragment,cantidad,curso,formato)
        return ""


    tasks = {}
    
    # Solo agregar tareas que tengan preguntas > 0
    if num_preg_tip['multiple_choice'] > 0:
        tasks["Preguntas de multiple choice"] = generate_multiple_choice
    if num_preg_tip['checkboxes'] > 0:
        tasks["Preguntas para checkboxes"] = generate_checkboxes
    if num_preg_tip['verdadero_falso'] > 0:
        tasks["Preguntas de verdadero/falso"] = generate_true_false
    if num_preg_tip['matching'] > 0:
        tasks["Preguntas de matching"]=generate_matching

    if not tasks:
        print(f"⚠️ WARNING: No hay preguntas que generar para {topic}")
        return f"### {topic}: No se asignaron preguntas para generar."

    results = []

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_task = {executor.submit(task): name for name, task in tasks.items()}
        for future in as_completed(future_to_task):
            task_name = future_to_task[future]
            try:
                result = future.result()
                if result and result.strip():  # Solo agregar si hay contenido
                    results.append(f"### {task_name}:\n" + result)
                    print(f"✅ {task_name} generadas correctamente para {topic}")
                else:
                    print(f"⚠️ {task_name} devolvió resultado vacío para {topic}")
            except Exception as e:
                print(f"❌ Error al generar {task_name} para {topic}: {e}")
                results.append(f"### {task_name}: No se pudieron generar preguntas para este tipo.")

    final_result = "\n\n".join(results) if results else f"No se pudieron generar preguntas para {topic}"
    
    # DEBUG: Contar preguntas en el resultado final
    question_markers = ["PREGUNTA", "Pregunta", "pregunta"]
    question_count = sum(final_result.count(marker) for marker in question_markers)
    expected_total = sum(num_preg_tip.values())
    
    print(f"🔍 {topic} - Preguntas esperadas: {expected_total}, encontradas en resultado: {question_count}")
    
    return final_result

def validate_and_rate_questions(questions: str, topic: str, curso: str, formato: str = "Prueba") -> str:
    """
    Valida y califica las preguntas generadas utilizando OpenAI.
    Devuelve un string con las preguntas validadas en el formato correcto.
    """
    
    if formato.lower() == 'quizz':
        prompt = f"""
        Analiza las siguientes preguntas del tema '{topic}' del curso '{curso}' en formato QUIZZ.
        
        IMPORTANTE: Devuelve EXACTAMENTE las preguntas tal como están, solo mejorando el formato pero manteniendo TODO el contenido original.
        
        Formato de salida requerido:
        **multiple choice**
        1. Pregunta: [Texto de la pregunta]
           A) Opción 1
           B) Opción 2
           C) Opción 3
           D) Opción 4
           E) Opción 5
           
           Tipo: Multiple Choice
           Respuesta correcta: [Letra]
           Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **relación de columnas**
        1. Pregunta: [Texto de la pregunta]
           A) Opción 1 
           B) Opción 2
           C) Opción 3
           D) Opción 4 
           E) Opción 5 (opcional) 
           
           Tipo: Relación de columnas
           Respuesta correcta: Descripción que corresponde a A); Descripción que corresponde a B); Descripción que corresponde a C); Descripción que corresponde a D); Descripción que corresponde a E
           Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **falso/verdadero**
        1. Pregunta: [Texto de la pregunta]
           A) Verdadero
           B) Falso
           
           Tipo: True / False
           Respuesta correcta: [Letra]
           Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        

        Preguntas a procesar:
        {questions}
        """
    else:  # formato == "Prueba"
        prompt = f"""
        Analiza y valida las siguientes preguntas del tema '{topic}' del curso '{curso}'.
        
        IMPORTANTE: Para cada pregunta debes:
        1. Mantener el contenido original completo
        2. Agregar la validación y calificación solicitada
        3. Corregir errores gramaticales si los hay
        4. Asegurar que TODA la información esté presente
        
        Formato de salida OBLIGATORIO:
        **multiple choice**
        1. Pregunta: [Texto de la pregunta]
           A) Opción 1.
           B) Opción 2.
           C) Opción 3.
           D) Opción 4.
           E) Opción 5.
           
           Tipo: Multiple Choice
           Respuesta correcta: [Letra de la opción correcta]
           Relevante: [Sí/No]
           Calidad: [Calificación de 0 a 5]
           Clasificada correctamente: [Sí/No]
           Respuesta correcta válida: [Sí/No]
           Complejidad: [Básico/Intermedio/Avanzado]
           Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **checkboxes**
        1. Pregunta: [Texto de la pregunta]
           A) Opción 1.
           B) Opción 2.
           C) Opción 3.
           D) Opción 4.
           E) Opción 5.
           
           Tipo: Checkboxes
           Respuesta correcta: [Letras de opciones correctas]
           Relevante: [Sí/No]
           Calidad: [Calificación de 0 a 5]
           Clasificada correctamente: [Sí/No]
           Respuesta correcta válida: [Sí/No]
           Complejidad: [Básico/Intermedio/Avanzado]
           Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **falso/verdadero**
        1. Pregunta: [Texto de la pregunta.]
           A) Verdadero.
           B) Falso.
           
           Tipo: True / False
           Respuesta correcta: [Letra de la opción correcta]
           Relevante: [Sí/No]
           Calidad: [Calificación de 0 a 5]
           Clasificada correctamente: [Sí/No]
           Respuesta correcta válida: [Sí/No]
           Complejidad: [Básico/Intermedio/Avanzado]
           Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        **matching**
        1. Pregunta: [Texto de la pregunta]
           A) Opción 1.
           B) Opción 2.
           C) Opción 3.
           D) Opción 4.
           E) Opción 5. (opcional)
           
           Tipo: Matching
           Respuesta correcta: Descripción que corresponde a A.; Descripción que corresponde a B.; Descripción que corresponde a C.; Descripción que corresponde a D.; Descripción que corresponde a E.
           Fragmento: [Texto relevante del contenido [Nombre del archivo]]

        REGLAS IMPORTANTES:
        - NO elimines ninguna pregunta
        - NO modifiques el contenido de las preguntas, solo el formato
        - SIEMPRE incluye la respuesta correcta, fragmento y todas las validaciones
        - Si una pregunta no tiene fragmento, marca como "Fragmento: [No disponible]"
        - Al final de cada descripción de preguntas tipo 'matching' añade un punto final.
        - Corrige errores gramaticales en preguntas tipo "completar frase"

        Preguntas a procesar:
        {questions}
        """
    
    try:
        response = ask_openai(prompt)
        if response:
            return response.strip()
        else:
            print("No se pudieron validar las preguntas.")
            return questions  # Devolver las preguntas originales si falla la validación
    except Exception as e:
        print(f"Error al validar preguntas: {e}")
        return questions  # Devolver las preguntas originales si hay error
     
def process_topic(topic, fragment, num_preg_tip, curso, formato="Prueba"):
    """
    Procesa un tema para generar y validar preguntas.
    """
    try:
        if not fragment.strip():
            return topic, "No se pudo procesar porque el fragmento está vacío."

        # DEBUG: Mostrar qué está recibiendo la función
        print(f"🔍 DEBUG - Tema: {topic}")
        print(f"🔍 DEBUG - Formato: {formato}")
        print(f"🔍 DEBUG - num_preg_tip recibido: {num_preg_tip}")

        # 🔁 Normalizar claves si el formato es Quizz
        if formato.lower() == "quizz" and 'relacion_col' in num_preg_tip:
            num_preg_tip = {
                'multiple_choice': num_preg_tip.get('multiple_choice', 0),
                'verdadero_falso': num_preg_tip.get('verdadero_falso', 0),
                'checkboxes': 0,  # No se usa en Quizz, se puede dejar en cero
                'matching': num_preg_tip.get('relacion_col', 0)
            }

        # Validar estructura de num_preg_tip
        expected_keys = ['multiple_choice', 'checkboxes', 'verdadero_falso', 'matching']
        if not all(key in num_preg_tip for key in expected_keys):
            return topic, f"Error: num_preg_tip debe contener {expected_keys}"

        # Calcular total de preguntas para este tema
        total_preguntas = sum(num_preg_tip.values())
        #print(f"🔍 DEBUG - Total preguntas esperadas para {topic}: {total_preguntas}")

        # GARANTIZAR MÍNIMO DE 3 PREGUNTAS
        if total_preguntas < 3:
            print(f"⚠️ WARNING - {topic} tiene {total_preguntas} preguntas, ajustando a mínimo 3")
            num_preg_tip = {
                'multiple_choice': max(1, num_preg_tip.get('multiple_choice', 1)),
                'verdadero_falso': max(1, num_preg_tip.get('verdadero_falso', 1)),
                'checkboxes': max(1, num_preg_tip.get('checkboxes', 1)),
                'matching': max(1, num_preg_tip.get('matching', 1))
            }
            total_preguntas = sum(num_preg_tip.values())
            #print(f"🔄 DEBUG - Ajustado a: {num_preg_tip}, Total={total_preguntas}")

        # Generar preguntas
        print(f"🔄 DEBUG - Generando preguntas...")
        questions = generate_questions_by_topic(topic, fragment, num_preg_tip, curso, formato)

        if not questions or not questions.strip():
            return topic, "No se pudieron generar preguntas."

        print(f"🔄 DEBUG - Preguntas generadas, validando...")

        # Validar preguntas generadas - PASAR EL FORMATO
        validated_questions = validate_and_rate_questions(questions, topic, curso, formato)

        if validated_questions and validated_questions.strip():
            print(f"✅ DEBUG - Preguntas validadas correctamente para {topic}")
            output = f"Tema: {topic}\n{validated_questions}\n" + "-" * 50 + "\n"
            return topic, output
        else:
            print(f"⚠️ WARNING - Validación falló, usando preguntas originales")
            output = f"Tema: {topic}\n{questions}\n" + "-" * 50 + "\n"
            return topic, output

    except Exception as e:
        print(f"❌ ERROR en process_topic para {topic}: {e}")
        import traceback
        print(f"❌ TRACEBACK: {traceback.format_exc()}")
        return topic, f"Error procesando el tema {topic}: {e}"

# FUNCIÓN AUXILIAR PARA DEBUG
def debug_question_generation(topic, fragment, num_preg_tip, curso, formato="Prueba"):
    """
    Función para debuggear la generación de preguntas paso a paso
    """
    print(f"\n{'='*60}")
    print(f"🔍 DEBUG COMPLETO - {topic}")
    print(f"{'='*60}")
    
    print(f"📋 Parámetros:")
    print(f"  - Tema: {topic}")
    print(f"  - Formato: {formato}")
    print(f"  - Curso: {curso}")
    print(f"  - Distribución: {num_preg_tip}")
    print(f"  - Fragmento: {len(fragment)} caracteres")
    
    print(f"\n🎯 PASO 1: Generando preguntas...")
    questions = generate_questions_by_topic(topic, fragment, num_preg_tip, curso, formato)
    
    print(f"📝 Preguntas generadas ({len(questions)} caracteres):")
    print(f"{'='*40}")
    print(questions[:500] + "..." if len(questions) > 500 else questions)
    print(f"{'='*40}")
    
    print(f"\n🎯 PASO 2: Validando preguntas...")
    validated = validate_and_rate_questions(questions, topic, curso, formato)
    
    print(f"✅ Preguntas validadas ({len(validated)} caracteres):")
    print(f"{'='*40}")
    print(validated[:500] + "..." if len(validated) > 500 else validated)
    print(f"{'='*40}")
    
    print(f"\n{'='*60}")
    print(f"🔍 FIN DEBUG - {topic}")
    print(f"{'='*60}\n")
    
    return validated

# SOLUCIÓN TEMPORAL: Wrapper para controlar la cantidad
def controlled_generate_questions_by_topic(topic, fragment, num_preg_tip, curso, formato="Prueba"):
    """
    Wrapper que controla la cantidad de preguntas generadas
    """
    if formato.lower() == 'quizz':
        # Para Quizz, verificar que no exceda los límites
        total_esperado = sum(num_preg_tip.values())
        if total_esperado > 5:
            # Ajustar proporcionalmente
            factor = 5 / total_esperado
            adjusted_num_preg_tip = {
                'multiple_choice': max(1, int(num_preg_tip['multiple_choice'] * factor)),
                'verdadero_falso': max(1, int(num_preg_tip['verdadero_falso'] * factor)),
                'checkboxes': max(1, int(num_preg_tip['checkboxes'] * factor))
            }
            
            # Asegurar que el total sea exactamente 5
            total_adjusted = sum(adjusted_num_preg_tip.values())
            if total_adjusted < 5:
                adjusted_num_preg_tip['multiple_choice'] += (5 - total_adjusted)
            
            print(f"⚠️ Ajustando {topic}: {num_preg_tip} → {adjusted_num_preg_tip}")
            num_preg_tip = adjusted_num_preg_tip
    
    return generate_questions_by_topic(topic, fragment, num_preg_tip, curso, formato)