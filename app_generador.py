import streamlit as st
import pandas as pd
from utils import (extract_text_from_pdf_bdd, export_txt_to_excel, generate_unique_filename,calcular_preguntas_por_tipo, get_file_content, log_message, global_exception_handler, check_dependencies, resource_path, translate_with_mymemory, export_txt_to_excel_quizz)
from openai_helper import (segment_content_with_openai, extract_modules_and_chapters_with_openai, process_topic)
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import traceback
import streamlit as st
import utils
import openai_helper


# Inicializar estados en session_state
if "content" not in st.session_state:
    st.session_state.content = ""
if "chapters" not in st.session_state:
    st.session_state.chapters = []
if "subchapters" not in st.session_state:
    st.session_state.subchapters = []
if "txt_filename" not in st.session_state:
    st.session_state.txt_filename = ""
if "excel_filename" not in st.session_state:
    st.session_state.excel_filename = ""
if "process_completed" not in st.session_state:
    st.session_state.process_completed = False
if "uploaded_files" not in st.session_state:
    st.session_state.uploaded_files = []
if "clave" not in st.session_state:
    st.session_state.clave = ""
if "cursocaps" not in st.session_state:
    st.session_state.cursocaps = []
if "curso" not in st.session_state:
    st.session_state.curso = ""
if "idioma" not in st.session_state:
    st.session_state.idioma = ""
if "formato" not in st.session_state:
    st.session_state.formato=""
if "files_loaded" not in st.session_state:
    st.session_state.files_loaded = False
if "step_completed" not in st.session_state:
    st.session_state.step_completed = 0  # Paso completado (0=ninguno, 1=clave/archivo, 2=adicionales, 3=formato, 4=idioma)
if "start_time" not in st.session_state:
    st.session_state.start_time = None  # Tiempo inicial para medir duración

USERS_PERMITIDOS = {
    "gabriela.garcia@netec.com.mx": "Netec.IA2025",
    "gustavo.olarte@netec.com.co": "Netec.IA2025",
    "montserrat.beltran@netec.com.mx": "Netec.IA2025",
    "brenda.domarco@netec.com": "Netec.IA2025",
    "angie.rueda@netec.com.mx": "Netec.IA2025"
}

def login():
    st.title("🔐 Inicio de sesión")
    username = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")
    login_btn = st.button("Iniciar sesión")

    if login_btn:
        if username in USERS_PERMITIDOS and USERS_PERMITIDOS[username] == password:
            st.session_state.usuario_autenticado = True
            st.experimental_rerun()
        else:
            st.error("❌ Usuario o contraseña incorrectos.")


# Función principal para la aplicación Streamlit
def main():
    if "usuario_autenticado" not in st.session_state or not st.session_state.usuario_autenticado:
        login()
        return
    
    check_dependencies()
    sys.excepthook = global_exception_handler
    st.set_page_config(page_title="Generador de Preguntas",page_icon='icono.png' ,layout="wide")

    # Título
    st.title("📝 Generador de Preguntas")

    # Crear carpeta para salida
    current_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(current_dir, "Archivos")
    os.makedirs(output_dir, exist_ok=True)

    # Paso 1: Ingresar clave o cargar archivo
    if st.session_state.step_completed < 1:
        st.header("Paso 1: Ingresar clave o cargar archivo")
        
        # Opción 1: Ingresar clave
        with st.form(key="clave_form"):
            st.subheader("Opción A: Ingresar clave del curso")
            clave_curso = st.text_input("Ingresa la clave del curso:")
            submit_clave = st.form_submit_button("Verificar clave")
        
        # Opción 2: Cargar archivo
        with st.form(key="file_form"):
            st.subheader("Opción B: Cargar archivo local")
            uploaded_files = st.file_uploader(
                "Carga un archivo local (PDF, Excel, Word, PowerPoint, TXT):",
                type=["pdf", "xlsx", "pptx", "docx", "txt"], accept_multiple_files=False
            )
            submit_file = st.form_submit_button("Procesar temario")
        
        # Procesar clave
        if submit_clave and clave_curso:
            st.session_state.start_time = time.time()  # Registrar el tiempo de inicio
            log_message(f"Iniciado proceso para la clave del curso: {clave_curso}")
            st.session_state.process_completed = False
            st.session_state.chapters = []
            st.session_state.clave = clave_curso  # Guardar la clave ingresada
            
            with st.spinner("Buscando temario en la base de datos..."):
                st.session_state.content = extract_text_from_pdf_bdd(clave_curso)

            if st.session_state.content:
                st.success(f"✅ Temario encontrado para la clave: **{clave_curso}**")
                log_message(f"Temario encontrado para la clave: {clave_curso}")
                
                with st.spinner("Extrayendo capítulos del temario..."):
                    cursocaps = extract_modules_and_chapters_with_openai(st.session_state.content)
                    if cursocaps:
                        st.session_state.curso = cursocaps[0]  # Guardar el nombre del curso en session_state
                        st.session_state.chapters = cursocaps[1:]
                        st.success("✅ Capítulos extraídos correctamente del temario.")
                        st.session_state.step_completed = 1  # Avanzar al siguiente paso
                    else:
                        st.error("❌ No se pudieron extraer capítulos del temario. Verifica el contenido.")
            else:
                st.warning(f"❌ No se encontró temario para la clave: **{clave_curso}**.")
                log_message(f"No se encontró temario para la clave: {clave_curso}")
                st.info("Por favor, carga un archivo local para usar como temario inicial.")
        
        # Procesar archivo
        if submit_file and uploaded_files:
            st.session_state.start_time = time.time()  # Registrar el tiempo de inicio
            log_message(f"Iniciado proceso para archivo local")
            
            with st.spinner("Procesando temario..."):
                st.session_state.temario_filename = uploaded_files.name
                st.session_state.content = get_file_content([uploaded_files])
                
                if st.session_state.content:
                    st.success("✅ Temario cargado correctamente.")
                    
                    with st.spinner("Extrayendo capítulos del temario..."):
                        cursocaps = extract_modules_and_chapters_with_openai(st.session_state.content)
                        if cursocaps:
                            st.session_state.curso = cursocaps[0]  # Guardar el nombre del curso
                            st.session_state.chapters = cursocaps[1:]
                            st.success("✅ Capítulos extraídos correctamente.")
                            st.session_state.step_completed = 1  # Avanzar al siguiente paso
                        else:
                            st.error("❌ No se pudieron extraer capítulos del temario.")
                else:
                    st.error("❌ No se pudo procesar el archivo cargado como temario.")
    
    # Mostrar vista previa del temario si hay contenido
    if st.session_state.content and st.session_state.step_completed >= 1:
        st.header("Temario del curso")
        st.subheader(f"🔍 Vista Previa del Temario: {st.session_state.curso}")
        st.text_area("", st.session_state.content, height=200)
        st.subheader("📚 Capítulos extraídos:")
        st.write(st.session_state.chapters)
    
    # Paso 2: Cargar archivos adicionales
    if st.session_state.step_completed == 1:
        st.header("Paso 2: Cargar archivos adicionales")
        
        with st.form(key="additional_files_form"):
            uploaded_files = st.file_uploader(
                "Carga archivos locales (PDF, Excel, Word, PowerPoint, TXT):",
                type=["pdf", "xlsx", "pptx", "docx", "txt"], accept_multiple_files=True
            )
            urls = st.text_area("Ingresa enlaces de archivos remotos (separados por coma):")
            submit_additional_files = st.form_submit_button("Procesar archivos adicionales")
        
        if submit_additional_files:
            with st.spinner("Procesando archivos adicionales..."):
                extra_content = get_file_content(uploaded_files, urls.split(",") if urls else [])
                
                if extra_content:
                    st.session_state.content += "\n" + extra_content
                    st.session_state.files_loaded = True

                    # Guardar el contenido en un archivo .txt
                    output_dir = os.path.join(os.getcwd(), "Archivos")  # Directorio para guardar el archivo
                    os.makedirs(output_dir, exist_ok=True)
                    
                    identifier = st.session_state.clave if st.session_state.clave else "sin_clave"
                    #lan_indentifier=st.session_state.idioma if st.session_state.idioma else "sin_idioma"
                    content_file_path = generate_unique_filename(os.path.join(output_dir, f"contenido_completo_{identifier}"), ".txt")
                    
                    
                    try:
                        with open(content_file_path, "w", encoding="utf-8") as file:
                            file.write(st.session_state.content)
                        st.success("✅ Archivos adicionales procesados correctamente.")
                        log_message(f"Archivos adicionales procesados correctamente")
                        st.info(f"Contenido guardado en: {content_file_path}")
                        st.session_state.step_completed = 2  # Avanzar al siguiente paso
                    except Exception as e:
                        st.error(f"❌ Error al guardar el contenido en archivo: {e}")
                else:
                    st.warning("No se procesaron archivos adicionales. Puedes continuar sin ellos.")
                    st.session_state.step_completed = 2  # Avanzar al siguiente paso incluso sin archivos adicionales

    #Paso 3: Formato
    if st.session_state.step_completed==2:
        st.header("Paso 3: Elegir formato para las preguntas")
        with st.form(key="format_form"):
            formato = st.radio(
                label="¿En qué formato quieres las preguntas?",
                options=["Prueba", "Quizz"]
            )
            submit_format = st.form_submit_button("Aceptar")
        
        if submit_format:
            st.session_state.formato = formato
            st.success(f"✅ Formato seleccionado: {formato}")
            st.session_state.step_completed = 3  # Avanzar al siguiente paso

    
    # Paso 4: Elegir idioma
    # if st.session_state.step_completed == 3:
    #     st.header("Paso 4: Elegir idioma para las preguntas")
        
    #     with st.form(key="language_form"):
    #         language = st.radio(
    #             label="¿En qué idioma quieres generar las preguntas?",
    #             options=["Español", "Inglés"]
    #         )
    #         submit_language = st.form_submit_button("Aceptar")
        
    #     if submit_language:
    #         st.session_state.idioma = language
    #         st.success(f"✅ Idioma seleccionado: {language}")
    #         st.session_state.step_completed = 4  # Avanzar al siguiente paso
        
    # Paso 4: Generar preguntas
    if st.session_state.step_completed == 3:
        st.header("Paso 4: Generar preguntas")
        
        if st.button("Iniciar generación de preguntas"):           
            
            # Clasificar contenido por módulos
            with st.spinner("Clasificando Contenido..."):
                # Determinar el identificador para los archivos de salida
                if st.session_state.clave:
                    identifier = st.session_state.clave
                elif "temario_filename" in st.session_state:
                    identifier = os.path.splitext(st.session_state.temario_filename)[0]
                else:
                    identifier = "sin_identificar"

                # Directorio de salida
                output_dir = os.path.join(current_dir, "Archivos")
                os.makedirs(output_dir, exist_ok=True)

                # Generar y guardar segmentos
                segments = segment_content_with_openai(
                    st.session_state.content, 
                    st.session_state.chapters, 
                    output_dir, 
                    identifier, 
                    st.session_state.curso,
                    st.session_state.subchapters
                )
                
                st.session_state.segments = segments
                
                # Mostrar segmentos
                st.subheader("**Segmentos extraídos:**")
                for idx, (topic, fragment) in enumerate(st.session_state.segments.items()):
                    unique_key = f"segment_{idx}_{topic}"
                    st.text_area(f"{topic}", fragment, height=200, key=unique_key)
            
            # Generar preguntas
            st.subheader("🛠 Generación y Validación de Preguntas")
            with st.spinner("Generación de Preguntas..."):
                num_chapters = len(st.session_state.chapters)
                num_preg_tip = calcular_preguntas_por_tipo(num_chapters, st.session_state.formato)
                #DEBUGG
                # st.write("🔍 DEBUG - Resultado de calcular_preguntas_por_tipo:")
                # st.write(num_preg_tip)
                # st.write(f"🔍 DEBUG - Formato: {st.session_state.formato}")
                # st.write(f"🔍 DEBUG - Número de capítulos: {num_chapters}")
                questions_output = ""
                max_workers = 1
                
                # Debug: Verificar que tenemos segmentos
                st.info(f"Procesando {len(st.session_state.segments)} segmentos...")

                # Procesamiento concurrente - CORREGIDO
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    from collections import defaultdict

                    future_to_topic = {}
                    capitulos = list(st.session_state.segments.items())
                    num_capitulos = len(capitulos)

                    # Configurar distribución según formato
                    if st.session_state.formato.lower() == 'quizz':
                        # Para formato Quizz - usar distribución directa
                        dist_opm = num_preg_tip['multiple_choice']
                        dist_vf = num_preg_tip['verdadero_falso']
                        dist_rc = num_preg_tip['relacion_col']

                        for i, (topic, fragment) in enumerate(capitulos):
                            preguntas_cap_dict = {
                                'multiple_choice': dist_opm[i],
                                'verdadero_falso': dist_vf[i],
                                'relacion_col':dist_rc[i]
                            }
                            # DEBUG: Verificar qué se está enviando a cada tema
                            st.write(f"📝 DEBUG - Enviando para {topic}:")
                            st.write(f"   Multiple Choice: {preguntas_cap_dict['multiple_choice']}")
                            st.write(f"   Verdadero/Falso: {preguntas_cap_dict['verdadero_falso']}")
                            st.write(f"   Relción de columnas: {preguntas_cap_dict['relacion_col']}")
                            st.write(f"   Total: {sum(preguntas_cap_dict.values())}")

                            future = executor.submit(
                                process_topic,
                                topic,
                                fragment,
                                preguntas_cap_dict,
                                st.session_state.curso,
                                st.session_state.formato
                            )
                            future_to_topic[future] = topic

                    else:  # Para formato Prueba
                        # Función para distribuir preguntas respetando restricciones (3-5 por módulo)
                        def distribuir_preguntas_por_capitulo(total_preguntas, num_chapters, min_por_cap=5, max_por_cap=None):
                            import random
                            if num_chapters == 0:
                                return []
                            
                            # Inicializar con el mínimo por capítulo
                            distribucion = [min_por_cap] * num_chapters
                            preguntas_asignadas = sum(distribucion)
                            
                            # Distribuir las preguntas restantes
                            preguntas_restantes = total_preguntas - preguntas_asignadas
                            
                            # Distribuir las preguntas extra aleatoriamente respetando el máximo
                            i = 0
                            while preguntas_restantes > 0:
                                if max_por_cap is None or distribucion[i] < max_por_cap:
                                    distribucion[i] += 1
                                    preguntas_restantes -= 1
                                i = (i + 1) % num_chapters                          
                            
                            return distribucion
                        
                        # Función para distribuir tipos de preguntas manteniendo proporciones globales
                        def distribuir_tipos_globalmente(total_om, total_vf, total_check, total_ma, preguntas_por_capitulo):
                            """
                            Distribuye los tipos de preguntas entre capítulos manteniendo los totales globales
                            """
                            num_caps = len(preguntas_por_capitulo)
                            if num_caps == 0:
                                return {'multiple_choice': [], 'verdadero_falso': [], 'checkboxes': [], 'matching': []}
                            
                            # Inicializar distribuciones
                            dist_om = [0] * num_caps
                            dist_vf = [0] * num_caps
                            dist_check = [0] * num_caps
                            dist_match= [0] * num_caps
                            

                            #Distribuir preguntas de matching
                            ma_restantes=total_ma
                            for i in range(num_caps):
                                if i==num_caps-1: #último capítulo se lleva el resto
                                    dist_match[i]=ma_restantes
                                else:
                                    #calcular proporción basada en preguntas del capítulo
                                    proporcion=preguntas_por_capitulo[i] /sum(preguntas_por_capitulo)
                                    asignadas=min(ma_restantes, max(1, round(total_ma*proporcion)))
                                    dist_match[i]=asignadas
                                    ma_restantes -= asignadas

                            # Distribuir preguntas de opción múltiple
                            om_restantes = total_om
                            for i in range(num_caps):
                                if i == num_caps - 1:  # Último capítulo se lleva el resto
                                    dist_om[i] = om_restantes
                                else:
                                    # Calcular proporción basada en preguntas del capítulo
                                    proporcion = preguntas_por_capitulo[i] / sum(preguntas_por_capitulo)
                                    asignadas = min(om_restantes, max(1, round(total_om * proporcion)))
                                    dist_om[i] = asignadas
                                    om_restantes -= asignadas
                            
                            # Distribuir preguntas verdadero/falso
                            vf_restantes = total_vf
                            for i in range(num_caps):
                                if i == num_caps - 1:  # Último capítulo se lleva el resto
                                    dist_vf[i] = vf_restantes
                                else:
                                    proporcion = preguntas_por_capitulo[i] / sum(preguntas_por_capitulo)
                                    asignadas = min(vf_restantes, max(1, round(total_vf * proporcion)))
                                    dist_vf[i] = asignadas
                                    vf_restantes -= asignadas
                            
                            # Distribuir preguntas checkboxes
                            check_restantes = total_check
                            for i in range(num_caps):
                                if i == num_caps - 1:  # Último capítulo se lleva el resto
                                    dist_check[i] = check_restantes
                                else:
                                    proporcion = preguntas_por_capitulo[i] / sum(preguntas_por_capitulo)
                                    asignadas = min(check_restantes, max(1, round(total_check * proporcion)))
                                    dist_check[i] = asignadas
                                    check_restantes -= asignadas
                            
                            # Verificar y ajustar si es necesario para que cada capítulo tenga el total correcto
                            for i in range(num_caps):
                                total_cap = dist_om[i] + dist_vf[i] + dist_check[i] + dist_match[i]
                                diferencia = preguntas_por_capitulo[i] - total_cap
                                
                                if diferencia != 0:
                                    # Ajustar en el tipo que tenga más preguntas
                                    if dist_om[i] >= dist_vf[i] and dist_om[i] >= dist_check[i] and dist_om[i] >= dist_match[i]:
                                        dist_om[i] += diferencia
                                    elif dist_vf[i] >= dist_check[i]:
                                        dist_vf[i] += diferencia
                                    elif dist_match[i]>=dist_om[i]:
                                        dist_match[i] += diferencia
                                    else:
                                        dist_check[i] += diferencia
                                    
                                    # Asegurar que no hay números negativos
                                    dist_om[i] = max(0, dist_om[i])
                                    dist_vf[i] = max(0, dist_vf[i])
                                    dist_check[i] = max(0, dist_check[i])
                                    dist_match[i]= max (0,dist_match[i])
                            
                            return {
                                'multiple_choice': dist_om,
                                'verdadero_falso': dist_vf,
                                'checkboxes': dist_check,
                                'matching': dist_match
                            }
                        
                        # Obtener totales globales
                        total_preguntas = num_preg_tip['total']
                        total_om = num_preg_tip['multiple_choice']
                        total_vf = num_preg_tip['verdadero_falso']
                        total_check = num_preg_tip['checkboxes']
                        total_match= num_preg_tip['matching']
                        
                        # Distribuir preguntas por capítulo (respetando 3-5 por capítulo)
                        preguntas_por_capitulo = distribuir_preguntas_por_capitulo(total_preguntas, num_capitulos)
                        
                        # Distribuir tipos manteniendo totales globales
                        dist_por_tipo = distribuir_tipos_globalmente(total_om, total_vf, total_check,total_match, preguntas_por_capitulo)
                        
                        # Debug: Mostrar distribución detallada
                        # st.write(f"🔍 DEBUG - Distribución por capítulo: {preguntas_por_capitulo}")
                        # st.write(f"🔍 DEBUG - Total distribuido: {sum(preguntas_por_capitulo)}")
                        # st.write(f"🔍 DEBUG - OM por capítulo: {dist_por_tipo['multiple_choice']}")
                        # st.write(f"🔍 DEBUG - VF por capítulo: {dist_por_tipo['verdadero_falso']}")
                        # st.write(f"🔍 DEBUG - Check por capítulo: {dist_por_tipo['checkboxes']}")
                        # st.write(f"🔍 DEBUG - Match por capítulo: {dist_por_tipo['matching']}")
                        # st.write(f"🔍 DEBUG - Total OM: {sum(dist_por_tipo['multiple_choice'])} (objetivo: {total_om})")
                        # st.write(f"🔍 DEBUG - Total VF: {sum(dist_por_tipo['verdadero_falso'])} (objetivo: {total_vf})")
                        # st.write(f"🔍 DEBUG - Total Check: {sum(dist_por_tipo['checkboxes'])} (objetivo: {total_check})")
                        # st.write(f"🔍 DEBUG - Total Match: {sum(dist_por_tipo['matching'])} (objetivo: {total_match})")
                        
                        # Verificar que los totales coincidan
                        for i in range(num_capitulos):
                            total_cap = (dist_por_tipo['multiple_choice'][i] + 
                                        dist_por_tipo['verdadero_falso'][i] + 
                                        dist_por_tipo['checkboxes'][i]+
                                        dist_por_tipo['matching'][i])
                            #st.write(f"🔍 DEBUG - Capítulo {i+1}: {total_cap} preguntas (objetivo: {preguntas_por_capitulo[i]})")

                        # Enviar a OpenAI para formato Prueba
                        for i, (topic, fragment) in enumerate(capitulos):
                            preguntas_cap_dict = {
                                'multiple_choice': dist_por_tipo['multiple_choice'][i],
                                'verdadero_falso': dist_por_tipo['verdadero_falso'][i],
                                'checkboxes': dist_por_tipo['checkboxes'][i],
                                'matching': dist_por_tipo['matching'][i]
                            }

                            future = executor.submit(
                                process_topic,
                                topic,
                                fragment,
                                preguntas_cap_dict,
                                st.session_state.curso,
                                st.session_state.formato
                            )
                            future_to_topic[future] = topic

                    # Procesar resultados de ambos formatos
                    for future in as_completed(future_to_topic):
                        original_topic = future_to_topic[future]
                        try:
                            result_topic, result_content = future.result()
                            
                            # Debug: Contar preguntas en el resultado
                            if result_content:
                                lines = str(result_content).split('\n')
                                question_count = len([line for line in lines if re.match(r'^\d+\.', line.strip())])
                            #     st.write(f"🔍 DEBUG - {original_topic}: {question_count} preguntas generadas")
                            
                            # st.write(f"🔍 Procesando resultado para: {original_topic}")
                            # st.write(f"📝 Contenido recibido: {len(result_content) if result_content else 0} caracteres")
                            
                            # Verificar si hay errores
                            if not result_content or "Error" in str(result_content) or "No se pudieron" in str(result_content):
                                st.warning(f"⚠️ Problema con {original_topic}: {result_content}")
                                log_message(f"⚠️ Error al generar preguntas para el tema: {original_topic}")
                            else:
                                # Agregar contenido válido
                                questions_output += str(result_content) + "\n\n"
                                #st.success(f"✅ Preguntas generadas para **{original_topic}**")
                                log_message(f"✅ Preguntas generadas correctamente para el tema: {original_topic}")
                                
                                # Debug: Mostrar progreso
                                #current_length = len(questions_output)
                                #st.write(f"📊 Total acumulado: {current_length} caracteres")
                                
                        except Exception as e:
                            st.error(f"❌ Error procesando el tema {original_topic}: {str(e)}")
                            log_message(f"❌ Error procesando el tema {original_topic}: {str(e)}")
                            st.code(traceback.format_exc())
            
            # Debug: Verificar contenido final antes de guardar
            st.write(f"📋 Total de preguntas generadas: {len(questions_output)} caracteres")
            
            if not questions_output.strip():
                st.error("❌ No se generaron preguntas. Revisa los logs para más detalles.")
                st.info("🔍 Posibles causas:")
                st.write("- Error en la función process_topic")
                st.write("- Problemas con la API de OpenAI")
                st.write("- Contenido de segmentos insuficiente")
                return
            
            # GUARDAR ARCHIVO TXT - SOLO UNA VEZ
            try:
                #lan_identifier = st.session_state.idioma if st.session_state.idioma else "sin_idioma"
                st.session_state.txt_filename = generate_unique_filename(
                    os.path.join(output_dir, f"preguntas_generadas_{identifier}"), 
                    ".txt"
                )
                
                # Preparar contenido final
                final_content = questions_output
                
                # Traducir si es necesario
                if st.session_state.idioma == "Inglés":
                    st.info("🌐 Traduciendo todas las preguntas al Inglés...")
                    try:
                        final_content = translate_with_mymemory(questions_output)
                        st.success("✅ Traducción completada")
                        log_message("Preguntas traducidas al Inglés")
                    except Exception as e:
                        st.warning(f"⚠️ Error en traducción: {e}. Usando contenido original.")
                        final_content = questions_output
                
                # Escribir archivo TXT
                with open(st.session_state.txt_filename, "w", encoding="utf-8") as file:
                    file.write(final_content)
                
                st.success(f"✅ Archivo TXT guardado: {os.path.basename(st.session_state.txt_filename)}")
                log_message(f"✅ Archivo TXT guardado: {st.session_state.txt_filename}")
                
            except Exception as e:
                st.error(f"❌ Error al guardar archivo TXT: {e}")
                st.code(traceback.format_exc())
                return
            
            # Generar archivo Excel
            try:
                #template_path = "Plantilla Creación de Preguntas N4S V2.xlsx"
                template_path=resource_path("Plantilla Creación de Preguntas N4S V2.xlsx")
                if st.session_state.formato == "Quizz":

                    #template_path_quizz = "NPL Plantilla Quizzes.xlsm"
                    template_path_quizz=resource_path("NPL Plantilla Quizzes.xlsm")
                    st.session_state.excel_filename = generate_unique_filename(
                        os.path.join(output_dir, f"preguntas_generadas_{identifier}"),
                        ".xlsm"
                    )
                    st.info("🛠 Generando archivo Excel Quizz...")
                    progress_bar = st.progress(0)
                    export_txt_to_excel_quizz(
                        st.session_state.txt_filename,
                        template_path_quizz,
                        st.session_state.excel_filename,
                        st.session_state.chapters
                    )
                    progress_bar.progress(100)
                else:
                    st.session_state.excel_filename = generate_unique_filename(
                        os.path.join(output_dir, f"preguntas_generadas_{identifier}"),
                        ".xlsx"
                    )
                    st.info("🛠 Generando archivo Excel...")
                    export_txt_to_excel(
                        st.session_state.txt_filename,
                        template_path,
                        st.session_state.excel_filename,
                        st.session_state.chapters
                    )
                
                st.success(f"✅ Archivo Excel generado: {os.path.basename(st.session_state.excel_filename)}")
                log_message(f"✅ Archivo Excel generado: {st.session_state.excel_filename}")
                
            except Exception as e:
                st.error(f"❌ Error al exportar a Excel: {e}")
                st.code(traceback.format_exc())
                return
            
            st.session_state.process_completed = True
            st.success("🎉 Proceso completado exitosamente.")
                

    # Mostrar resultados y opciones de descarga
    if st.session_state.process_completed:
        st.header("Resultados")
        st.subheader("📁 Archivo Generado")
        st.write(f"**Archivo Excel generado:** `{os.path.basename(st.session_state.excel_filename)}`")
        log_message(f"✅ Archivo Excel generado: `{os.path.basename(st.session_state.excel_filename)}`.")

        if os.path.exists(st.session_state.excel_filename):
            
            # Calcular tiempo transcurrido y convertirlo a formato legible
            if st.session_state.start_time:
                elapsed_time = time.time() - st.session_state.start_time
                hours, remainder = divmod(elapsed_time, 3600)  # Obtener horas y el resto en segundos
                minutes, seconds = divmod(remainder, 60)  # Obtener minutos y el resto en segundos
                # Crear un formato legible
                time_str = f"{int(hours)} horas, {int(minutes)} minutos, {int(seconds)} segundos" if hours > 0 else \
                        f"{int(minutes)} minutos, {int(seconds)} segundos" if minutes > 0 else \
                        f"{int(seconds)} segundos"
                st.write(f"⏱️ Tiempo total transcurrido: {time_str}.")
                log_message(f"✅ Proceso completado en {time_str}.")
            else:
                st.write("⏱️ No se pudo calcular el tiempo transcurrido.")
                log_message("⚠️ No se pudo calcular el tiempo transcurrido.")

            st.subheader("🔎 Vista Previa del Archivo Excel")
            try:
                excel_preview = pd.read_excel(st.session_state.excel_filename, sheet_name=0)
                st.dataframe(excel_preview.head(10))  # Mostrar las primeras 10 filas
            except FileNotFoundError as e:
                st.error(f"❌ Error al cargar la vista previa del Excel: {e}")
                error_msg = f"❌ Archivo Excel no encontrado: {st.session_state.excel_filename}. Error: {e}"
                st.error(error_msg)
                log_message(error_msg)
            except Exception as e:
                st.error(f"❌ Error al cargar la vista previa del Excel: {e}")
                error_msg = f"❌ Error al cargar la vista previa del Excel: {e}"
                st.error(error_msg)
                log_message(error_msg)
            
            try:
                st.download_button(
                    label="📊 Descargar Archivo Excel",
                    data=open(st.session_state.excel_filename, "rb"),
                    file_name=os.path.basename(st.session_state.excel_filename),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"        
                    )
            except FileNotFoundError as e:
                error_msg = f"❌ Error al descargar el archivo Excel: {e}"
                st.error(error_msg)
                log_message(error_msg)
            except Exception as e:
                error_msg = f"❌ Error inesperado al descargar el archivo Excel: {e}"
                st.error(error_msg)
                log_message(error_msg)
        
    # Reiniciar proceso
    if st.session_state.process_completed:
        if st.button("Iniciar nuevo proceso"):
            # Reiniciar todas las variables de estado excepto las que guardan archivos generados
            st.session_state.content = ""
            st.session_state.chapters = []
            st.session_state.subchapters = []
            st.session_state.process_completed = False
            st.session_state.uploaded_files = []
            st.session_state.clave = ""
            st.session_state.cursocaps = []
            st.session_state.curso = ""
            st.session_state.idioma = ""
            st.session_state.files_loaded = False
            st.session_state.step_completed = 0
            st.session_state.start_time = None
            if "segments" in st.session_state:
                del st.session_state.segments
            st.experimental_rerun()
    
    log_message(f"\n")
    log_message(f"-"*50)
    
if __name__ == "__main__":
    main()