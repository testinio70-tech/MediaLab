# Modelos locales de restauración

MediaLab incluye tres modelos ONNX publicados por OpenCV Zoo. Se ejecutan
localmente con OpenCV DNN; ningún fotograma se envía a un servicio externo.

## PP-OCRv3 text detection

- Archivo: `text_detection_en_ppocrv3_2023may.onnx`
- Origen: <https://github.com/opencv/opencv_zoo/tree/main/models/text_detection_ppocr>
- Licencia: Apache License 2.0
- SHA-256: `03F550C6B406FDA8BF54BD8327815F6C7E2EDD98CEA02348C93D879254366587`

El modelo delimita regiones con texto. MediaLab refina esas regiones y descarta
cualquier píxel que coincida con la máscara humana protegida.

## PP-HumanSeg

- Archivo: `human_segmentation_pphumanseg_2023mar.onnx`
- Origen: <https://github.com/opencv/opencv_zoo/tree/main/models/human_segmentation_pphumanseg>
- Licencia: Apache License 2.0
- SHA-256: `552D8A984054E59B5D773D24B9B12022B22046CEB2BBC4C9AAEACEB36A9DDF24`

El modelo genera la silueta de protección. MediaLab amplía esa silueta para
incluir bordes de cabello y extremidades. Los modelos no reconstruyen caras,
piel ni anatomía.

## YuNet

- Archivo: `face_detection_yunet_2023mar.onnx`
- Origen: <https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet>
- Licencia: MIT
- SHA-256: `8F2383E4DD3CFBB4553EA8718107FC0423210DC964F9F4280604804ED2552FA4`

YuNet crea una segunda máscara alrededor del rostro. Ambos modos evitan usar
LaMa sobre esa máscara y recurren a interpolación local si un texto la cruza.
En Restauración IA HD, MediaLab reduce casi por completo la contribución de
baja frecuencia del superescalador y permite solamente microdetalle de fuerza
baja.

## Modelos grandes instalados localmente

`setup_restore_models.py` instala en `Tools/Models` dos modelos grandes que no
se guardan en Git:

- LaMa de OpenCV para reconstruir únicamente los trazos de texto que cruzan una
  persona.
- Real-ESRGAN 2× para superresolución controlada y mezclada con el fotograma
  real.

Los dos archivos se descargan por HTTPS, se verifican mediante SHA-256 y
permanecen en el equipo. Ningún fotograma se sube a internet.

El texto completo de la licencia aplicable se conserva en
`LICENSE-APACHE-2.0.txt`.
