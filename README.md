# Lume ingestion — handoff

Pacote Python local e determinístico para extrair, normalizar e validar documentos contábeis. Ele não acessa rede, banco, IA, OCR ou os arquivos de origem em modo de escrita.

## O que está pronto

- NFS-e Nacional: XML e DANFSe PDF textual.
- Controle de caixa: XLSX e PDF, com comparação entre formatos.
- Extrato bancário Itaú PDF textual.
- Plano de contas XLSX: `chart_of_accounts`.
- Histórico contábil XLSX: `accounting_history`.
- Regressão por SHA-256, goldens e artefatos `raw.json`, `normalized.json` e `result.json`.

Os contratos normalizados ficam em `src/lume_ingestion/models.py`. O ponto de integração é `lume_ingestion.pipeline.run_pipeline(path, output_root)`; ele sempre retorna um `IngestionResult` estruturado, inclusive em falhas previsíveis.

## Instalação

Requer Python 3.12+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install .
python -m lume_ingestion --help
```

Para executar os testes, instale também as dependências de desenvolvimento:

```powershell
pip install . pytest==9.0.2
pytest
```

Alternativa equivalente com Poetry: `poetry install` e `poetry run pytest`.

## Uso na aplicação

```python
from lume_ingestion.pipeline import run_pipeline

result = run_pipeline(r"C:\entrada\Plano de Contas.xlsx", r"C:\saida")
if result.success:
    payload = result.data
else:
    error = result.errors[0].model_dump()
```

Ou use a CLI para um arquivo ou uma pasta:

```powershell
python -m lume_ingestion batch "C:\entrada" --output "C:\saida"
```

Cada arquivo recebe uma pasta de saída baseada no hash. Nunca use o arquivo RAW como contrato da aplicação; consuma `result.data` ou o `normalized.json`.

## Acervo de regressão (não incluído)

Os documentos reais não fazem parte deste pacote porque podem conter dados sensíveis. Para executar a regressão R05, copie o acervo para `docs/` preservando as subpastas abaixo e execute:

```powershell
python -m lume_ingestion regress docs
```

```text
docs/
  Nota fiscal/{pdf,xml}/
  Controle de caixa/{pdf,xlsx}/
  Extrato/{pdf,xlsx}/
  Plano de conta/
  Historico contabil/
```

O manifesto e as goldens já estão em `tests/`. Um arquivo bloqueado pelo Excel/OneDrive retorna `file_unreadable`; feche o programa que o mantém aberto antes de rodar a regressão completa.

## Próximo passo para a outra IA

1. Instalar e rodar `python -m lume_ingestion --help`.
2. Rodar `pytest` após disponibilizar o acervo; não alterar goldens para “fazer passar” uma regressão.
3. Integrar `run_pipeline` atrás do adaptador/API da aplicação, gravando os três artefatos por hash.
4. Para novos layouts tabulares, seguir `docs/releases/R07-TABULARES.md`; não misturar classificação de débito/crédito ou IA ao parser determinístico.

As decisões e a baseline atual estão em `docs/releases/R05-REGRESSAO-ACERVO.md`. O experimento de classificação é documentado em `docs/PLANO-CLASSIFICADOR-DEBITO-CREDITO.md`.
