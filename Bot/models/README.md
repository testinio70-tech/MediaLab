# Modelos locales de restauración

MediaLab incluye dos modelos ONNX publicados por OpenCV Zoo. Se ejecutan
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

El texto completo de la licencia aplicable se conserva en
`LICENSE-APACHE-2.0.txt`.
