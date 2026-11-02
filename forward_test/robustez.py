# -*- coding: utf-8 -*-
"""
robustez.py — la vara para decidir si algo se opera en real. Usar SIEMPRE.

POR QUE EXISTE
    El 8 y 9 de septiembre de 2026 pasamos por esto: una cohorte paso Monte Carlo p5,
    SPP con 60 configuraciones, walk-forward 4 de 4, multi-activo 11 de 13 y placebo
    0 de 30. Se cableo en real. Y despues resulto ser ruido.

    Fallaron DOS cosas, y las dos hay que chequearlas de ahora en mas:

    1. SESGO DE SELECCION. Todas esas pruebas miden si UNA cohorte es solida EN
       AISLAMIENTO. Ninguna corrige por haberla ELEGIDO entre 43 intentos. Con 43
       intentos al 5% se esperan dos ganadores por puro azar. La correccion es el
       SHARPE DEFLACTADO (Bailey y Lopez de Prado, 2014).

    2. FUERA DE MUESTRA EN ACTIVOS. La regla congelada se aplico a 16 activos que
       nunca habian participado. Adentro daba +0,584%/op; afuera dio -0,216%/op.
       Es la prueba mas barata y la mas dificil de enganar: no hay parametros que
       tocar, o funciona o no.

LA REGLA
    Para operar en REAL hay que cumplir LAS TRES:
        a) Monte Carlo p5 > 0            (solida en aislamiento)
        b) DSR >= 0,95                   (sobrevive a haber sido elegida)
        c) fuera de muestra del mismo signo y magnitud parecida

    Si falta cualquiera, va a papel. No hay excepciones: la unica vez que se hizo una
    excepcion se cableo ruido en real.

USO
    from robustez import evaluar, deflactado, cuantas_faltan
    evaluar(retornos_cohorte, retornos_todos_los_intentos, retornos_fuera_muestra,
            unidad="op")            # <- OBLIGATORIO desde iter.58, keyword-only

    `unidad` dice QUE ES UN ELEMENTO de la serie, en el vocabulario UNIDADES
    (op, activo, celda, dia, racimo, desconocida). No es decorativo: la vara se
    construye con Sharpe POR OPERACION, y una serie por activo o por racimo puesta
    contra ella da un numero que no significa nada. No se puede inferir de (n, sr):
    iter.57 lo midio (sensibilidad 0/6) e iter.58 lo midio sobre la poblacion
    completa (5/18). Por eso lo declara el llamador y la funcion lo guarda.
"""

from __future__ import annotations

import math
import statistics as st

EULER = 0.5772156649015329
UMBRAL_DSR = 0.95

# __VARA_HONESTA_43_2026_09_21__  (iter.56)
# El mismo techo y el mismo piso que intentos.py aplica al POOL (el denominador de
# la vara), aplicados tambien a la COHORTE (el numerador). Hasta esta fecha la
# compuerta era ASIMETRICA: intentos.py rechazaba un Sharpe por ACTIVO o una celda
# con n=2 para entrar al pool, y evaluar() aceptaba exactamente lo mismo como
# cohorte candidata. Medido en iter.56: con la vara honesta (+0,7260), 5 de los 43
# veredictos guardados en disco dan DSR >= 95 % y los 5 tienen |sr| > 1,00 — los
# cinco son series por ACTIVO de iter.9, que la propia vuelta 9 declaro `pasa:false`.
# Un |sr| POR OPERACION mayor que 1,00 es casi siempre otra unidad (un estadistico t
# sin dividir por sqrt(n), o un Sharpe por activo), no una cohorte extraordinaria.
TECHO_SHARPE_OP = 1.0     # gemelo de intentos.TECHO_SHARPE_OP
PISO_N = 5                # gemelo de intentos.PISO_N

# __UNIDAD_OBLIGATORIA_2026_09_21__  (iter.58)
# La unidad de `v` NO se puede inferir de (n, sr): es una propiedad del LLAMADOR.
# Medido en iter.57: la compuerta unidad_ok() (que mira |sr| > 1,00) tiene
# sensibilidad 0/6 sobre los unicos positivos que la casa puede PROBAR. Medido en
# iter.58 sobre la poblacion completa: 5/18 = 0,28. Mira un SINTOMA habitual, no la
# unidad, y por eso 9 de las 36 llamadas reales del repo pasaban series por ACTIVO,
# por RACIMO o de procedencia mezclada contra una vara construida por OPERACION,
# sin que quedara escrito en ningun lado. 18 de los 43 veredictos guardados (41,9 %)
# salieron de esos sitios.
#
# Por eso `unidad=` es OBLIGATORIO y KEYWORD-ONLY. Lo que garantiza, dicho sin
# inflar: sensibilidad 1,00 contra la OMISION (el que no declara no obtiene un
# veredicto, obtiene un TypeError) y CERO contra la MENTIRA (si el llamador declara
# "op" sobre una serie por activo, la funcion no tiene como saberlo). El valor del
# cambio es que la unidad pasa de ser una propiedad no escrita a una declaracion
# firmada, guardada en el veredicto y auditable contra el codigo del llamador.
# unidad_ok() queda como RED DE SEGURIDAD, no como la compuerta principal.
#
# `desconocida` esta en el vocabulario a proposito: el unico sitio del repo que
# re-emite veredictos guardados (bt_reemision_unidad_43) mezcla las tres unidades en
# el mismo bucle y un vocabulario sin casilla para "no se cual es" lo obligaria a
# mentir justo ahi. Lo que se prohibe es el silencio, no la ignorancia: `desconocida`
# suma falta igual que `activo` o `racimo`.
UNIDAD_DE_LA_VARA = "op"
UNIDADES = ("op", "activo", "celda", "dia", "racimo", "desconocida")

# __CONTRASTE_UNIDAD_2026_09_21__  (iter.59)
# iter.58 dejo el agujero escrito con todas las letras: `unidad=` tiene sensibilidad
# 1,00 contra la OMISION y CERO contra la MENTIRA. Un llamador que firma `op` sobre
# una serie por activo obtiene un veredicto limpio, y el arreglo mas barato ante el
# TypeError es exactamente esa mentira.
#
# Esto NO intenta INFERIR la unidad: eso ya se refuto dos veces (D1 `|sr|>1,00` da
# 5/18 = 0,28 sobre la poblacion completa). Lo que hace es DISCREPAR de la
# declaracion: si el llamador firma `op` pero la serie tiene la forma de otra cosa,
# se emite una SOSPECHA FECHADA con nombre propio. No es una falta y no toca `real`:
# el veredicto sigue siendo del llamador, que es quien sabe.
#
# POR QUE {9, 25} Y NO OTRO NUMERO: es el recuento de activos de la casa (25 dentro
# de muestra, 9 reservados). Una serie por activo tiene n = cantidad de activos, asi
# que no es una coincidencia numerica: es la particion de la casa imprimiendose en
# el largo de la serie.
#
# SU TASA DE ERROR, MEDIDA ANTES DE CABLEARLO (iter.59, n=43 veredictos publicados
# con su sitio de origen atribuido) — una advertencia sin su tasa de error es una
# supersticion con permiso de imprimir:
#     precision      16/16 = 1,0000   (12/16 = 0,75 contra la atribucion heredada
#                                      de iter.58, que esta vuelta probo incompleta)
#     sensibilidad   16/23 = 0,6957   — NO caza series largas por activo ni racimos
#                                      grandes: n = 35, 81, 251, 357, 373, 419, 441
#     en el repo real 4/4 = 1,0000    — y las 4 son UNA sola llamada que el parche
#                                      de iter.58 firmo `op` sobre series por activo
#
# O sea: su primera captura no fue un llamador futuro, fue el parche anterior.
# Por eso la advertencia se imprime SIEMPRE, tambien sobre codigo ya auditado.
FIRMA_ACTIVOS = (9, 25)
CONTRASTE_PRECISION = "16/16 = 1,0000 (n=43 veredictos, iter.59 2026-09-21)"
CONTRASTE_SENSIBILIDAD = "16/23 = 0,6957"


# __RESERVA_CACHE_2026_09_22__  (iter.110)
# LA CONDICION 4 DE LA VARA (MISION §2.4, fuera de muestra en TIEMPO) SE PUEDE
# CUMPLIR EN EL PAPEL Y ESTAR VACIA EN LOS HECHOS.
#
# Lo que iter.109 encontro sobre el ancla de MISION §1: el cache congelado que la
# alimento terminaba 2026-09-04 y el corte de reserva declarado es 2026-09-11. O sea
# que NINGUNA operacion de la reserva podia entrar, y el n=7 de 65 que se publico
# como "fuera de muestra temporal" no lo puso la regla: lo puso el borde del cache.
#
# Lo que iter.110 midio sobre TODA la casa (bt_reserva_cache_20260922.py, censo de
# 125 publicaciones):
#   · 63 cohortes publicadas comen de un cache.
#   · De esas, 53 (84,1 %) NO DECLARAN NINGUNA reserva temporal.
#   · De las 10 que si la declaran, 8 (80,0 %) la tienen VACIA POR CONSTRUCCION:
#     su cache ligante termina entre 5,0 y 15,7 dias ANTES del corte.
#   · Solo 2 de 63 (3,2 %) tienen la condicion 4 efectivamente alcanzable, y son
#     justo las dos que leen la MISMA base que define el corte (mom.db) en vez de
#     un cache de factores congelado aparte.
#   · El control por horizonte de salida (iter.110 H5) se come 2,3 pp de los 100:
#     el vacio NO lo explica la mecanica de salida, lo explica el cache.
#
# POR ESO ESTE GATE. Y POR ESO ARRANCA EN MODO AVISO:
# hoy avisaria sobre el 98,9 % de los 93 llamadores vivos de evaluar() del repo
# (uno solo declara), asi que levantar de entrada no seria una vara: seria apagar
# la casa. El aviso va en out["reserva"], NO entra en out["faltas"] y NO PUEDE
# cambiar out["real"] ni por accidente — misma disciplina que __CONTRASTE_UNIDAD__.
#
# LIMITE DECLARADO, escrito aca y no solo en el pre-registro: este gate tiene
# sensibilidad 1,00 contra la OMISION (el que no declara recibe el aviso) y 0,00
# contra la MENTIRA (si el llamador declara un cache_hasta que no es el de su cache,
# la funcion no tiene como saberlo). Es exactamente el perfil de `unidad=`, medido
# en iter.57/58. Prometer mas seria repetir lo que esta casa ya se cobro.
RESERVA_ESTADOS = ("NO_DECLARADA", "CACHE_NO_DECLARADO", "VACIA_POR_CONSTRUCCION",
                   "PARCIAL", "ALCANZABLE")

# __MITADES_CACHE_2026_09_22__ — iter.111.
#
# «Las dos mitades del mismo signo» es, de las condiciones de MISION §2, la UNICA que
# corre SIEMPRE y la unica que hasta hoy NO TENIA IMPLEMENTACION ACA. Cada script se
# la escribia. Censado en iter.111: **165 archivos vivos de `forward_test/` parten en
# mitades, en 375 sitios, con 4 mecanicas distintas conviviendo** — y **10 de las 47
# publicaciones que AFIRMAN mitades no tienen ningun script que las calcule**. Una
# condicion sin implementacion unica no tiene definicion unica, y entonces no se puede
# auditar ni comparar entre cohortes. Esto es lo minimo para que se pueda.
#
# LO QUE iter.111 MIDIO, y que decide que hace esta funcion:
#
#  1. La premisa de que la 2a mitad venia «recortada al 24-29 % del n» es FALSA para
#     el 81,2 % de la casa: la particion de esta casa es ENDOGENA (indice del medio,
#     mediana de la propia muestra, o coordenada normalizada por `len(muestra)`), y
#     una particion endogena se RE-NORMALIZA cuando el cache trunca: `n2/n1 = 1,000`.
#     Solo 1 archivo de 165 (0,6 %) usa una mecanica sensible al truncamiento.
#
#  2. El dano es el OPUESTO y es peor: con el n balanceado, la 2a mitad **se corre
#     hacia atras** y termina donde termina el cache. Medido: la 2a mitad del brazo
#     truncado cubre una MEDIANA DEL 35,1 % del tramo tardio del periodo (22,4 % en el
#     peor caso), contra 98,1 % con cache fresco y 100 % destruncado. O sea que la
#     prueba **APRUEBA MAS FACIL CUANTO MAS VENCIDO ESTA EL CACHE**: no es una prueba
#     debil, es un indicador INVERTIDO, y no deja rastro porque los n se ven parejos.
#
#  3. Y aprueba por falta de POTENCIA, no por estabilidad: en el 57,6 % de las celdas
#     del brazo truncado, la mitad MENOR tiene |t| < 2 (47,7 % tiene |t| < 1). Con el
#     portador completo eso cae a 8,3 % y 0,0 %. Dos monedas con sesgo debil dan
#     «mismo signo» la mitad de las veces por definicion.
#
# Por eso hay TRES disparadores y no uno. D1 es el que la tarea de iter.111 pidio y se
# implementa tal cual, sin cambiarle la consigna — pero se publica su cobertura REAL,
# que sobre la mecanica que los scripts de esta casa implementan es **0 de 10**: D1
# solo no cerraria nada. Los que muerden son D2 y D3.
MITADES_ESTADOS = ("NO_DECLARADA", "APROBADA", "SE_DA_VUELTA", "NO_EVALUADA")
MITADES_PISO_N = 20          # n minimo de la mitad menor para que la prueba discrimine
MITADES_T_MIN = 1.0          # |t| minimo de la mitad menor
MITADES_RAZON_MIN = 0.50     # n2 >= 50 % de n1   (D1, lo que pide la tarea de iter.111)
MITADES_COB_MIN = 0.50       # la 2a mitad tiene que cubrir >= 50 % del tramo tardio

# __SELLO_PORTADOR_2026_09_22__ — iter.113.
#
# EL PROBLEMA. iter.110 y iter.111 persiguieron el cache VENCIDO: un frame congelado
# que se pudre y hace que la cifra describa un periodo mas corto del que dice. iter.112
# encontro el enemigo OPUESTO y peor: **14 de las 30 cohortes medibles no nombran
# NINGUN truncador**. Comen directo de `forward_test.db`, `vp_engine.db`,
# `label_outcomes.db` o `mom.db`, que se escriben todos los dias. Su cobertura de
# calendario da 100 % **no porque cubran el periodo sino porque no hay nada que las
# congele**: su muestra cambia cada vez que alguien las vuelve a correr. iter.109 ya
# habia medido que **+0,2337 pp del ancla de MISION §1 salian solo de que dia se la
# corria**. Un cache vencido, al menos, es reproducible. El frame que no existe no
# deja huella, y por eso es peor que el frame que se pudre.
#
# QUE HACE EL SELLO. Cuatro campos que el llamador declara junto a su cifra:
#
#     n         cuantas filas entraron
#     max_ts    el instante de la ultima
#     sha_ids   blake2b-16 del conjunto ORDENADO de ids
#     sha_filas blake2b-16 de (id, ts, valor) ordenados
#
# Los dos hashes no son redundantes y la diferencia es el hallazgo de iter.113:
# `sha_ids` NO ve la MUTACION RETROACTIVA. Una operacion que estaba abierta cuando se
# publico la cifra y cerro despues **conserva su id**: el conjunto de ids no cambia,
# el conjunto de filas si. Sellar solo los ids deja pasar justo el cambio que ningun
# filtro por fecha recupera.
#
# EL LIMITE, ESCRITO ACA Y NO SOLO EN EL PRE-REGISTRO — y es el motivo por el que esta
# funcion no promete lo que la tarea de iter.113 le pedia. **El sello DETECTA, no
# RECONSTRUYE.** Un hash que no coincide avisa que la muestra cambio; no deja rebajar
# la muestra de hoy a la de ayer, porque de un hash no se sale. Para REPRODUCIR hace
# falta la LISTA de ids, no su resumen. Lo que el sello si compra, y no es poco, es
# que la obsolescencia deje de ser invisible: hoy una cifra sin frame congelado se
# re-corre y da otro numero sin que nada grite.
#
# Por eso `CRECIO` esta rotulado como INFERENCIA DEBIL: con `n` y `max_ts` se puede
# sospechar que el portador solo crecio, pero **un hash no puede probar contencion**.
# Si hicieran falta garantias, hay que guardar la lista.
SELLO_ESTADOS = ("NO_DECLARADO", "SIN_REFERENCIA", "COINCIDE", "MUTO", "CRECIO",
                 "DIVERGE")
SELLO_CAMPOS = ("n", "max_ts", "sha_ids", "sha_filas")


def sello_de(ids, ts=None, valores=None):
    """
    __SELLO_PORTADOR_2026_09_22__
    Calcula el sello a partir de lo que el llamador YA TIENE en memoria. No abre
    bases ni archivos a proposito: `robustez.py` no depende de nada y no va a empezar
    a depender ahora.

    `ids`      identificadores de las filas que entraron a la cifra
    `ts`       instantes (ISO, ms o s epoch), opcional: sin ellos no hay `max_ts`
    `valores`  el valor por fila (pnl, retorno) que hace a la cifra, opcional: sin
               ellos `sha_filas` degrada a `sha_ids` y la MUTACION deja de verse,
               cosa que se dice en el propio dict con `cubre_mutacion`.
    """
    import hashlib
    ids = [str(x) for x in (ids or [])]
    n = len(ids)
    mx = None
    if ts:
        for x in ts:
            v = _a_ms(x)
            if v is not None and (mx is None or v > mx):
                mx = v
    h1 = hashlib.blake2b(digest_size=16)
    for s in sorted(ids):
        h1.update(s.encode("utf-8", "replace") + b"\x00")
    cubre = bool(valores) and len(valores) == n
    if cubre:
        filas = sorted("%s|%s|%s" % (ids[i], (ts[i] if ts and i < len(ts) else ""),
                                     valores[i]) for i in range(n))
        h2 = hashlib.blake2b(digest_size=16)
        for s in filas:
            h2.update(s.encode("utf-8", "replace") + b"\x00")
        sha2 = h2.hexdigest()
    else:
        sha2 = h1.hexdigest()
    return {"n": n, "max_ts": mx, "sha_ids": h1.hexdigest(), "sha_filas": sha2,
            "cubre_mutacion": cubre}


def sello(actual=None, esperado=None):
    """
    __SELLO_PORTADOR_2026_09_22__
    Compara el sello de HOY contra el que la cohorte declaro cuando publico su cifra.

    NUNCA devuelve una falta y NUNCA decide nada. Quien la llama tiene PROHIBIDO
    sumar esto a `faltas` o tocar `real`: arranca en modo AVISO, igual que
    `contraste_unidad()` (iter.59), `reserva_alcanzable()` (iter.110) y `mitades()`
    (iter.111). Pasarla a falta es decision HUMANA y se toma con la cobertura a la
    vista, no aca.
    """
    out = {"estado": "NO_DECLARADO", "aviso": None, "actual": actual,
           "esperado": esperado, "delta_n": None, "inferencia": None}
    if not actual:
        out["aviso"] = ("SIN SELLO: la cifra no dice de que conjunto de operaciones "
                        "salio. Si su portador se escribe todos los dias, re-correrla "
                        "manana da otro numero y nada lo va a gritar (iter.109: "
                        "+0,2337 pp del ancla de MISION §1 salian solo del dia).")
        return out
    if not esperado:
        out["estado"] = "SIN_REFERENCIA"
        out["aviso"] = ("SELLO NUEVO, sin referencia contra que comparar: sirve de acá "
                        "en adelante, no valida la cifra de hoy.")
        return out
    a, e = actual, esperado
    out["delta_n"] = (a.get("n") or 0) - (e.get("n") or 0)
    if a.get("sha_filas") and a.get("sha_filas") == e.get("sha_filas"):
        out["estado"] = "COINCIDE"
        return out
    if a.get("sha_ids") and a.get("sha_ids") == e.get("sha_ids"):
        out["estado"] = "MUTO"
        out["aviso"] = ("MISMOS ids, DISTINTAS filas: hubo MUTACION RETROACTIVA. Filas "
                        "que ya existian al publicar cambiaron despues (tipico: una "
                        "operacion abierta que cerro). Esto NO se recupera filtrando "
                        "por fecha: el filtro por ts las deja adentro, con su valor de "
                        "hoy. La cifra publicada no es reconstruible.")
        return out
    if out["delta_n"] > 0 and (a.get("max_ts") or 0) > (e.get("max_ts") or 0):
        out["estado"] = "CRECIO"
        out["inferencia"] = "DEBIL: un hash no puede probar contencion"
        out["aviso"] = ("el portador tiene %d filas mas y un max_ts posterior: la cifra "
                        "publicada describe un subconjunto de lo que hay hoy. "
                        "INFERENCIA DEBIL — de `n` y `max_ts` no se deduce que las "
                        "viejas sigan iguales; para PROBARLO hace falta la lista de "
                        "ids, no su hash." % out["delta_n"])
        return out
    out["estado"] = "DIVERGE"
    out["aviso"] = ("el sello no coincide y el portador no simplemente crecio "
                    "(delta_n=%+d): la muestra de la cifra publicada y la de hoy son "
                    "conjuntos distintos, y de los hashes no se sale para saber en que."
                    % out["delta_n"])
    return out


# __SELLO_VARA_2026_09_22__ — iter.115.
#
# EL PROBLEMA. iter.113 sello el PORTADOR: de que conjunto de operaciones salio la
# cifra. Queda sin sellar la otra mitad del veredicto, y es la que decide REAL o
# PAPEL: **la VARA contra la que se juzgo**. La vara no es una constante de la casa,
# es `sd(pool) * f(N_attempted)`, y las dos entradas se mueven solas. Medido esta
# vuelta:
#
#   · la vara paso de +0,7260441358 (iter.56, N=136) a +0,8358971342 (hoy, N=379),
#     +0,1099 en 27 archivos de intentos y 12,84 dias;
#   · el pool entra a **27,97 intentos/dia** (30,0/dia en los ultimos 7), asi que la
#     vara se mueve mas que el 4o decimal con el que esta casa publica en **0,01 dias
#     (unos 14 minutos)** y mas de 0,005 en **0,75 dias**;
#   · **180 de 180** (113 de 113 sin las copias) de los veredictos con (sr, sr0) en
#     disco tuvieron AL MENOS un movimiento de pool posterior a su escritura. O sea:
#     ni uno solo de los veredictos guardados de esta casa se puede juzgar hoy contra
#     la vara con la que se emitio.
#
# POR QUE NO ALCANZA CON `N`, Y ESTA MEDIDO, NO SUPUESTO. La tentacion es guardar el
# N y listo. Se probo: de los **92** veredictos deduplicados que declaran un N, solo
# **3 (3,3 %)** reconstruyen su propio `sr0` desde `vara(pool_de_hoy, N_declarado)`
# con tolerancia 1e-9, y **5 (5,4 %)** con 1e-3. La mediana del error de
# reconstruccion es **0,0799** y el maximo **1,5346**. La `sd` implicita en los `sr0`
# guardados va de **0,1135 a 0,9125 — un rango de 8,04x**. `N` dice el TAMANO del
# pool; la vara depende tambien de su FORMA. Por eso el sello lleva `sd` y el hash.
#
# LA DIFERENCIA CON EL SELLO DE PORTADOR, Y ES EL HALLAZGO DE ESTA VUELTA. El sello de
# iter.113 tiene un estado `CRECIO` que es tranquilizador: si el portador solo sumo
# filas, la cifra vieja describe un subconjunto. **Para la vara ese razonamiento no
# vale.** Sobre la historia real del pool: de los **26** tramos en que N crece
# estrictamente, en **8** la vara **BAJA**, y el peor baja **-0,0589 con N subiendo
# 40** (al entrar `barrido_salida.json`, 40 intentos homogeneos que aplastan la `sd`).
# Es el mismo mecanismo que iter.98 encontro por el otro lado. **La vara NO es
# monotona en N.** Asi que este sello no infiere el signo del cambio: lo MIDE y lo
# publica en `delta_vara`. Un sello que dijera "CRECIO" estaria diciendo "tranquilo,
# la vara subio" en 8 de 26 casos en que bajo.
#
# EL LIMITE, EL MISMO QUE EL DE iter.113 Y DICHO ACA: **el sello DETECTA, no
# RECONSTRUYE.** De `sha_pool` no se sale: si no coincide, no hay forma de rebajar el
# pool de hoy al de ayer para re-emitir la cifra vieja. Para eso hace falta la LISTA
# de Sharpe, y entra: 240 floats son ~5 KB contra 77 bytes de sello. `sello_vara_de`
# acepta `guardar_lista=True` para el que quiera pagar esos 5 KB y poder reconstruir
# de verdad.
SELLO_VARA_ESTADOS = ("NO_DECLARADO", "SIN_REFERENCIA", "COINCIDE", "SUBIO", "BAJO",
                      "MISMA_VARA_OTRO_POOL")
SELLO_VARA_CAMPOS = ("N", "N_puntuados", "sd", "sr0", "sha_pool")


def sello_vara_de(sr_intentos, N=None, sd=None, guardar_lista=False):
    """
    __SELLO_VARA_2026_09_22__
    El sello de la VARA con la que un veredicto se esta por juzgar. Se calcula de lo
    que el llamador YA TIENE: la lista de Sharpe de los intentos y, si lo sabe, el N
    attempted. No abre nada.

    `N`   trials ATTEMPTED (MISION §2.2). Con None se usa len(sr_intentos), que es el
          numero optimista: cuantos se pudo PUNTUAR. Mismo default que `vara()`.
    `sd`  dispersion transversal, si el llamador sostiene otra (ver `vara_desde_sd` y
          `intentos.pool_efectivo`). Con None se estima de `sr_intentos`.
    `guardar_lista`  agrega `lista` con los Sharpe ordenados. Es lo UNICO que permite
          RECONSTRUIR la vara vieja; el hash solo permite DETECTAR que cambio.

    `sha_pool` es del MULTICONJUNTO: se ordena antes de hashear, porque la vara no
    depende del orden en que se acumularon los intentos. Dos corridas que junten los
    mismos Sharpe en distinto orden tienen que dar el mismo sello, y lo dan.
    """
    import hashlib
    xs = [float(x) for x in (sr_intentos or [])]
    n_punt = len(xs)
    Nef = n_punt if N is None else int(N)
    if sd is None:
        sd_ef = (st.pvariance(xs) ** 0.5) if n_punt >= 2 else None
    else:
        sd_ef = float(sd)
    sr0 = vara_desde_sd(sd_ef, Nef) if (sd_ef and sd_ef > 0 and Nef >= 3) else None
    h = hashlib.blake2b(digest_size=16)
    for x in sorted(xs):
        h.update((repr(x) + "\x00").encode("utf-8"))
    out = {"N": Nef, "N_puntuados": n_punt, "sd": sd_ef, "sr0": sr0,
           "sha_pool": h.hexdigest()}
    if guardar_lista:
        out["lista"] = sorted(xs)
    return out


def sello_vara(actual=None, esperado=None):
    """
    __SELLO_VARA_2026_09_22__
    Compara la vara de HOY contra la que el veredicto declaro cuando se publico.

    NUNCA devuelve una falta y NUNCA decide nada. Quien la llama tiene PROHIBIDO
    sumar esto a `faltas` o tocar `real`: arranca en modo AVISO, igual que
    `contraste_unidad()` (iter.59), `reserva_alcanzable()` (iter.110), `mitades()`
    (iter.111) y `sello()` (iter.113). Pasarla a falta es decision HUMANA.

    No hay estado `CRECIO`. La vara no es monotona en N (medido iter.115: 8 de 26
    tramos con N creciente la bajan). El signo va MEDIDO en `delta_vara`.
    """
    out = {"estado": "NO_DECLARADO", "aviso": None, "actual": actual,
           "esperado": esperado, "delta_N": None, "delta_sd": None,
           "delta_vara": None, "reconstruible": False}
    if not actual:
        out["aviso"] = ("SIN SELLO DE VARA: la cifra no dice contra que umbral se "
                        "juzgo. La vara de esta casa es sd(pool)*f(N) y entra a "
                        "27,97 intentos/dia: se mueve mas que el 4o decimal en 14 "
                        "minutos. Un DSR sin sello de vara no es re-emitible.")
        return out
    if not esperado:
        out["estado"] = "SIN_REFERENCIA"
        out["aviso"] = ("SELLO DE VARA NUEVO, sin referencia contra que comparar: "
                        "sirve de aca en adelante, no valida la cifra de hoy.")
        return out
    a, e = actual, esperado
    out["delta_N"] = (a.get("N") or 0) - (e.get("N") or 0)
    if a.get("sd") is not None and e.get("sd") is not None:
        out["delta_sd"] = a["sd"] - e["sd"]
    if a.get("sr0") is not None and e.get("sr0") is not None:
        out["delta_vara"] = a["sr0"] - e["sr0"]
    out["reconstruible"] = bool(e.get("lista"))
    if a.get("sha_pool") and a.get("sha_pool") == e.get("sha_pool") \
       and out["delta_N"] == 0:
        out["estado"] = "COINCIDE"
        return out
    if out["delta_vara"] is not None and abs(out["delta_vara"]) <= 1e-15 \
       and a.get("sha_pool") != e.get("sha_pool"):
        out["estado"] = "MISMA_VARA_OTRO_POOL"
        out["aviso"] = ("el POOL cambio pero la vara da el mismo numero: sd*f(N) no "
                        "es inyectiva. El veredicto sigue siendo comparable, la "
                        "PROCEDENCIA no. No confundir una cosa con la otra.")
        return out
    dv = out["delta_vara"]
    if dv is None:
        out["estado"] = "SUBIO" if (out["delta_N"] or 0) > 0 else "BAJO"
        out["aviso"] = ("el pool cambio (delta_N=%+d) y NO hay `sr0` en uno de los dos "
                        "sellos: no se puede decir el signo del movimiento de la vara. "
                        "Un sello sin `sr0` no sirve para esto." % (out["delta_N"] or 0))
        return out
    out["estado"] = "SUBIO" if dv > 0 else ("BAJO" if dv < 0 else "COINCIDE")
    if out["estado"] == "COINCIDE":
        return out
    out["aviso"] = (
        "LA VARA SE MOVIO %+.10f (de %.10f a %.10f) con delta_N=%+d y delta_sd=%+.10f. "
        "El veredicto publicado se juzgo contra un umbral que YA NO EXISTE. %s "
        "INFERENCIA IMPOSIBLE desde el hash: `sha_pool` DETECTA el cambio, no permite "
        "RECONSTRUIR el pool viejo; para eso hace falta la lista (guardar_lista=True)."
        % (dv, e.get("sr0") or float("nan"), a.get("sr0") or float("nan"),
           out["delta_N"] or 0, out["delta_sd"] if out["delta_sd"] is not None else float("nan"),
           ("La vara BAJO: el veredicto viejo es MAS exigente que el de hoy, o sea que "
            "si pasaba entonces pasa ahora — pero si NO pasaba, hoy podria pasar y hay "
            "que re-emitirlo." if dv < 0 else
            "La vara SUBIO: todo veredicto que pasaba por poco puede haber dejado de "
            "pasar. Hay que re-emitirlo antes de citarlo.")))
    return out


def _a_ms(x):
    """ISO-8601, ms epoch o s epoch -> ms. None si no se puede leer como reloj."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        f = float(x)
        if f > 1e14:
            return f / 1000.0
        if f > 1e11:
            return f
        if f > 1e9:
            return f * 1000.0
        return None
    import datetime as _dt
    try:
        d = _dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp() * 1000.0


def reserva_alcanzable(corte_reserva=None, cache_hasta=None, fin_periodo=None):
    """
    __RESERVA_CACHE_2026_09_22__
    ¿La reserva temporal que el llamador declaro era ALCANZABLE con el cache que
    uso? Devuelve un dict con `estado` (vocabulario RESERVA_ESTADOS), `aviso` y,
    cuando se puede, `alcance` (fraccion de la ventana de reserva que el cache
    hacia alcanzable) y `dias_de_hueco`.

    NUNCA devuelve una falta y NUNCA decide nada. Quien la llama tiene PROHIBIDO
    sumar esto a `faltas` o tocar `real`: arranca en modo AVISO a proposito.
    """
    c = _a_ms(corte_reserva)
    k = _a_ms(cache_hasta)
    f = _a_ms(fin_periodo)
    out = {"corte_reserva": corte_reserva, "cache_hasta": cache_hasta,
           "fin_periodo": fin_periodo, "alcance": None, "dias_de_hueco": None,
           "modo": "AVISO"}
    if c is None:
        out["estado"] = "NO_DECLARADA"
        out["aviso"] = (
            "RESERVA TEMPORAL NO DECLARADA. La condicion 4 de la vara (MISION §2.4) "
            "no se puede auditar sin `corte_reserva=`. Medido en iter.110: 53 de 63 "
            "cohortes publicadas de esta casa que comen de un cache (84,1 %) no "
            "declaran ninguna, y de las 10 que si, 8 la tenian VACIA.")
        return out
    if k is None:
        out["estado"] = "CACHE_NO_DECLARADO"
        out["aviso"] = (
            "CORTE DECLARADO SIN INSTANTE FINAL DEL CACHE. Un corte de reserva sin "
            "`cache_hasta=` al lado no dice si la reserva tenia algo adentro: el "
            "ancla de MISION §1 'paso' la condicion 4 con la reserva vacia porque su "
            "cache terminaba 7 dias antes del corte (iter.109). Declarar el ULTIMO "
            "TIMESTAMP DENTRO de los archivos del cache, no su mtime.")
        return out
    out["dias_de_hueco"] = (c - k) / 86400000.0
    if k < c:
        out["estado"] = "VACIA_POR_CONSTRUCCION"
        out["alcance"] = 0.0
        out["aviso"] = (
            "*** RESERVA TEMPORAL VACIA POR CONSTRUCCION *** El cache termina %.2f "
            "dias ANTES del corte de reserva, asi que NINGUNA operacion de la reserva "
            "podia entrar. Lo que este veredicto llame 'fuera de muestra en tiempo' no "
            "lo puso la regla: lo puso el borde del cache. La condicion 4 de MISION "
            "§2.4 NO esta cumplida, por mas que el numero exista."
            % out["dias_de_hueco"])
        return out
    if f is not None and f > c:
        out["alcance"] = max(0.0, min(k, f) - c) / (f - c)
    if out["alcance"] is not None and out["alcance"] < 0.5:
        out["estado"] = "PARCIAL"
        out["aviso"] = (
            "RESERVA TEMPORAL PARCIAL: el cache hace alcanzable solo el %.1f %% de la "
            "ventana reservada. El n de la reserva esta acotado por el CACHE y no por "
            "la regla; publicarlo sin decirlo repite el defecto de iter.109."
            % (100 * out["alcance"]))
        return out
    out["estado"] = "ALCANZABLE"
    out["aviso"] = None
    return out


def mitades(v1=None, v2=None, *, ts2_max=None, corte=None, fin_periodo=None):
    """
    __MITADES_CACHE_2026_09_22__
    La prueba de «las dos mitades del mismo signo» de MISION §2, con el n de cada
    mitad AL LADO DEL SIGNO — que es lo que hoy no viaja — y con los tres
    disparadores de NO_EVALUADA que iter.111 midio.

    `v1`, `v2`: retornos por operacion de la 1a y la 2a mitad, en ese orden temporal.
    `corte`, `ts2_max`, `fin_periodo`: opcionales, para D2. Sin ellos, D2 no corre y
    se dice (no se inventa un aprobado).

    NUNCA devuelve una falta y NUNCA decide nada. Quien la llama tiene PROHIBIDO
    sumar esto a `faltas` o tocar `real`: arranca en modo AVISO, igual que
    `reserva_alcanzable()` (iter.110) y `contraste_unidad()` (iter.59). Pasarla a
    falta es decision HUMANA y se toma con la cobertura a la vista.
    """
    out = {"modo": "AVISO", "n1": None, "e1": None, "t1": None,
           "n2": None, "e2": None, "t2": None, "razon_n": None,
           "cobertura_2a": None, "disparadores": [], "aviso": None}
    if not v1 or not v2:
        out["estado"] = "NO_DECLARADA"
        out["aviso"] = (
            "PRUEBA DE MITADES NO DECLARADA. Es la unica condicion de MISION §2 que "
            "corre SIEMPRE, y por eso es la unica que nadie declara y nadie puede "
            "auditar. Censado en iter.111: 10 de las 47 publicaciones que AFIRMAN "
            "mitades no tienen ningun script que las calcule.")
        return out

    def _st(v):
        n = len(v)
        e = sum(v) / n
        if n < 2:
            return n, e, None
        m = sum((x - e) ** 2 for x in v) / (n - 1.0)
        s = m ** 0.5
        return n, e, (e / (s / (n ** 0.5)) if s > 0 else None)

    out["n1"], out["e1"], out["t1"] = _st(v1)
    out["n2"], out["e2"], out["t2"] = _st(v2)
    out["razon_n"] = out["n2"] / out["n1"] if out["n1"] else None

    disp = []
    # D1 — DESBALANCE. Lo que pide la tarea de iter.111, implementado tal cual. Su
    # cobertura REAL sobre la mecanica que esta casa usa es 0 de 10, y se dice: sin
    # D2 y D3 al lado, esta sola seria una regla que nunca dispara.
    if out["razon_n"] is not None and out["razon_n"] < MITADES_RAZON_MIN:
        disp.append("D1_DESBALANCE")
    # D2 — COBERTURA DE CALENDARIO. El dano que de verdad hace un cache vencido.
    if ts2_max is not None and corte is not None and fin_periodo is not None:
        c, t2, f = _a_ms(corte), _a_ms(ts2_max), _a_ms(fin_periodo)
        if None not in (c, t2, f) and f > c:
            out["cobertura_2a"] = max(0.0, min(1.0, (t2 - c) / (f - c)))
            if out["cobertura_2a"] < MITADES_COB_MIN:
                disp.append("D2_COBERTURA")
    # D3 — POTENCIA. Una prueba que no discrimina no aprueba: no evalua.
    # __MITADES_CACHE_2026_09_22__ — la mitad que fija la potencia es LA MAS DEBIL, y
    # "mas debil" se mide por |t|, no por n. La primera version de esta funcion elegia
    # `t1` cuando `n1 <= n2`, y como el 81,2 % de la casa parte con n BALANCEADO, el
    # empate caia SIEMPRE en la primera mitad: una 2a mitad puro ruido pasaba con el
    # |t| de la 1a. Lo cazo verify_mitades_20260922.py en el caso (5), no una lectura.
    n_men = min(out["n1"], out["n2"])
    ts = [t for t in (out["t1"], out["t2"])]
    t_men = None if None in ts else min(ts, key=abs)
    if n_men < MITADES_PISO_N or t_men is None or abs(t_men) < MITADES_T_MIN:
        disp.append("D3_POTENCIA")

    out["disparadores"] = disp
    mismo = (out["e1"] > 0) == (out["e2"] > 0)
    if disp:
        out["estado"] = "NO_EVALUADA"
        motivos = {
            "D1_DESBALANCE":
                "la 2a mitad tiene %d operaciones contra %d de la 1a (%.0f %%): una "
                "mitad con menos de la mitad del n pasa por no tener n, no por ser "
                "del mismo signo" % (out["n2"], out["n1"], 100 * (out["razon_n"] or 0)),
            "D2_COBERTURA":
                "la 2a mitad cubre el %.1f %% del tramo tardio del periodo: el resto "
                "no lo mira NINGUNA de las dos mitades, y con el n balanceado eso no "
                "deja rastro (iter.111: mediana 35,1 %% con cache vencido)"
                % (100 * (out["cobertura_2a"] or 0)),
            "D3_POTENCIA":
                "la mitad menor tiene n=%d y |t|=%s: con esa potencia, «mismo signo» "
                "es el resultado por defecto y no evidencia de estabilidad"
                % (n_men, "—" if t_men is None else "%.2f" % abs(t_men)),
        }
        out["aviso"] = ("MITADES **NO EVALUADA** (no APROBADA): " +
                        " · ".join(motivos[d] for d in disp))
    else:
        out["estado"] = "APROBADA" if mismo else "SE_DA_VUELTA"
        if not mismo:
            out["aviso"] = ("MITADES SE DAN VUELTA: 1a %+.4f (n=%d) contra 2a %+.4f "
                            "(n=%d)" % (out["e1"], out["n1"], out["e2"], out["n2"]))
    return out


class UnidadNoDeclarada(TypeError):
    """__UNIDAD_OBLIGATORIA_2026_09_21__ — el llamador no dijo que es un elemento de `v`."""


def contraste_unidad(unidad, v):
    """
    __CONTRASTE_UNIDAD_2026_09_21__
    ¿Lo que el llamador FIRMO se parece a lo que la serie ES? Devuelve la lista de
    sospechas fechadas, o [] si no hay ninguna.

    Solo habla cuando la firma es `op`: si el llamador ya confeso otra unidad, el
    motivo que vale es su confesion y no una heuristica. NUNCA devuelve una falta:
    quien lo llama tiene prohibido sumar esto a `faltas` o tocar `real`.
    """
    if unidad != UNIDAD_DE_LA_VARA:
        return []
    s, n, m = sharpe(v), len(v), []
    if n in FIRMA_ACTIVOS:
        m.append(
            "SOSPECHA (2026-09-21, precision %s): se firmo `op` pero n=%d es "
            "EXACTAMENTE el recuento de activos de la casa %s. Si cada elemento de "
            "la serie es un ACTIVO y no una operacion, el DSR de este veredicto no "
            "significa nada. Verificar como se construyo la serie y, si corresponde, "
            "declarar unidad='activo'." % (CONTRASTE_PRECISION, n, list(FIRMA_ACTIVOS)))
    if s is not None and abs(s) > TECHO_SHARPE_OP:
        m.append(
            "SOSPECHA (2026-09-21): se firmo `op` pero |Sharpe| %.4f pasa el techo "
            "por operacion %.2f, que es el sintoma habitual de otra unidad. Esta "
            "rama NO agrega informacion nueva sobre la falta que unidad_ok() ya "
            "levanta (medido iter.59: aporte marginal 0 veredictos sobre 43); se "
            "imprime para que la sospecha tenga NOMBRE y no solo un motivo."
            % (abs(s), TECHO_SHARPE_OP))
    return m


def _ncdf(x):
    """
    PHI(x) por erfc.

    ★ __VARA_HONESTA_43_2026_09_21__ — ANTES era 0.5*(1+erf(x/sqrt(2))), que en la
      cola izquierda se come el resultado por cancelacion catastrofica: ya se
      equivoca 1,8 % en x=-8,0 y devuelve 0.0 EXACTO desde x=-8,259 (dsr 7,34e-17).
      Consecuencia medida en iter.56: 13 de los 43 veredictos guardados en disco
      (30,2 %) tienen su DSR escrito como "0.0" y NO se pueden reconstruir contra
      una vara nueva — solo admiten cota superior. 0.5*erfc(-x/sqrt(2)) es exacta
      hasta x ~ -38. No cambia ningun veredicto (el umbral es 0,95): cambia que el
      numero publicado siga siendo informacion en vez de un cero.
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _nppf_acklam(p):
    if p <= 0.0 or p >= 1.0:
        return float("inf") if p >= 1.0 else float("-inf")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _nppf(p, _pasos=4):
    """
    PHI^-1(p) — Acklam + Newton.

    ★ __VARA_HONESTA_43_2026_09_21__ — el racional de Acklam es una APROXIMACION,
      no un inverso. Medido en iter.56 sobre 200.000 puntos de (0,1), su peor error
      de ida y vuelta |PHI(PHI^-1(p)) - p| es 2,726e-10; con estos pasos de Newton
      baja a 3,331e-16. Importa porque todo DSR publicado es re-juzgable contra una
      vara futura invirtiendo esta funcion, y porque cuantas_faltan() la usa para
      decir cuanta muestra hace falta.
    """
    z = _nppf_acklam(p)
    if not math.isfinite(z):
        return z
    for _ in range(_pasos):
        d = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        if d <= 0.0:
            break
        z -= (_ncdf(z) - p) / d
    return z


def unidad_ok(v):
    """
    __VARA_HONESTA_43_2026_09_21__
    ¿La cohorte esta en la unidad que la vara mide? Devuelve [] si si, o la lista
    de motivos por los que NO lo esta. La vara se construye con Sharpe POR OPERACION
    del pool; comparar contra ella un Sharpe por ACTIVO, o un estadistico t, o una
    celda de n=2, es un error de categoria, no un resultado extraordinario.
    """
    motivos = []
    n = len(v)
    if n < PISO_N:
        motivos.append("n=%d por debajo del piso %d: no es una serie, es un puñado"
                       % (n, PISO_N))
    s = sharpe(v)
    if s is not None and abs(s) > TECHO_SHARPE_OP:
        motivos.append("|Sharpe| %.4f por encima del techo por operacion %.2f: "
                       "casi siempre es OTRA UNIDAD (t sin dividir por sqrt(n), "
                       "Sharpe por activo) y no una cohorte extraordinaria"
                       % (abs(s), TECHO_SHARPE_OP))
    return motivos


def sharpe(v):
    """Sharpe POR OPERACION, sin anualizar."""
    if len(v) < 5:
        return None
    s = st.pstdev(v)
    return (sum(v) / len(v) / s) if s > 0 else None


def vara(sr_intentos, N=None):
    """
    El Sharpe maximo que produce el AZAR probando N configuraciones. Todo lo que
    quede por debajo de esta linea es indistinguible de haber buscado mucho.

    __PISO_N_2026_09_21__ (iter.85) — `N` SEPARADO DE `len(sr_intentos)`.
    Bailey-Lopez de Prado cuenta TRIALS ATTEMPTED. Hasta esta fecha N era
    `len(sr_intentos)`, o sea "cuantos intentos pude PUNTUAR", que no es lo mismo:
    todo intento que fue a DEUDA (existio, se probo, no dejo Sharpe) salia del
    conteo. Medido en iter.85: 154 con Sharpe contra 255 attempted (101 de deuda),
    y la vara pasa de +0,800755 a +0,850091. Esos +0,049336 son EXACTAMENTE el
    optimismo que `intentos.guardar()` venia declarando sin cuantificar.
    `sd` se sigue estimando de los Sharpe disponibles: es un estimador de la
    dispersion transversal de los intentos, y el supuesto que pide es que la deuda
    disperse como lo cosechado. OJO, ese supuesto NO es gratis: iter.85 midio que
    dos submuestras del propio pool (las 92 que declaran n contra las 62 que no)
    difieren en desvio con p=0,0005. El signo del error, eso si, es el seguro:
    con N attempted la vara SUBE, y equivocarse hacia arriba cuesta oportunidad,
    no dinero.
    Por defecto N = len(sr_intentos): TODO llamador anterior conserva su numero.

    ★ TRAMPA, cometida el 2026-09-09 en bt_short_gamma.py y corregida:
      esta funcion usa el DESVIO de los Sharpe de los intentos. Si los intentos se
      rellenan con CEROS (por ejemplo "los 20 intentos de la corrida anterior, que
      no tengo a mano"), la varianza se hunde y la vara BAJA: paso de +0,157 a
      +0,088 y una cohorte de Sharpe 0,159 quedo "APTA PARA REAL" cuando en verdad
      estaba POR DEBAJO de la vara (DSR 47%, no 97%).
      Rellenar con ceros INVIERTE el sentido de la correccion: en vez de castigar la
      busqueda, la premia. Los intentos van con su Sharpe REAL, o no se calcula el
      DSR. Guardar siempre los Sharpe de cada corrida para poder acumularlos.
    """
    if len(sr_intentos) < 3:
        return None
    N = len(sr_intentos) if N is None else int(N)
    if N < 3:
        return None
    sd = st.pvariance(sr_intentos) ** 0.5
    if sd <= 0:
        return None
    return vara_desde_sd(sd, N)


# __POOL_CORRELACIONADO_2026_09_21__  (iter.99)
# LA FORMULA CON LA `sd` INYECTABLE. Hasta esta fecha la unica via a la vara era
# `vara(lista)`, que ESTIMA la sd de la lista con `st.pvariance`. Eso hace que toda
# regla sobre la DISPERSION del pool sea inalcanzable desde afuera, y la dispersion
# es el 79-91 % del efecto (iter.85: 91,4 %; iter.99 canal `sd` de R2: 79,0 %).
# Por eso iter.98, al descubrir que sus 40 intentos homogeneos BAJABAN la vara de
# +0,872876 a +0,805916, no tuvo otra salida que cablear una auto-exclusion DENTRO
# de su propio script: no habia por donde pasar otra sd. Ese remedio es propiedad
# del LLAMADOR y no del POOL, y iter.99 midio lo que cuesta: la misma vuelta se
# juzga con +0,860970 si se auto-excluye y con +0,805916 si no, y la dispersion
# entre los 20 llamadores posibles es 0,132880. O sea: el que barrio 40 se juzgaba
# con MAS vara que el que no barrio nada — premia buscar, que es lo que MISION §2.2
# prohibe.
#
# Esta funcion NO cambia ningun numero: `vara()` la llama con la sd que ya calculaba
# y con el mismo `int(N)`, verificado bit a bit en
# verify_pool_correlacionado_20260921.py. Lo que agrega es la CAPACIDAD de que una
# regla de pool (ver `intentos.pool_efectivo`) ponga su propia sd y su propio N.
# Acepta N no entero: el N EFECTIVO de un pool con bloques correlacionados no es
# entero (medido: 162,93 contra 334 attempted).
def vara_desde_sd(sd, N):
    """El mismo cuerpo de `vara()` con la dispersion dada explicitamente.

    `sd` = dispersion transversal de los intentos (la que el llamador sostenga).
    `N`  = trials attempted, ENTERO O NO. Un pool con bloques correlacionados tiene
           N efectivo fraccionario y redondearlo seria inventar un intento.
    """
    if sd is None or sd <= 0:
        return None
    N = float(N)
    if N < 3:
        return None
    return sd * ((1 - EULER) * _nppf(1 - 1.0 / N) + EULER * _nppf(1 - 1.0 / (N * math.e)))


# __MIGRACION_VARA_2026_09_21__  (iter.86)
# EL PARAMETRO `N` DE vara() ERA INALCANZABLE DESDE LA RUTA QUE EMITE VEREDICTOS.
# iter.85 lo agrego a vara() y lo declaro "retrocompatible por diseño". Medido en
# iter.86 con `ast` sobre los 149 sitios de llamada del repo propio: `deflactado()`
# llamaba `vara(sr_intentos)` con UN posicional y no tenia parametro `N` propio, y
# `evaluar()` llamaba `deflactado(v, sr_intentos)`. O sea que los 212 sitios que
# llaman evaluar()/deflactado() (43,0 % del censo) no podian migrar NI QUERIENDO:
# no habia por donde pasar el N. El alcance efectivo del parche de iter.85 sobre
# sitios VIVOS era 1 (verificar_intentos.py, que llama vara() directo).
# Se enhebra `N` por las dos funciones. Con N=None el numero es el de siempre, bit a
# bit (verificado sobre 3.000 casos en verify_migracion_vara_20260921.py).
#
# OJO, Y ES LO IMPORTANTE: enhebrarlo cablea la CAPACIDAD de migrar, no la migracion.
# NINGUN sitio vivo pasa `N=` todavia, y el motivo esta en el bloque
# «POR QUE EL DEFAULT NO SE INVIERTE» de mas abajo: iter.86 midio que el SIGNO de la
# correccion a N attempted no esta determinado.
def deflactado(v, sr_intentos, N=None):
    """
    Probabilidad de que el Sharpe verdadero supere la vara del azar.

    `N` (iter.86) = TRIALS ATTEMPTED, si el llamador lo sabe. Con None se usa
    len(sr_intentos), que es "cuantos pude PUNTUAR" — el numero optimista. El
    veredicto devuelve LOS DOS: `N` (el que se uso) y `N_puntuados`, para que un
    veredicto guardado diga con que denominador se juzgo y no haya que adivinarlo,
    que es el agujero que iter.85 dejo abierto en los 43 veredictos anteriores.
    """
    sr = sharpe(v)
    sr0 = vara(sr_intentos, N)
    if sr is None or sr0 is None:
        return None
    n = len(v); m = sum(v) / n; sd = st.pstdev(v)
    if sd <= 0:
        return None
    g3 = sum((x - m) ** 3 for x in v) / (n * sd ** 3)
    g4 = sum((x - m) ** 4 for x in v) / (n * sd ** 4)
    den = 1.0 - g3 * sr + ((g4 - 1.0) / 4.0) * sr * sr
    if den <= 0:
        return None
    return {"sr": sr, "sr0": sr0, "n": n,
            "N": len(sr_intentos) if N is None else int(N),
            "N_puntuados": len(sr_intentos),
            "dsr": _ncdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(den))}


# __MIGRACION_VARA_2026_09_21__  (iter.86)
# POR QUE EL DEFAULT DE vara() NO SE INVIERTE, declarado por escrito como la tarea
# de iter.86 pidio («migrar al N attempted, o declarar por escrito por que no»).
#
# La opcion era `N = intentos.N_attempted()[0]` cuando N es None. Se RECHAZA por tres
# motivos, en orden de peso, y cada uno con su numero:
#
# (1) EL SIGNO DE LA CORRECCION NO ESTA DETERMINADO. Es el motivo que manda.
#     `vara() = sd * f(N)`. Subir N de 154 a 255 sube f(N) de 2,678953 a 2,844006,
#     pero `sd` se sigue estimando SOLO de los 154 puntuados. La vara honesta
#     +0,850091 supone que los 101 de deuda dispersan como el pool (sd 0,298906).
#     iter.86 midio el umbral de indiferencia: si la deuda dispersa con
#     sd < 0,252828, migrar BAJA la vara. Y la clase de referencia de la deuda no es
#     el pool: es la de los archivos que no escriben sus numeros — las mudas, sd
#     0,194570 (iter.85) o 0,230401 (re-medido iter.86). LAS DOS estan debajo del
#     umbral. Con sd 0,194570 la vara honesta es +0,746798, o sea -0,053957 contra la
#     publicada: la brecha de iter.85 no sube 0,049, BAJA 0,054. El rango completo
#     segun el supuesto va de +0,660626 (deuda con sd=0) a +0,868951.
#     Ni con un corrimiento de medias se salva: para restaurar +0,800755 con sd
#     0,194570 la deuda necesitaria una media 0,207747 aparte de la del pool (0,695
#     desvios), que nadie midio.
#     MISION §2.2 prohibe exactamente esto: una vara cuyo signo depende de un
#     supuesto no medido no puede ser la vara de la casa.
# (2) CICLO DE IMPORT. `intentos.py:414` ya importa `robustez.vara`. Que robustez
#     importe intentos cierra el ciclo.
# (3) REPRODUCIBILIDAD. `intentos.N_attempted()` LEE bt_cache/. Con el default
#     invertido, la MISMA llamada con la MISMA lista devuelve un numero distinto cada
#     vez que crece el disco: ningun veredicto publicado quedaria reconstruible.
#     `robustez` es PURO a proposito — no lee disco, no importa nada del repo — y esa
#     pureza es lo que permite re-juzgar los 43 veredictos viejos contra una vara nueva.
#
# LO QUE SE HACE EN CAMBIO: `N` explicito y enhebrado (arriba), y `banda_vara()`
# (abajo) para publicar la COTA en vez de un punto. Regla §5.3 del pre-registro:
# la vara VIGENTE sigue siendo la de N=154 hasta que la deuda se COSECHE. Es la ley
# que iter.85 ya habia escrito — un pool de intentos no se limpia, se completa.
def banda_vara(sr_intentos, N_attempted, sd_deuda=None):
    """
    __MIGRACION_VARA_2026_09_21__
    La vara como BANDA y no como punto, que es lo unico honesto mientras haya deuda.

    Con `N_attempted > len(sr_intentos)` hay intentos que existieron y no dejaron
    Sharpe. `f(N)` sube con N (empuja la vara ARRIBA) pero `sd` se estima solo de los
    puntuados, y si la deuda dispersara menos que ellos la vara bajaria. Los dos
    efectos tiran en sentido contrario y el neto DEPENDE DEL SUPUESTO. Esta funcion
    devuelve el supuesto explicito en vez de esconderlo:

        vara_puntuados   la vigente: sd(puntuados) * f(len(sr_intentos))
        vara_attempted   la de iter.85: sd(puntuados) * f(N_attempted)
                         — supone que la deuda dispersa COMO el pool
        sd_indiferencia  el sd que tendria que tener la deuda para que migrar no
                         mueva la vara. Debajo de esto, migrar la BAJA.
        vara_supuesta    si se pasa `sd_deuda`: la vara con ESE supuesto, mezclando
                         las varianzas de los dos grupos con medias iguales.

    Sigue siendo PURA: no lee disco y no importa nada. El `N_attempted` lo trae el
    llamador (`intentos.N_attempted()[0]`), que es quien puede saberlo.
    """
    n_p = len(sr_intentos)
    if n_p < 3:
        return None
    sd_p = st.pvariance(sr_intentos) ** 0.5
    if sd_p <= 0:
        return None
    N_a = max(int(N_attempted), n_p)
    n_d = N_a - n_p
    out = {"n_puntuados": n_p, "n_deuda": n_d, "N_attempted": N_a, "sd_puntuados": sd_p,
           "vara_puntuados": vara(sr_intentos, n_p),
           "vara_attempted": vara(sr_intentos, N_a),
           "sd_indiferencia": None, "vara_supuesta": None, "sd_deuda": sd_deuda}
    if n_d > 0:
        f_a = _f_de_N(N_a)
        # sd* tal que sd_mezcla(sd*) * f(N_a) == vara_puntuados
        objetivo = out["vara_puntuados"] / f_a
        resto = N_a * objetivo ** 2 - n_p * sd_p ** 2
        out["sd_indiferencia"] = math.sqrt(resto / n_d) if resto > 0 else 0.0
        if sd_deuda is not None:
            mez = math.sqrt((n_p * sd_p ** 2 + n_d * float(sd_deuda) ** 2) / N_a)
            out["sd_mezcla"] = mez
            out["vara_supuesta"] = mez * f_a
            out["migrar_baja_la_vara"] = out["vara_supuesta"] < out["vara_puntuados"]
    return out


def _f_de_N(N):
    """__MIGRACION_VARA_2026_09_21__ — el factor de orden extremo de vara(), solo."""
    return (1 - EULER) * _nppf(1 - 1.0 / N) + EULER * _nppf(1 - 1.0 / (N * math.e))


# __FACTIBILIDAD_VARA_2026_09_22__  (iter.128)
#
# EL PROBLEMA. Un `DSR 0,0 %` se lee como "la cohorte no alcanza". Puede querer decir
# otra cosa: que la VARA quedo fuera del conjunto de Sharpe que la MISION misma define
# como exito, y entonces el 0,0 % no informa NADA sobre la cohorte — habria dado 0,0 %
# con cualquier cohorte, incluida una que cumpliera la meta al pie de la letra.
# Medido en iter.128 y esa es la razon de que esto exista:
#   · MISION §1 pide WR >= 70 %. Para un pago de dos puntos, el Sharpe POR OPERACION
#     tiene un SUPREMO DURO en `sqrt(p/(1-p))`, que con p=0,70 vale +1,5275. No es una
#     cota empirica: es aritmetica, y no la mueve ni el R:R ni el tamaño de muestra.
#   · La vara vigente el 2026-09-22 es +2,1614 > +1,5275. O sea que la condicion 2 de
#     MISION §2 rechazaba la meta de MISION §1 con probabilidad 1.
#   · La meta literal de la casa (WR 70 %, R:R 1,86, E=+1,00 %/op) produce un Sharpe por
#     operacion de +0,7645 — 2,83 veces por debajo de su propia vara, y ya estaba por
#     debajo de la vara ANTERIOR (+0,8365, 1,09x). El DSR nunca pudo aprobar la meta.
#
# QUE HACE Y QUE NO HACE. NO baja la vara, NO toca `vara()`, NO cambia un veredicto.
# Devuelve la cuenta para que un `DSR` fallado se pueda leer. Va en MODO AVISO, DESPUES
# de fijar `faltas`/`real`, con la misma disciplina que `contraste` (iter.59), `reserva`
# (iter.110), `mitades` (iter.111), `sello` (iter.113) y `sello_vara` (iter.115):
# septima vez. Levantarlo a falta es decision HUMANA.
#
# PURA a proposito, como el resto de `robustez`: no lee disco y no importa nada del repo.
WR_MISION = 0.70                 # MISION §1, literal
RR_MISION = (1.86, 2.47, 3.29)   # la tabla de MISION §1, literal


def sharpe_dos_puntos(p, r):
    """Sharpe POR OPERACION de un pago `+r` con prob `p` y `-1` con prob `1-p`.
    Invariante de escala: el notional no entra, por eso `E` tampoco."""
    if not (0.0 < p < 1.0) or r <= 0:
        return None
    return (p * r - (1.0 - p)) / (math.sqrt(p * (1.0 - p)) * (r + 1.0))


def sup_sharpe(p):
    """El SUPREMO de `sharpe_dos_puntos(p, r)` sobre todo `r>0`, en el limite r->inf:

        (p*r - (1-p)) / (sqrt(p(1-p))*(r+1))  ->  p/sqrt(p(1-p))  =  sqrt(p/(1-p))

    COTA DURA: ninguna estrategia con tasa de acierto `p` puede tener un Sharpe por
    operacion mayor, con ningun R:R y con ninguna muestra."""
    if not (0.0 < p < 1.0):
        return None
    return math.sqrt(p / (1.0 - p))


def wr_minima(v):
    """La tasa de acierto minima que hace ALCANZABLE una vara `v`, aun con r->inf.
    Invierte `sqrt(p/(1-p)) = v`."""
    if v is None or v <= 0:
        return 0.0
    return (v * v) / (1.0 + v * v)


def wr_requerida(v, r):
    """La `p` que resuelve `sharpe_dos_puntos(p, r) = v`, por biseccion. None si no
    existe (no pasa con dos puntos, pero se chequea igual)."""
    if v is None:
        return None
    lo, hi = 1e-9, 1.0 - 1e-12
    if sharpe_dos_puntos(hi, r) < v:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        s = sharpe_dos_puntos(mid, r)
        if s is None or s < v:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def factibilidad(sr0, wr=WR_MISION, n=None, objetivo=UMBRAL_DSR):
    """
    ¿La vara `sr0` esta DENTRO del conjunto de Sharpe que la meta de la casa produce?

    `alcanzable is False` significa: **ninguna** estrategia con tasa de acierto `wr`
    puede pasar este DSR, a ningun R:R, con ninguna muestra. Un `dsr` fallado contra
    una vara asi **no es informacion sobre la cohorte**.

    `sr_requerido` incluye el termino de muestra cuando se pasa `n`, que es lo que de
    verdad hay que superar: `sr0 + z*sqrt(den)/sqrt(n-1)` con `den=1` de referencia.
    """
    if sr0 is None:
        return None
    sup = sup_sharpe(wr)
    out = {"sr0": sr0, "wr": wr, "sup_sharpe": sup,
           "alcanzable": (sup is not None and sr0 <= sup),
           "wr_minima": wr_minima(sr0),
           "sharpe_de_la_meta": sharpe_dos_puntos(wr, RR_MISION[0]),
           "wr_requerida": {r: wr_requerida(sr0, r) for r in RR_MISION}}
    out["holgura"] = (sup - sr0) if sup is not None else None
    if n and n > 1:
        req = sr0 + _nppf(objetivo) / math.sqrt(n - 1)
        out["n"] = n
        out["sr_requerido"] = req
        out["alcanzable_con_n"] = (sup is not None and req <= sup)
        out["wr_minima_con_n"] = wr_minima(req)
    return out


def cuantas_faltan(v, sr_intentos, objetivo=UMBRAL_DSR, N=None):
    """
    Cuantas operaciones harian falta para llegar al DSR objetivo manteniendo el Sharpe
    actual. Devuelve None si el Sharpe esta POR DEBAJO de la vara: en ese caso no
    llega nunca, por mas muestra que junte. Es la pregunta que evita esperar en vano.
    """
    d = deflactado(v, sr_intentos, N)   # __MIGRACION_VARA_2026_09_21__
    if not d or d["sr"] <= d["sr0"]:
        return None
    z = _nppf(objetivo)
    return int(math.ceil(1 + (z / (d["sr"] - d["sr0"])) ** 2))


def evaluar(v, sr_intentos, v_fuera=None, p5=None, *, unidad=None, N=None,
            corte_reserva=None, cache_hasta=None, fin_periodo=None,
            mitad_1=None, mitad_2=None, mitad_ts2_max=None, mitad_corte=None,
            sello_actual=None, sello_esperado=None,
            sello_vara_actual=None, sello_vara_esperado=None):
    """
    Veredicto completo. `v` son los retornos por operacion de la cohorte, `sr_intentos`
    los Sharpe de TODOS los intentos que se hicieron para llegar a ella, y `v_fuera`
    los retornos en datos que no participaron en elegirla.

    __UNIDAD_OBLIGATORIA_2026_09_21__ — `unidad` es OBLIGATORIO y keyword-only:
    QUE ES UN ELEMENTO de `v`, en el vocabulario UNIDADES. Keyword-only a proposito,
    para que ningun posicional suelto lo llene por accidente. No declararlo levanta
    UnidadNoDeclarada en la primera linea: un llamador que no sabe en que unidad esta
    midiendo no tiene derecho a un veredicto, ni siquiera a uno degradado.
    """
    if unidad is None:
        raise UnidadNoDeclarada(
            "evaluar() exige unidad=: decir QUE ES UN ELEMENTO de v. Vocabulario: %s. "
            "La vara se construye con Sharpe POR OPERACION (unidad='%s'); pasarle una "
            "serie por activo, por racimo o por dia es un error de CATEGORIA y no se "
            "puede detectar desde (n, sr) — medido en iter.57 (sensibilidad 0/6) y en "
            "iter.58 (5/18 sobre la poblacion completa)."
            % (", ".join(UNIDADES), UNIDAD_DE_LA_VARA))
    if unidad not in UNIDADES:
        raise UnidadNoDeclarada(
            "unidad=%r no esta en el vocabulario %s. Si ninguna describe lo que hay, "
            "usar 'desconocida' (suma falta) y NO forzar una que suene parecida."
            % (unidad, ", ".join(UNIDADES)))
    # __MIGRACION_VARA_2026_09_21__ — `N` enhebrado hasta vara(). `N_intentos` NO
    # cambia de significado (sigue siendo len(sr_intentos)): 43 veredictos guardados y
    # informe() lo leen, y redefinir un campo publicado es la falta de iter.50. El N que
    # de verdad se uso va en `N_usado`, al lado.
    d = deflactado(v, sr_intentos, N)
    out = {"n": len(v), "e": sum(v) / len(v) if v else None,
           "p5": p5, "dsr": d["dsr"] if d else None,
           "sr": d["sr"] if d else None, "sr0": d["sr0"] if d else None,
           "N_intentos": len(sr_intentos),
           "N_usado": len(sr_intentos) if N is None else int(N),
           "faltan": cuantas_faltan(v, sr_intentos, N=N)}
    if v_fuera and len(v_fuera) >= 20:
        ef = sum(v_fuera) / len(v_fuera)
        out["fuera_n"] = len(v_fuera); out["fuera_e"] = ef
        out["fuera_ok"] = (ef > 0 and out["e"] is not None and ef >= 0.5 * out["e"])
    else:
        out["fuera_ok"] = None
    faltas = []
    if p5 is not None and p5 <= 0:
        faltas.append("Monte Carlo p5 no es positivo")
    if out["dsr"] is None or out["dsr"] < UMBRAL_DSR:
        # __MIGRACION_VARA_2026_09_21__ — el texto es BIT A BIT el de antes cuando el
        # llamador no pasa N; si lo pasa, el mensaje dice los DOS numeros, porque
        # "elegida entre 154" y "elegida entre 255" no son la misma afirmacion.
        if N is None:
            faltas.append("Sharpe deflactado %.1f%% < 95%% (elegida entre %d intentos)"
                          % (100 * (out["dsr"] or 0), len(sr_intentos)))
        else:
            faltas.append("Sharpe deflactado %.1f%% < 95%% (elegida entre %d intentos "
                          "ATTEMPTED, %d con Sharpe)"
                          % (100 * (out["dsr"] or 0), out["N_usado"], len(sr_intentos)))
    if out["fuera_ok"] is False:
        faltas.append("fuera de muestra %+.3f%%/op contra %+.3f%% adentro"
                      % (out["fuera_e"], out["e"]))
    if out["fuera_ok"] is None:
        faltas.append("sin prueba fuera de muestra")
    # __UNIDAD_OBLIGATORIA_2026_09_21__ — la declaracion del llamador, guardada en el
    # veredicto y convertida en falta si no es la unidad de la vara. Va ANTES de la
    # red de seguridad: si el llamador ya confeso que no es por operacion, el motivo
    # que vale es su confesion, no la heuristica del techo.
    out["unidad"] = unidad
    if unidad != UNIDAD_DE_LA_VARA:
        faltas.append(
            "UNIDAD DECLARADA `%s`: la vara es Sharpe POR OPERACION (`%s`). "
            "Comparar contra ella una serie por %s es un error de categoria, no un "
            "resultado extraordinario." % (unidad, UNIDAD_DE_LA_VARA, unidad))
    # __VARA_HONESTA_43_2026_09_21__ — la compuerta tiene que ser SIMETRICA: el mismo
    # techo y el mismo piso que intentos.py le exige al pool, exigidos a la cohorte.
    motivos_unidad = unidad_ok(v)
    out["unidad_motivos"] = motivos_unidad
    out["unidad_ok"] = not motivos_unidad
    for m in motivos_unidad:
        faltas.append("UNIDAD: " + m)
    out["faltas"] = faltas
    out["real"] = not faltas
    # __CONTRASTE_UNIDAD_2026_09_21__ — DESPUES de fijar `faltas` y `real`, a
    # proposito y de manera visible: el contraste es una SOSPECHA FECHADA, no una
    # falta. No puede cambiar un veredicto ni por accidente. Probado en iter.59
    # sobre 3.000 casos: `faltas` y `real` identicos con y sin el parche.
    out["contraste"] = contraste_unidad(unidad, v)
    # __RESERVA_CACHE_2026_09_22__ — DESPUES de fijar `faltas` y `real`, a proposito y
    # de manera visible. El gate arranca en MODO AVISO: no puede cambiar un veredicto
    # ni por accidente, igual que __CONTRASTE_UNIDAD__ de iter.59, y se prueba igual
    # (verify_reserva_cache_20260922.py: veredictos identicos campo a campo con y sin
    # el parche). Cuando se sepa cuantas cohortes rompe, la decision de pasarlo a
    # falta es HUMANA y se toma con el numero a la vista, no aca.
    out["reserva"] = reserva_alcanzable(corte_reserva, cache_hasta, fin_periodo)
    # __MITADES_CACHE_2026_09_22__ — DESPUES de fijar `faltas` y `real`, a proposito y
    # de manera visible, con la MISMA disciplina que `contraste` (iter.59) y `reserva`
    # (iter.110): MODO AVISO, fuera de `faltas`, sin poder cambiar `real` ni por
    # accidente. Probado en verify_mitades_20260922.py, veredicto por veredicto y
    # campo por campo, con y sin el parche. Levantarla a falta es decision HUMANA:
    # hoy la casa no declara mitades en NINGUN llamador de `evaluar()`, asi que
    # levantarla de entrada seria apagar la casa — el mismo argumento de iter.110.
    out["mitades"] = mitades(mitad_1, mitad_2, ts2_max=mitad_ts2_max,
                             corte=mitad_corte, fin_periodo=fin_periodo)
    # __SELLO_PORTADOR_2026_09_22__ — DESPUES de fijar `faltas` y `real`, a proposito y
    # de manera visible, con la MISMA disciplina que `contraste` (iter.59), `reserva`
    # (iter.110) y `mitades` (iter.111): MODO AVISO, fuera de `faltas`, sin poder
    # cambiar `real` ni por accidente. Probado en verify_sello_portador_20260922.py
    # veredicto por veredicto y campo por campo, con y sin el parche. Levantarlo a
    # falta es decision HUMANA — y hoy seria apagar la casa: 0 de los llamadores vivos
    # de `evaluar()` declaran un sello, que es el mismo argumento de iter.110.
    out["sello"] = sello(sello_actual, sello_esperado)
    # __SELLO_VARA_2026_09_22__ — la MISMA disciplina, la sexta vez: DESPUES de fijar
    # `faltas` y `real`, en MODO AVISO, fuera de `faltas`, sin poder cambiar `real` ni
    # por accidente. Probado en verify_sello_vara_20260922.py veredicto por veredicto y
    # campo por campo, con y sin el parche. Levantarlo a falta es decision HUMANA.
    #
    # LA COMODIDAD QUE SE CABLEA ACA, Y ES DELIBERADA: si el llamador NO pasa
    # `sello_vara_actual`, se calcula solo con los mismos `sr_intentos` y el mismo `N`
    # que la funcion ya uso para la vara. No hay excusa de "no tenia los datos": el
    # sello de la vara sale de lo que `evaluar()` YA recibio. Lo unico que el llamador
    # tiene que aportar es la REFERENCIA (`sello_vara_esperado`), porque esa es la que
    # el veredicto viejo deberia haber guardado y ninguno guardo (0 de 113, iter.115).
    if sello_vara_actual is None:
        try:
            sello_vara_actual = sello_vara_de(sr_intentos, N)
        except Exception:
            sello_vara_actual = None
    out["sello_vara"] = sello_vara(sello_vara_actual, sello_vara_esperado)
    # __FACTIBILIDAD_VARA_2026_09_22__ — la MISMA disciplina, la septima vez: DESPUES de
    # fijar `faltas` y `real`, en MODO AVISO, fuera de `faltas`, sin poder cambiar `real`
    # ni por accidente. Probado en verify_factibilidad_vara_20260922.py veredicto por
    # veredicto y campo por campo, con y sin el parche. Levantarlo a falta es decision
    # HUMANA — y hoy seria apagar la casa al reves: con la vara en +2,1614 TODA cohorte
    # tendria esta falta, incluida una que cumpliera la meta de MISION §1 exactamente.
    # Lo que contesta: si el `dsr` fallado de arriba dice algo sobre LA COHORTE o solo
    # dice que la vara quedo fuera del conjunto factible que la propia MISION define.
    out["factibilidad"] = factibilidad(out.get("sr0"), n=out.get("n"))
    return out


def informe(nombre, r):
    print("  %s" % nombre)
    # __UNIDAD_OBLIGATORIA_2026_09_21__ — la unidad se IMPRIME siempre. Un numero sin
    # unidad al lado es como se publicaron 18 de los 43 veredictos de la casa.
    u = r.get("unidad")
    if u is not None and u != UNIDAD_DE_LA_VARA:
        print("     [!] unidad declarada: %s — NO es la unidad de la vara (%s)"
              % (u, UNIDAD_DE_LA_VARA))
    print("     n=%d · neto %+.3f%%/%s · Sharpe %+.4f · vara %+.4f · DSR %.1f%% (%d intentos)"
          % (r["n"], r["e"] or 0, u or "op", r["sr"] or 0, r["sr0"] or 0,
             100 * (r["dsr"] or 0), r["N_intentos"]))
    # __MIGRACION_VARA_2026_09_21__ — si el llamador declaro un N attempted distinto de
    # los puntuados, se imprime: el denominador de la vara es parte del veredicto.
    if r.get("N_usado") is not None and r["N_usado"] != r["N_intentos"]:
        print("     N attempted declarado: %d (%d con Sharpe, %d de deuda)"
              % (r["N_usado"], r["N_intentos"], r["N_usado"] - r["N_intentos"]))
        # __ARCHIVO_MUERTO_2026_09_21__ (iter.89) — LA DEUDA SE PUBLICA COMO
        # PERMANENTE, NO COMO PENDIENTE DE COSECHA.
        # Entre iter.85 e iter.88 esta casa publico el hueco entre N attempted y
        # N puntuado como si fuera transitorio, y dejo tareas abiertas que suponian
        # que algun dia se cosecha. Las dos mediciones que lo cierran:
        #   · iter.88 — re-correr los scripts cerrados recupera 0 de 94. Los que
        #     reprodujeron su .json BIT A BIT recuperaron cero igual, porque el
        #     .json no tiene la serie por operacion y sin serie no hay Sharpe.
        #     La deuda es un problema de FORMATO DE LO GUARDADO, no de ejecucion.
        #   · iter.89 — censo de ejecutabilidad de las 93 versiones archivadas
        #     (`_archivo_muerto_evidencia/corridas.json`). Aun si corriesen 93 de
        #     93, no pagaria un solo item, por lo anterior.
        # Esta linea es ADITIVA: no cambia `sr0`, ni `dsr`, ni `real`, ni ningun
        # numero que la vara produce. Solo dice lo que el numero significa.
        # La regla hacia adelante vive en `cierre.py`.
        print("     esa deuda es PERMANENTE (iter.88: re-correr recupera 0 de 94; "
              "sin serie por operacion no hay Sharpe). La vara ya la cuenta en N; "
              "no queda nada que cosechar. Regla hacia adelante: cierre.py")
    if r.get("fuera_n"):
        print("     fuera de muestra: n=%d · %+.3f%%/op" % (r["fuera_n"], r["fuera_e"]))
    if r["faltan"]:
        print("     necesitaria %d operaciones para llegar al 95%%" % r["faltan"])
    elif r["sr"] is not None and r["sr0"] is not None and r["sr"] <= r["sr0"]:
        print("     su Sharpe esta POR DEBAJO de la vara: no llega nunca, no es esperar")
    if r["real"]:
        print("     -> APTA PARA REAL")
    else:
        print("     -> PAPEL. Falta: %s" % " · ".join(r["faltas"]))
    # __CONTRASTE_UNIDAD_2026_09_21__ — la sospecha se imprime DESPUES del veredicto
    # y sin cambiarlo. Va al final justo para que se lea como lo que es: una duda
    # sobre la DECLARACION, no un motivo mas de la lista de faltas.
    for c in r.get("contraste") or []:
        print("     [?] CONTRASTE DE UNIDAD · %s" % c)
    # __RESERVA_CACHE_2026_09_22__ — el aviso de la condicion 4 se imprime DESPUES del
    # veredicto y sin cambiarlo, por la misma razon que el contraste: es una auditoria
    # de la DECLARACION del llamador, no un motivo mas de la lista de faltas.
    res = r.get("reserva") or {}
    if res.get("aviso"):
        print("     [!] CONDICION 4 (fuera de muestra en TIEMPO) · %s · %s"
              % (res.get("estado"), res["aviso"]))
    elif res.get("estado") == "ALCANZABLE":
        al = res.get("alcance")
        print("     condicion 4: reserva ALCANZABLE%s (cache hasta %s, corte %s)"
              % ("" if al is None else " (%.1f %% de la ventana)" % (100 * al),
                 res.get("cache_hasta"), res.get("corte_reserva")))
    # __MITADES_CACHE_2026_09_22__ — el n de CADA MITAD, AL LADO DEL SIGNO. Hoy ese n
    # no viaja: las cohortes publican «mismo signo en las dos mitades» y el lector no
    # tiene como saber si la 2a mitad tenia 640 operaciones o 30. Se imprime siempre
    # que el llamador declare mitades, y DESPUES del veredicto, sin tocarlo.
    mit = r.get("mitades") or {}
    if mit.get("n1"):
        print("     mitades: 1a n=%d %+.4f (t %+.2f)  ->  2a n=%d %+.4f (t %+.2f)"
              "  n2/n1=%.2f%s  -> %s"
              % (mit["n1"], mit["e1"], mit["t1"] or 0, mit["n2"], mit["e2"],
                 mit["t2"] or 0, mit["razon_n"] or 0,
                 "" if mit.get("cobertura_2a") is None
                 else "  cob2=%.1f %%" % (100 * mit["cobertura_2a"]),
                 mit.get("estado")))
    if mit.get("aviso"):
        print("     [!] %s" % mit["aviso"])
    # __SELLO_PORTADOR_2026_09_22__ — el sello se imprime DESPUES del veredicto y sin
    # tocarlo, por la misma razon que los tres anteriores. Lo que agrega al informe es
    # DE QUE CONJUNTO salio el numero: hoy una cifra viaja con su `n` y con nada mas,
    # asi que dos corridas con 1.285 operaciones distintas se leen igual.
    sel = r.get("sello") or {}
    act = sel.get("actual") or {}
    if act:
        print("     sello del portador: n=%s · max_ts=%s · ids=%s · filas=%s  -> %s"
              % (act.get("n"), act.get("max_ts"), (act.get("sha_ids") or "")[:12],
                 (act.get("sha_filas") or "")[:12], sel.get("estado")))
    if sel.get("aviso"):
        print("     [!] SELLO · %s · %s" % (sel.get("estado"), sel["aviso"]))
    # __SELLO_VARA_2026_09_22__ — el sello de la VARA se imprime ultimo y sin tocar el
    # veredicto, por la misma razon que los cuatro anteriores. Lo que agrega es CONTRA
    # QUE UMBRAL se juzgo el numero: hoy un DSR viaja con su `sr0` y sin `sd` ni pool,
    # y ese `sr0` reconstruye desde `N` en 3 de 92 casos (iter.115).
    sv = r.get("sello_vara") or {}
    sva = sv.get("actual") or {}
    if sva:
        print("     sello de la vara: N=%s (punt. %s) · sd=%s · sr0=%s · pool=%s  -> %s"
              % (sva.get("N"), sva.get("N_puntuados"),
                 "%.10f" % sva["sd"] if sva.get("sd") is not None else "-",
                 "%.10f" % sva["sr0"] if sva.get("sr0") is not None else "-",
                 (sva.get("sha_pool") or "")[:12], sv.get("estado")))
    if sv.get("aviso"):
        print("     [!] SELLO DE VARA · %s · %s" % (sv.get("estado"), sv["aviso"]))


def main():
    print("=" * 84)
    print("robustez.py — la vara para decidir si algo se opera en real")
    print("=" * 84)
    print(__doc__.split("USO")[0].split("POR QUE EXISTE")[1].strip())
    print()
    print("  Ejemplo con el caso real de esta sesion (SHORT + FVG + IFVG):")
    import random
    r = random.Random(1)
    # se reproduce el orden de magnitud del caso para ilustrar el uso
    dentro = [2.0 - 0.11 if r.random() < 0.566 else -1.11 for _ in range(53)]
    fuera = [2.0 - 0.11 if r.random() < 0.302 else -1.11 for _ in range(43)]
    intentos = [r.gauss(0, 0.12) for _ in range(43)]
    informe("SHORT FVG + IFVG", evaluar(dentro, intentos, fuera, p5=0.232, unidad="op"))
    print()
    print("  __UNIDAD_OBLIGATORIA_2026_09_21__ — y el mismo veredicto si el llamador")
    print("  confiesa que su serie es por ACTIVO (iter.58: 9 de 36 sitios lo eran):")
    informe("el mismo, declarado por activo",
            evaluar(dentro, intentos, fuera, p5=0.232, unidad="activo"))
    print()
    print("  __CONTRASTE_UNIDAD_2026_09_21__ — y la SOSPECHA cuando el llamador firma")
    print("  `op` sobre una serie con la forma del recuento de activos de la casa.")
    print("  Es el caso REAL de bt_m2_liq_ok.py, que iter.58 firmo `op` siendo `activo`:")
    informe("25 activos firmados como 25 operaciones",
            evaluar(dentro[:25], intentos, fuera, p5=0.232, unidad="op"))


if __name__ == "__main__":
    main()
