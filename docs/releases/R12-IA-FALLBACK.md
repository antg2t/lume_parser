# R12 — IA como fallback controlado

## Contexto e objetivo

Esta é a última release. Ela só começa depois que os parsers determinísticos e o OCR tiverem métricas reais de falha. IA não substitui os fluxos locais que já atingirem os critérios de aceite.

O objetivo é recuperar campos de documentos difíceis com custo, privacidade, evidência e qualidade mensuráveis.

## Implementação planejada

- Definir um gate objetivo por tipo: parser incompatível, campos críticos ausentes ou confiança abaixo do limite medido.
- Enviar apenas texto, páginas ou regiões necessárias; evitar documento completo quando possível.
- Usar contrato Pydantic específico do tipo documental e resposta estritamente estruturada.
- Exigir para cada campo valor, evidência, página/região e confiança.
- Converter ausência em `null`; proibir preenchimento por conhecimento externo.
- Validar CNPJ/CPF, datas, valores e totais depois da resposta.
- Manter resultado da IA separado do RAW determinístico e registrar modelo, versão, latência, tokens e custo.
- Não enviar documentos sem configuração explícita de provedor e política de dados.
- Permitir execução totalmente local com IA desativada.

## Testes

- Casos já resolvidos deterministicamente para comprovar que o gate não chama IA.
- Documentos degradados com goldens manuais.
- Resposta inválida, campo inventado, timeout, indisponibilidade e limite de custo.
- Repetição para medir variação e precisão por campo.

## Critérios de aceite

- A IA melhora de forma mensurável a precisão dos casos difíceis.
- Nenhum campo sem evidência é aceito.
- Custo e latência ficam registrados por documento.
- Falha do provedor não impede a preservação dos resultados locais.
- O laboratório funciona integralmente com o fallback desabilitado.
