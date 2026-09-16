# Lume — handoff do motor de pré-contabilização

Este pacote é a referência técnica para evoluir a Lume de uma suíte local de
ingestão para um produto de **pré-contabilização e conciliação assistida**. Ele
contém a suíte Python atualizada, contratos normalizados, testes e o contexto
de produto necessário para orientar a IA da aplicação.

Os documentos originais dos clientes e os resultados gerados não estão neste
handoff. Eles podem conter dados financeiros e pessoais e devem ficar em
armazenamento privado, segregado por empresa. O código e os testes usam esses
arquivos somente quando um acervo autorizado é disponibilizado localmente.

## Comece por aqui

1. Leia [Produto, jornada e adequações](docs/PRODUTO_JORNADA_E_ADEQUACOES.md).
2. Leia [Contexto para a IA do produto](docs/CONTEXTO_PARA_IA_DO_PRODUTO.md).
3. Consulte [o guia do motor contábil](docs/GUIA-MOTOR-CONTABIL-DETERMINISTICO.md).
4. Instale e rode a suíte conforme a seção abaixo.

O direcionamento de produto consolidado nos dois primeiros documentos vem da
conversa compartilhada pelo time: <https://chatgpt.com/share/6aa84691-3b98-83e9-8a46-ea96071db731>.

## O que está funcional neste snapshot

| Capacidade | Situação |
|---|---|
| NFS-e Nacional XML e DANFSe PDF textual | Normaliza e valida |
| Controle de caixa XLSX e PDF | Normaliza, reconcilia e compara os formatos |
| Extrato Itaú PDF e XLSX tabular | Normaliza para `BankStatement` |
| Extrato Bradesco PDF e Net Empresa XLS | Normaliza para `BankStatement` |
| Plano de contas e histórico contábil XLSX | Normaliza para contratos próprios |
| Motor de lançamentos | Regras, memória histórica local, fallback global e fila de revisão |
| Avaliação do fallback global | Top-1, top-3, cobertura e precisão automática por cliente |
| Regressão | 95 testes aprovados neste snapshot, com acervo local autorizado |

O formato de saída do motor é `lancamentos.json` mais `report.md`. Exportadores
para Contimatic, Domínio, Alterdata e formatos finais XLSX ainda são uma
adequação de produto, não uma capacidade já entregue.

## Instalação e verificação

Requisitos: Python 3.12+ e Poetry 1.8+.

```powershell
poetry install
poetry run pytest
```

O acervo real não acompanha este handoff. Para os testes de integração e os
ciclos contábeis, disponibilize cópias autorizadas em uma pasta local, mantendo
os arquivos fechados no Excel/OneDrive. Nunca altere um original para fazê-lo
passar em um teste.

```powershell
poetry run python -m lume_ingestion batch "C:\acervo\CLIENTE" --output "C:\saida\importacao"

poetry run python -m lume_ingestion.accounting_entries `
  "C:\acervo\CLIENTE" `
  "C:\acervo\Plano de Contas.xlsx" `
  --period 2026-08 `
  --global-model "C:\artefatos\global_account_model\2026-08" `
  --output "C:\saida\CLIENTE\2026-08"
```

O segundo comando aceita arquivos ou pastas e percorre diretórios de forma
recursiva. A pasta de saída contém o JSON auditável, relatório e os artefatos
de importação por hash.

## Limites deliberados

- Não há API, banco, autenticação, tela de revisão, exportador final nem
  dashboard neste pacote.
- OCR só entra quando a página não tem texto útil (CID sem ToUnicode ou
  impressão PDFCreator em paths/imagem). Não é OCR genérico de NFS-e. Os
  adapters de extrato continuam `itau-digital-bank-statement-v1` e
  `bradesco-bank-statement-v1`.
- Não há OFX/QFX, CSV genérico ou suporte universal a XLS/XLSX. Cada novo
  layout precisa de um adaptador reconhecido por conteúdo e coberto por
  fixture.
- O fallback atual é TF-IDF local/versionado, não um LLM. Toda classificação
  abaixo do threshold permanece em revisão.
- Uma planilha final do mesmo mês só é referência de avaliação: nunca pode
  alimentar o treinamento ou a classificação daquele próprio mês.

## Estrutura

```text
src/lume_ingestion/       # ingestão, normalização, motor e avaliação
tests/                    # contratos, regressão e fixtures autorizadas
config/                   # manifesto do treino global
docs/                     # produto, operação e decisões técnicas
```

O ponto de integração do importador é `lume_ingestion.pipeline.run_pipeline`.
O ponto de geração contábil é `lume_ingestion.accounting_entries.run_cycle`.
Consuma `result.data`, `normalized.json` e `lancamentos.json`; o `raw.json` é
evidência de auditoria, não contrato da interface do produto.
