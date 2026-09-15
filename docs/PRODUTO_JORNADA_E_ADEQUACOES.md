# Produto Lume — jornada, estado da suíte e adequações

## Objetivo de produto

A Lume não deve ser apenas uma importadora de extratos. O produto é um motor
de **pré-contabilização e conciliação assistida**, que prepara lotes seguros
para o sistema contábil e melhora com as correções autorizadas do contador.

O principal ativo por empresa é:

```text
Empresa + plano de contas + histórico + regras + correções humanas
    = modelo contábil específico da empresa
```

Esse modelo é isolado por empresa. Dados brutos, regras particulares e
correções nunca podem ser reutilizados por outra empresa sem política explícita
de autorização, anonimização e governança.

## Módulos do produto

O direcionamento de produto consolida sete módulos, em vez de uma tela única
de importação:

```text
Empresas → Contabilidade → Inteligência → Movimentações
         → Conciliação → Exportação → BI
```

| Módulo | Responsabilidade | Estado na suíte atual |
|---|---|---|
| Empresas | empresa, usuários, permissões e parâmetros | ainda precisa de aplicação e banco |
| Contabilidade | plano, histórico, contas e regras locais | contratos e importadores disponíveis |
| Inteligência | padrões locais, regras e fallback versionado | motor determinístico + TF-IDF local/global |
| Movimentações | upload, detecção, normalização e lotes | funcional localmente |
| Conciliação | cruzar extrato, caixa e lançamentos | validação de saldos e conciliação inicial disponíveis |
| Exportação | lote aceito para cada sistema contábil | ainda precisa de adaptadores de exportação |
| BI | saldos, pendências e indicadores | ainda precisa de persistência e interface |

## Jornada do cliente

### 1. Cadastro da empresa

O operador cria a empresa, define a competência de trabalho, a moeda, o sistema
contábil de destino e as permissões. O produto cria um espaço de dados isolado
(`tenant_id`) e uma trilha de auditoria. Nenhum arquivo deve ser associado a
uma empresa por inferência de nome de pasta.

**Saídas:** empresa ativa, usuários autorizados, política de retenção e destino
de exportação escolhido.

### 2. Configuração contábil

O usuário envia o plano de contas da empresa e confirma as principais contas:
bancos, aplicações, clientes, fornecedores, tributos, despesas e receitas.
O sistema normaliza o plano e preserva a origem de cada linha.

**Saídas:** versão do plano, mapa de contas locais e validação de duplicidades.

### 3. Importação do histórico

O usuário envia períodos anteriores já contabilizados. O sistema normaliza cada
lançamento e cria uma memória local de pares débito/crédito, complemento,
contraparte e histórico padrão. O mês que será gerado não pode ser usado como
memória dele próprio.

**Saídas:** histórico versionado, cobertura de padrões e diagnóstico de linhas
não mapeáveis ao plano.

### 4. Base de regras e aprendizado

O sistema aplica nesta ordem: regras explícitas, memória textual da própria
empresa e fallback global permitido. A conta financeira e o lado débito/crédito
são fixados pelo sinal do movimento; a inteligência prevê somente a
contrapartida. Cada decisão guarda `reason`, `confidence`, versão do modelo e
evidência de origem.

**Saídas:** regras locais auditáveis, política de automação e fila de casos
sem evidência suficiente.

### 5. Importação de movimentações

O usuário envia extratos bancários e/ou controles de caixa. O produto detecta
o formato por conteúdo, preserva o original, gera RAW imutável, normaliza e
valida saldos. O lote é associado à competência e à empresa antes da geração.

**Saídas:** documentos normalizados, avisos de conciliação e eventos
financeiros rastreáveis.

### 6. Classificação e central de revisão

O motor prepara o lote contábil. Cada linha recebe um estado:

| Estado | Significado | Ação permitida |
|---|---|---|
| `LANCAR` | evidência e política permitem sugestão automática | aprovar, ajustar ou rejeitar |
| `REVISAR CONTA` | contrapartida não é suficientemente segura | contador escolhe conta e registra motivo |
| `REVISAR RATEIO` | movimento agregado precisa de desdobramento | contador cria as pernas e a memória de cálculo |
| Não identificado | fonte ou conta financeira ausente | corrigir a entrada ou criar regra |

A decisão humana precisa virar um evento de correção, e não sobrescrever a
previsão original. Isso permite medir assertividade e melhorar a memória local
no período seguinte.

### 7. Conciliação, exportação e BI

Após a revisão, o sistema concilia eventos, aprova o lote e cria o arquivo do
sistema contábil escolhido. O dashboard mostra somente dados persistidos e
reconciliados; indicadores ilustrativos como “% conciliado” ou “% automático”
nunca devem ser apresentados como verdade sem numerador, denominador, período e
estado do lote.

**Saídas:** lote exportável, arquivo de exportação, log de aprovação e visão de
saldos, fornecedores, clientes, tributos, aplicações, empréstimos e pendências.

## Ordem do MVP

Construir na ordem abaixo reduz risco de uma interface bonita sobre dados não
auditáveis:

1. cadastro da empresa e isolamento de dados;
2. plano de contas e informações principais;
3. histórico contábil anterior;
4. motor de regras, memória local e política de confiança;
5. importação de extrato/caixa e geração do lote;
6. central de revisão com registro de correções;
7. exportação do lote para um primeiro sistema contábil.

Conciliação avançada e dashboard entram imediatamente depois dessa fundação.

## Estado comprovado da suíte

O snapshot foi validado com 95 testes e três clientes de agosto/2026. Os dados
abaixo são de validação local e não devem ser tratados como promessa comercial.

| Cliente | Movimentos | Lançamentos | Prontos | Revisão | Acurácia top-1 do fallback |
|---|---:|---:|---:|---:|---:|
| LUFTKLIM | 352 | 352 | 199 | 153 | 86,0% |
| Martine | 311 | 311 | 94 | 217 | 57,6% |
| Vanguarda | 255 | 243 | 203 | 40 | 85,0% |

A métrica top-1 mede a classificação da contrapartida contra lançamentos
finalizados de agosto, com agosto fora do treino. Ela não substitui a
assertividade fim a fim do lote, que precisa de um comparador de eventos e
lançamentos aprovado pelo produto.

Formatos já testados:

| Documento | Formatos/layouts suportados |
|---|---|
| Nota fiscal de serviço | NFS-e Nacional XML e DANFSe PDF textual |
| Controle de caixa | XLSX e PDF atuais |
| Extrato bancário | Itaú PDF digital, Itaú XLSX tabular, Bradesco PDF atual e Bradesco Net Empresa XLS |
| Plano de contas | XLSX atual |
| Histórico de lançamentos | XLSX atual |

## Contratos que a aplicação deve persistir

| Entidade | Campos essenciais | Regra |
|---|---|---|
| Empresa | `id`, configuração, política e destino contábil | isolada por tenant |
| Documento | hash, tipo, origem, competência, versão e estado | original imutável |
| Artefato | RAW, normalizado, resultado e evidências | auditável e versionado |
| Plano | empresa, versão e contas | não substituir a versão usada por um lote |
| Histórico | empresa, período, linhas e origem | separar referência de treino/avaliação |
| Lote | empresa, competência, fontes e política | estados: rascunho, revisão, aprovado, exportado |
| Lançamento sugerido | débito, crédito, valor, confiança, motivo e evidência | previsão nunca é apagada |
| Correção humana | sugestão original, decisão, usuário e justificativa | alimenta regra/memória futura |
| Exportação | layout, versão, arquivo, usuário e data | somente lote aprovado |

## Adequações necessárias para produto funcional

### P0 — antes de colocar clientes em produção

1. API/autenticação, isolamento por empresa, RLS e auditoria.
2. Armazenamento privado de originais e artefatos por hash; URLs assinadas e
   retenção configurável.
3. Orquestração assíncrona de upload, extração, normalização, validação e
   geração, com idempotência pelo hash.
4. Modelagem de empresa, plano versionado, histórico, lote, linha sugerida e
   correção humana.
5. Central de revisão que conserve previsão, correção, motivo e evidência.
6. Política explícita de treino, autorização, corte temporal e proteção contra
   vazamento de golden.
7. Exportador de um sistema contábil alvo, começando por layout versionado e
   arquivo XLSX/CSV validado pelo contador.

### P1 — qualidade operacional

1. Comparador fim a fim entre evento financeiro, sugestão e lançamento final.
2. Conciliação persistida entre caixa, banco, duplicatas e movimentos
   agregados/desdobrados.
3. Catálogo de layouts e onboarding de novos bancos sem regra por nome de
   cliente.
4. Dashboard com métricas de lote: entradas, geradas, revisão, aprovadas,
   precisão, cobertura, pendências e divergências.
5. OFX/QFX, CSV e outros layouts bancários com fixtures reais.

### P2 — evolução controlada

1. Conectores adicionais de sistemas contábeis.
2. OCR para documentos escaneados com revisão reforçada.
3. Regras configuráveis por empresa com aprovação e versionamento.
4. Recursos de IA generativa somente como sugestão limitada, com fonte,
   confiança, política de privacidade e fallback conservador.

## Guardrails de produto

- Não criar regra condicionada ao nome de uma empresa.
- Não inventar conta fora do plano vigente.
- Não usar lançamento final do mês para classificá-lo.
- Não automatizar pagamento composto sem memória de cálculo ou revisão.
- Não misturar dados de empresas em memória local ou avaliação.
- Não publicar documentos reais, RAWs, CNPJ/CPF ou extratos em ferramentas sem
  autorização e contrato de tratamento de dados.
- Medir precisão e cobertura separadamente; alta precisão com baixa cobertura
  é preferível a lançamento automático incorreto.
