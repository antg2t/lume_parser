# R02 — NFS-e/DANFSe em PDF

## Contexto e objetivo

Pré-requisito: extração PDF da R00 e modelo `FiscalDocument` estabilizado na R01. Há 17 DANFSe digitais, todos com uma página e texto nativo. Eles não correspondem aos quatro XMLs atuais, portanto não serão tratados como pares.

O objetivo é interpretar o layout nacional atual e produzir o mesmo modelo fiscal usado pelo XML.

## Contrato documental

Extrair chave, número da NFS-e e DPS, série, emissão, competência, situação, prestador, tomador, município, códigos do serviço, NBS, descrição, valores, ISS, retenções e IBS/CBS quando impresso. Todo campo registra página, região ou trecho de evidência.

## Implementação planejada

- Classificar por cabeçalho `DANFSe`, chave e rótulos internos, nunca pelo nome da pasta.
- Usar texto com coordenadas do `pdfplumber`; manter o texto do `pypdf` como diagnóstico comparativo.
- Criar regras por âncoras visuais e blocos, tolerando rótulos colados e mudanças de espaçamento.
- Normalizar acentuação defeituosa, CNPJ/CPF, datas e valores brasileiros sem alterar o RAW.
- Tratar descrições longas e grupos opcionais sem deslocar os campos seguintes.
- Ler QR Code apenas como verificação opcional da chave, nunca como fonte exclusiva.
- Calcular completude e emitir warning quando campo obrigatório não tiver evidência confiável.

## Testes

- Golden annotation para cada um dos 17 PDFs.
- Variações de emitente, município, descrição, retenção e grupos tributários.
- Testes contra troca de ordem, espaços removidos, texto duplicado e campo ausente.
- Garantir que PDFs genéricos não sejam classificados como NFS-e.

## Critérios de aceite

- Dezessete de dezessete PDFs são classificados corretamente.
- Chave, número, datas, prestador, tomador, serviço e valores coincidem com as goldens.
- Nenhum documento atual utiliza OCR ou IA.
- Ausências e ambiguidades aparecem como warnings, não como valores inventados.
- A saída possui o mesmo contrato semântico da NFS-e XML.
