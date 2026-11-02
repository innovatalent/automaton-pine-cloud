# AUTOMATON: prueba de alojamiento

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
