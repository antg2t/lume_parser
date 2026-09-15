# Acervo externo autorizado

Não versionar nem enviar documentos reais neste pacote. Copie versões
autorizadas para `docs/` apenas no ambiente privado que executará os testes.

## Estrutura esperada

```text
docs/
  Nota fiscal/{pdf,xml}/
  Controle de caixa/{pdf,xlsx}/
  Extrato/{pdf,xlsx}/
  Plano de conta/
  Historico contabil/
  Clientes/
    Vanguarda/
    LUFTKLIM/
    Martine/
```

Os testes dos adaptadores bancários tabulares usam, no acervo autorizado, um
XLSX do Itaú para LUFTKLIM e um XLS Net Empresa do Bradesco para Martine. O
arquivo de Martine pode conter um segundo período; o adaptador deve isolar o
primeiro lote pelo segundo cabeçalho, sem excluir o original.

## Rotina segura

1. Mantenha os originais fora do Git e do pacote enviado a ferramentas de IA.
2. Feche Excel e OneDrive antes de executar a suíte.
3. Rode `poetry run pytest` para contratos e regressões.
4. Rode a geração apenas com competência explícita e modelo treinado antes dela.
5. Guarde `raw.json`, `normalized.json`, `result.json` e `lancamentos.json`
   como evidência do lote; não sobrescreva o original.
