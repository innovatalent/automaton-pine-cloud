# AUTOMATON: generador preparado para Render

El servidor ejecuta el calculo y ofrece `/AUTOMATON_ACTUAL.pine`, `/api/tv_pine_full`
y `/status`. No consulta el dashboard de la PC. La plantilla Pine se conserva.

Estado: pendiente de primera generacion remota completa. El worker espera hasta
el 2/11/2026 a las 22:50 UTC antes de consultar fuentes, respetando el bloqueo
de Binance confirmado hasta las 22:47:47 UTC.

Un solo proceso calcula y conserva caches entre ciclos, con 60 segundos de pausa
entre generaciones. NO garantiza un archivo nuevo cada minuto. Conserva timestamps
originales y exige gamma reciente BTC/ETH. Respeta bloqueos y Retry-After.
Solo permite descargar archivos con hash correcto y antiguedad menor a 15 minutos.

No incluye credenciales, posiciones de cuenta ni bases historicas privadas del
motor. Las funciones que dependian de esas bases pueden no tener resultado.
Render Free puede dormir o reiniciar y perder almacenamiento; no hay garantia 24/7.
Pegar el nuevo Pine en TradingView sigue siendo manual.

`generator_deployed: true` significa codigo instalado, no generacion exitosa.
La migracion se valida con dos generaciones remotas sucesivas y archivos correctos.

Tests: `python -m unittest test_cloud -v`. CI construye imagen Linux y verifica
imports, descargas y bloqueos.

## Prueba inicial (version anterior)

Esta version solo comprueba conectividad publica desde Render con Bybit,
Binance, Deribit y Yahoo. NO genera Pine ni reemplaza la publicacion local.
No contiene claves, posiciones ni datos privados de cuentas.

[Desplegar prueba gratuita en Frankfurt](https://render.com/deploy?repo=https://github.com/innovatalent/automaton-pine-cloud)

El blueprint selecciona exclusivamente `plan: free`. No aceptar cambios a un
plan de pago. Tras desplegar, abrir la URL del servicio y revisar `/status`.
`all_sources_ok: true` indica respuesta con datos de las cuatro consultas,
no garantiza aun que el generador completo funcione.

Si el flujo Blueprint solicita tarjeta, intentar New > Web Service con este
repositorio publico, runtime Docker, region Frankfurt y plan Free.

Render Free puede suspenderse por inactividad; no se promete servicio continuo.
La generacion y su frecuencia siguen pendientes de validacion.
