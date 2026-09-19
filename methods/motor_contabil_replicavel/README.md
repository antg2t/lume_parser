# Motor contábil reproduzível por tabelas

Este diretório é uma entrega autocontida da inteligência aprovada em 18/09/2026:

1. previsão da conta de contrapartida a partir de um movimento financeiro;
2. previsão local de `HIST` a partir do par Débito/Crédito já definido;
3. transformação determinística do texto de caixa/extrato para o `Complemento` da planilha HIST.

Ele não contém dados de clientes. Um ambiente novo só precisa fornecer as tabelas no contrato de [data-contract.json](data-contract.json), instalar `scikit-learn==1.8.0` e executar o script de referência. Nenhuma decisão depende de caminhos, nomes de arquivos ou da posição de uma linha numa planilha.

## Começo rápido

```powershell
cd motor-contabil-replicavel
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python reference_engine.py --input example-input.json --output resultado.json
```

O resultado esperado para o exemplo é [example-output.json](example-output.json). Ele demonstra uma entrada PIX, conta financeira `1000012`, contrapartida `2000001`, `HIST=551` e a transformação `RECEBIMENTO PIX - ACME - DOC 123`.

## O que precisa ser entregue pelo novo ambiente

| Tabela do contrato | Origem normal | Finalidade |
|---|---|---|
| `accounts` | Plano de Contas | Valida códigos, identifica contas financeiras e resolve regras semânticas. |
| `history` | Planilha HIST/Lançamentos revisados | Memória textual e modelo C/D → HIST. |
| `approved_examples` | Junção já revisada de extrato/caixa com o lançamento contábil correto | Treinamento local de texto → conta (e, quando existente, texto → HIST). |
| `movements` | Extrato ou caixa do mês alvo | Única fonte dos movimentos novos que receberão previsão. |
| `global_examples` (opcional) | Linhas aprovadas de outros clientes, já traduzidas para chave semântica | Fallback global para **Conta**, nunca para HIST. |

O elemento indispensável para o modelo de Conta é `approved_examples`: a planilha HIST isolada não contém o texto original de banco/caixa, portanto não permite treinar com fidelidade a relação “texto bruto → conta”. O campo `financial_account` também deve chegar preenchido nos movimentos; ele é a conta bancária/aplicação do plano, não o nome do banco impresso no extrato.

Leia [TABELAS-E-CONVERSAO.md](TABELAS-E-CONVERSAO.md) antes de gerar a primeira carga.

## Ordem de decisão — não alterar

```text
MOVIMENTO DO MÊS ALVO
|
+-- transferência espelhada? (mesma data, mesmo valor absoluto, sinal oposto,
|   duas contas financeiras distintas e texto de transferência)
|   `-- sim: D/C entre as duas contas financeiras; LANCAR
|
`-- mais de um tributo no texto?
    `-- sim: REVISAR RATEIO; não inferir uma única conta

    `-- não
        |
        +-- regra semântica explícita: rendimento, tarifa/IOF/encargos,
        |   capitalização ou exatamente um tributo
        |
        +-- memória textual local: melhor Similarity(texto_movimento,
        |   Complemento_HIST), com corte >= 0,50
        |
        +-- modelo local do cliente: memória exata, depois TF-IDF
        |   de palavras + caracteres; cabeças independentes de Conta e HIST
        |
        +-- modelo global semântico opcional: só se o local não aprovar
        |
        `-- padrão por direção: sugestão em REVISAR CONTA, nunca automática

APÓS DEFINIR D/C
|
`-- modelo local C/D -> HIST:
    P(HIST | débito, crédito) = frequência no histórico anterior
    só escreve HIST automaticamente se suporte >= 2 e pureza = 100%
```

A sequência é intencional: fornecedor é um sinal importante, mas não substitui modalidade, direção e conta financeira. A busca pelo último fornecedor foi testada e rejeitada como regra geral porque reduz a precisão em pagamentos de natureza distinta para a mesma entidade.

## Campos de saída e probabilidades

Todos os lançamentos retornam os campos abaixo. Eles precisam ser preservados na tabela destino/auditoria.

| Campo | Significado exato |
|---|---|
| `debit_account`, `credit_account` | Contas previstas; o valor é sempre o valor absoluto da linha de origem. |
| `standard_history` | HIST efetivamente aplicado. Pode ficar nulo sem histórico local confiável. |
| `complement` | Texto no padrão da coluna Complemento do HIST; é determinístico. |
| `account_probability` | Probabilidade top-1 do TF-IDF, quando a fonte é `client_tfidf`/`global_tfidf`; 1,0 para regra ou memória exata; score de similaridade quando a fonte é `text_similarity`; 0,0 para padrão por direção. |
| `account_probability_source` | Semântica do número: `deterministic_rule`, `text_similarity`, `exact_memory`, `client_tfidf`, `global_tfidf`, `matched_transfer` ou `direction_default`. |
| `history_probability` | Probabilidade/score do HIST efetivamente aplicado. Para C/D estável, é a frequência empírica do par. |
| `history_cd_candidate` | Melhor HIST proposto pelo modelo C/D, ainda que o par seja ambíguo e não tenha sido aplicado. |
| `history_cd_probability` | `frequência_do_HIST_vencedor / todos_os_HIST_do_par`. |
| `history_cd_support`, `history_cd_alternatives` | Quantidade de precedentes do par e de HISTs distintos do par. |
| `status` | `LANCAR`, `REVISAR CONTA` ou `REVISAR RATEIO`. |
| `reason` | Trilha compacta da regra aplicada; `hist_contas:Nx` indica preenchimento C/D com N precedentes. |

Uma probabilidade de classificador não é automaticamente calibrada como probabilidade real de acerto. A origem do score deve sempre ser lida junto do número. Em especial, `1.0` em `account_probability_source=exact_memory` significa repetição exata observada; não significa garantia estatística fora da amostra.

## Regra de publicação automática de Conta

O modelo entrega sugestão para toda linha, mas publicar automaticamente exige uma porta de confiança prequencial:

- limiares possíveis: `0,80`, `0,85`, `0,90`, `0,95`, `0,98`, `1,00`;
- para o mês M, escolher o **menor** limiar cuja precisão nos meses revisados `< M` seja pelo menos 92%, com no mínimo 20 decisões;
- se não houver evidência suficiente, usar `1,00`;
- a revisão do mês M só pode alimentar a escolha do mês seguinte.

A função `choose_prequential_threshold` materializa essa regra. A porta deve ser aplicada à probabilidade de **Conta** e não à de HIST. O HIST possui sua própria regra de estabilidade C/D.

## Evidência que fundamenta a política

Walk-forward temporal em 3 empresas, sem usar o gabarito do mês alvo:

| Métrica de Conta | Vanguarda | Martine | Luftklima | Total |
|---|---:|---:|---:|---:|
| Precisão das decisões automáticas | 94,9% | 94,6% | 94,4% | **94,6%** |
| Cobertura automática | 75,3% | 41,4% | 69,5% | **63,6%** |
| Linhas automáticas | 629/835 | 410/991 | 1.431/2.060 | 2.470/3.886 |
| Acurácia da sugestão sem porta | 89,0% | 85,5% | 84,1% | 85,5% |

Para o HIST por par C/D, com as contas corretas como entrada:

| Cliente | Cobertura estável | Precisão quando cobre | Situação |
|---|---:|---:|---|
| Vanguarda | 89,2% (745/835) | 99,87% | Pode preencher automaticamente após D/C confirmado. |
| Martine | 0% | — | Não havia HIST preenchido em meses anteriores. |
| Luftklima | 0% na avaliação disponível | — | Meses anteriores sem HIST preenchido. |

As métricas são limites observados, não promessa para um novo cliente. Veja [VALIDACAO-E-LIMITES.md](VALIDACAO-E-LIMITES.md) para a metodologia e os riscos conhecidos.

## Estrutura do handoff

| Arquivo | Conteúdo |
|---|---|
| `reference_engine.py` | Implementação executável, sem dependência deste repositório. |
| `data-contract.json` | Contrato de integração legível por máquina. |
| `example-input.json` / `example-output.json` | Caso de aceitação executável. |
| `TABELAS-E-CONVERSAO.md` | Dicionário de campos, joins e normalização. |
| `LOGICA-DETALHADA.md` | Regras, modelos, fórmulas e prioridades. |
| `VALIDACAO-E-LIMITES.md` | Benchmarks, protocolo temporal e decisões rejeitadas. |
| `MANIFESTO-DE-EQUIVALENCIA.md` | Versões, hashes de origem e checklist de migração. |

## Limites explícitos

- Não é permitido usar `HIST`, `Complemento`, Conta, revisão humana ou qualquer dado do mês alvo para treinar/preencher aquela mesma competência.
- O fallback global não transfere HIST entre clientes. Códigos HIST são locais até haver um contrato explícito de equivalência.
- Se uma conta financeira não estiver identificada, a linha fica em `REVISAR CONTA`; não escolher banco por palpite textual neste núcleo.
- Movimento com mais de um tributo é `REVISAR RATEIO`; quebrar o valor entre contas exige dado adicional, não inferência.
- A transformação de Complemento padroniza a redação e preserva evidência disponível; ela não cria fornecedor, NF, CNPJ ou natureza contábil inexistentes no movimento.
