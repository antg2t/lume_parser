# R09 — PDF escaneado e imagens com OCR

## Contexto e objetivo

Esta release só começa após receber no mínimo cinco documentos escaneados reais, incluindo variações de resolução, rotação e qualidade. Todo o código de orquestração será Python; Tesseract 5 local é a dependência de reconhecimento.

O objetivo é gerar texto rastreável para que os parsers documentais existentes possam operar sobre PDFs sem camada textual e imagens.

## Implementação planejada

- Executar preflight e usar OCR somente em páginas sem texto suficiente ou com camada inválida.
- Renderizar PDFs em 300 DPI e aceitar PNG, JPEG e TIFF.
- Aplicar rotação, deskew, escala, contraste, binarização, remoção de ruído e bordas apenas quando as métricas indicarem necessidade.
- Executar Tesseract em português, configurando segmentação por perfil de documento.
- Preservar texto, caixas, página e confiança por palavra.
- Testar mais de uma configuração de preprocessamento sem escolher resultado apenas pelo volume de texto.
- Entregar o texto ao classificador e parser documental normal; OCR não interpreta campos fiscais ou bancários.
- Manter texto nativo e OCR separados em documentos híbridos.

## Testes

- Documento reto, rotacionado, baixa resolução, fundo irregular e múltiplas páginas.
- Página híbrida com texto nativo e imagem.
- CNPJ, datas, códigos e valores com caracteres visualmente semelhantes.
- Comparação por caractere e por campo contra transcrição manual.

## Critérios de aceite

- Cada fixture possui transcrição ou golden de campos críticos.
- O OCR preserva página, região e confiança.
- O sistema não aciona OCR nos 19 PDFs digitais atuais.
- Resultados de baixa confiança geram warning e nunca completam campos silenciosamente.
