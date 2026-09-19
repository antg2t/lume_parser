# Manifesto de equivalência e implantação

## Escopo congelado

Este handoff congela o núcleo decisório no estado de 18/09/2026. Ele reproduz a lógica de negócio, os hiperparâmetros e os contratos de saída dos quatro componentes abaixo:

| Componente de origem | SHA-256 no momento do handoff | Materializado em |
|---|---|---|
| `src/lume_ingestion/accounting_entries.py` | `93A323F8B606DAC70C5DE5B78DACA71DC7F72D71BD155EE704AD1B800E2CFD0C` | `reference_engine.py`: regras de Conta, transferência, memória textual, Complemento e campos de saída. |
| `src/lume_ingestion/client_template_model.py` | `DEEA69B39C188E6120618D0936443F87D15FE2483DCA827FA5A5E2F2C243D610` | `reference_engine.py`: memória exata e TF-IDF local com cabeças Conta/HIST. |
| `src/lume_ingestion/account_history_model.py` | `108F08CC377A715D743E97B0E5233D2BF744262366332216F8C63640514B7EC4` | `reference_engine.py`: frequência local C/D → HIST. |
| `src/lume_ingestion/global_account_model.py` | `8FFA2EC4212DEB794C4583F23DFB95823B0C2CE5D8DEBBAFC19919005DE69FE3` | `reference_engine.py`: fallback global semântico de Conta. |

O script não inclui parsers de XLSX/PDF/OFX nem o mapeamento automático de nome de banco para conta do plano: esses são problemas de ingestão, não classificação. A tabela `movements` precisa, portanto, entregar `financial_account` já resolvida. Isso torna o pacote realmente transportável: qualquer ETL que construa as tabelas válidas pode chamar o mesmo motor.

## Equivalência funcional que deve ser verificada

Para considerar uma migração aprovada, executar os dois motores com as mesmas tabelas normalizadas e comparar, linha por linha:

```text
id
date
debit_account
credit_account
amount
standard_history
complement
status
reason
account_probability
account_probability_source
history_probability
history_probability_source
history_cd_candidate
history_cd_probability
history_cd_support
history_cd_alternatives
```

Diferença em qualquer campo deve gerar investigação, não arredondamento silencioso. Para probabilidades de TF-IDF, use a mesma versão de `scikit-learn`, `random_state=42`, codificação UTF-8 e os hiperparâmetros preservados. O arquivo `requirements.txt` fixa a versão validada.

## Checklist de migração

1. Copiar esta pasta sem alterar nomes ou conteúdo.
2. Instalar Python 3.12+ e `scikit-learn==1.8.0`.
3. Rodar `python -m unittest test_reference_engine.py` e obter sucesso.
4. Converter uma competência histórica para `approved_examples`, `history`, `accounts` e `movements` respeitando [TABELAS-E-CONVERSAO.md](TABELAS-E-CONVERSAO.md).
5. Rodar a competência histórica no motor antigo e neste motor; fazer o diff de equivalência acima.
6. Iniciar um cliente novo com `client_auto_threshold=1.0`, sem inventar histórico. Só reduzir o limiar após 20 decisões revisadas e precisão anterior de pelo menos 92%.
7. Tratar `REVISAR CONTA` e `REVISAR RATEIO` como fila humana. Após revisão, gravar as linhas aprovadas nas tabelas, para uso apenas em competências futuras.
8. Não habilitar `global_examples` sem a chave semântica correta e auditoria de mapeamento único para o plano local.

## Referências de benchmark preservadas

| Arquivo original | SHA-256 | Conteúdo |
|---|---|---|
| `prequential_gate_results.json` | `92728D91FDFD1E945D0C424BD1FE801F8B8DAC38CFB869DB3810BFD73349FACC` | Política de limiar 92% e resultados por mês/cliente. |
| `account_pair_hist_results.json` | `16F3FA296E0CE403153974ED1AA8114301FEC3B3C83D7C665AB078AB8265AE3F` | Avaliação temporal do C/D → HIST. |

Os números resumidos estão em [VALIDACAO-E-LIMITES.md](VALIDACAO-E-LIMITES.md). Os arquivos de benchmark não são copiados para evitar transportar dados internos desnecessários; os hashes garantem a proveniência do contexto de decisão.

## Mudanças que exigem nova validação

Qualquer alteração abaixo muda o motor e exige uma nova avaliação temporal nas três classes de clientes: histórico abundante, histórico parcial e histórico vazio.

- tokenização, normalização de texto ou ordem de concatenação do movimento;
- limiares de similaridade 0,50/0,70;
- lista de regras semânticas e de tributos;
- versão/hiperparâmetros do TF-IDF ou `SGDClassifier`;
- regra de memória exata, suporte mínimo 2 ou `min_df`;
- regra de estabilidade do HIST (`support >= 2` e pureza 100%);
- transferência de HIST entre clientes;
- qualquer uso de informação da competência alvo no treino, no limiar ou no mapeamento de entidade.
