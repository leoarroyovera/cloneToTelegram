# cloneToTelegram

Respalda media (fotos, video, documentos, audio) y links de un grupo de Telegram con Topics hacia un supergrupo nuevo, replicando la misma estructura de temas.

## Setup

1. Instalar dependencias:
   ```
   pip install -r requirements.txt
   ```
2. Verificar que `.env` tenga `TG_API_ID` y `TG_API_HASH` (se obtienen en https://my.telegram.org).
3. La primera corrida pide login interactivo (teléfono, código, 2FA si aplica). Debe ejecutarse en una terminal real donde puedas escribir la respuesta — no funciona en un entorno sin stdin interactivo. La sesión queda guardada en `backup_session.session` y no se vuelve a pedir login en corridas siguientes.

## Uso

```
python main.py --source <grupo_origen> --dest-title "<Nombre del grupo nuevo>"
```

- `--source`: username o link (`t.me/...`) del grupo origen. Debe tener el modo Temas (Forum) activado.
- `--dest-title`: nombre del supergrupo nuevo a crear (opcional). Si se omite, se usa `<source>Respaldo`; si ese nombre ya existe entre tus chats, se le agrega un sufijo aleatorio de 3 dígitos. Solo se usa la primera vez; en corridas siguientes se reutiliza el grupo ya creado (guardado en el checkpoint).

### Opciones útiles

| Flag | Qué hace |
|---|---|
| `--dry-run` | Solo lista los topics del grupo origen, no crea ni sube nada. |
| `--only-topic <ID>` | Limita el proceso a un solo topic origen (para pruebas). |
| `--limit-per-topic <N>` | Limita la cantidad de mensajes procesados por topic (para pruebas). |
| `--dest-chat-id <ID>` | Reanuda contra un chat destino ya existente en vez de crear uno nuevo. |

## Flujo recomendado para respaldar un grupo

1. **Ver los topics del grupo origen**, sin escribir nada:
   ```
   python main.py --source mi_grupo --dry-run
   ```
2. **Piloto acotado**: probar con un topic pequeño y pocos mensajes antes de la corrida completa:
   ```
   python main.py --source mi_grupo --dest-title "MiGrupoRespaldo" --only-topic <ID> --limit-per-topic 20
   ```
   Verificar manualmente en Telegram que el topic se creó bien, que la media tiene su caption y nombre de archivo correcto, y que los links aparecen como texto plano (no forward).
3. **Corrida completa**, quitando los límites de prueba:
   ```
   python main.py --source mi_grupo --dest-title "MiGrupoRespaldo"
   ```

## Reutilizar para otro grupo

No hace falta tocar código. Solo cambiar `--source` y `--dest-title`:

```
python main.py --source otro_grupo --dest-title "OtroGrupoRespaldo"
```

Cada grupo origen tiene su propio archivo de checkpoint en `state/<nombre_origen>.json`, así que se pueden respaldar varios grupos sin que se pisen entre sí. Se recomienda repetir el flujo de dry-run → piloto → corrida completa por cada grupo nuevo.

## Resume / checkpoint

El progreso se guarda automáticamente después de cada mensaje procesado (`state/<nombre_origen>.json`): topics ya creados, último mensaje procesado por topic, y mensajes saltados (por ejemplo, por exceder el límite de tamaño de subida).

Si el proceso se interrumpe (Ctrl+C, corte de red, error) en cualquier momento, basta con volver a correr el mismo comando: retoma exactamente donde quedó, sin duplicar contenido ya subido.

## Qué se respalda

- **Media**: fotos, videos, documentos y audio, descargados y re-subidos como archivo nuevo (no forward), preservando caption y nombre de archivo original.
- **Links**: URLs encontradas en el texto o caption del mensaje, copiadas como mensaje de texto plano nuevo (no forward).
- Se excluyen stickers y notas de voz.
- Un mismo mensaje origen (por ejemplo, una foto con un link en el caption) puede generar tanto la subida de la media como el mensaje de texto con el link, ambos en el mismo topic destino.
- Archivos que excedan el límite de subida de la cuenta (2GB) se saltan y quedan registrados en el checkpoint (`skipped`), sin detener la corrida.

## Rendimiento

La subida de archivos usa múltiples conexiones TCP paralelas al datacenter de Telegram (`fast_upload.py`), más rápido que la subida estándar de Telethon. Tanto la descarga como la subida muestran progreso periódico (porcentaje, velocidad, ETA) para archivos grandes.

## Estructura del proyecto

```
main.py              CLI (argparse)
backup.py             orquestación: itera topics -> mensajes -> respalda media/links
topics.py             descubrir topics origen, crear supergrupo y topics destino
classify.py            clasificación de mensajes (media relevante / links)
state.py               checkpoint (lectura/escritura atómica)
telegram_client.py     cliente Telethon + manejo de FloodWait
fast_upload.py          subida paralela (múltiples conexiones TCP)
progress.py             log de progreso legible (velocidad, ETA)
config.py               carga de variables de entorno
```
