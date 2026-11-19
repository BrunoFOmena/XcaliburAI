# XcaliburAI

**Uma camada de agentes de IA sobre motores de workflow para genômica.**

Em vez de montar e rodar pipelines na mão, o usuário conversa. O sistema escolhe, configura, executa e interpreta o pipeline adequado e entrega resultados e um relatório reprodutível.

> Status: fase de concepção. Ainda não há código; este README registra a visão e o escopo inicial.

---

## Visão

Pipelines de genômica são reprodutíveis e robustos, mas exigem conhecimento técnico para escolher ferramentas, ajustar parâmetros e interpretar saídas. XcaliburAI coloca agentes de IA entre o usuário e o motor de workflow (Nextflow):

```
usuário  ──conversa──▶  supervisor  ──▶  agentes de domínio  ──▶  motor de workflow  ──▶  resultados
                                              ▲                                              │
                                              └──────────── interpretação e relatório ◀──────┘
```

Exemplo de uso pretendido:

> "Rode o pipeline nas minhas amostras contra a referência M2904, mostre a estrutura populacional e diga a qual clado pertence a amostra nova."

## Arquitetura

O desenho segue o padrão de sistemas multiagente com plugins, adotado por projetos como o [Lobster AI](https://github.com/the-omics-os/lobster). Adotamos a **ideia** da arquitetura, mas **todo o código é de implementação própria**; não copiamos código de terceiros.

- **Núcleo:** supervisor multiagente, registro de agentes por plugin, configuração de LLM (o usuário traz o próprio provedor), workspace e proveniência.
- **Pacote de domínio:** cada domínio é um pacote independente com quatro partes:
  1. um ou mais agentes;
  2. os módulos do workflow (Nextflow);
  3. o ambiente de container;
  4. um **manifesto** descrevendo entradas, saídas, parâmetros, validações e valores padrão seguros.
- **Camada de execução:** um adaptador único para o motor de workflow (Docker local, HPC e nuvem). O agente só pede "rodar este pacote com estes parâmetros".
- **Camada de interpretação:** agentes que leem as saídas (VCF, árvores, tabelas) e geram figuras e relatórios.
- **Aquisição de dados:** agente comum para bancos públicos (SRA, ENA, NCBI) e literatura.
- **Proveniência:** tudo que foi usado em uma execução (versões, parâmetros, referência) é registrado e exportável.

Adicionar um domínio é adicionar um pacote, sem alterar o núcleo. Isso também permite contribuições de terceiros.

## Escopo inicial: domínio ECHOLAS

O primeiro e, por ora, único domínio é o do **ECHOLAS** (*Evolutionary Comparison and Hierarchical Organization on Leishmania Analysis*): genômica populacional a partir de leituras curtas.

Etapas cobertas pelo pacote:

1. **QC e trimming adaptativo** das leituras;
2. **Alinhamento e chamada de variantes** contra um genoma de referência;
3. **Genotipagem conjunta** entre amostras;
4. **Filtragem** de SNPs e INDELs;
5. **PCA e filogenia** de máxima verossimilhança, além de análises de diversidade, Fst e divergência de clados.

O ECHOLAS é a origem do pipeline e pertence ao cofounder [@MCamposDA](https://github.com/MCamposDA); o repositório original é [MCamposDA/ECHOLAS](https://github.com/MCamposDA/ECHOLAS).

## Proposta de expansão de domínios

Depois que o domínio ECHOLAS estiver sólido, a ideia é crescer por **novos pacotes**, nesta ordem sugerida (sujeita a revisão):

| Ordem | Domínio | Escopo |
|---|---|---|
| 1 | Genômica populacional e de patógenos | **ECHOLAS (escopo atual)** |
| 2 | Variantes humanas e clínicas | WGS/WES, anotação e priorização de variantes |
| 3 | RNA-seq bulk | Quantificação, expressão diferencial, enriquecimento |
| 4 | Metagenômica e microbioma | Perfilamento taxonômico e funcional |

**Fonte de pipelines:** o [nf-core](https://nf-co.re) (catálogo aberto de pipelines Nextflow revisados pela comunidade) pode servir como **base inicial** para domínios futuros, por meio de um adaptador genérico. A preferência do projeto, porém, é a **implementação própria** dos pipelines e agentes; o nf-core entra como ponto de partida, não como destino.

**Princípio de crescimento:** poucos domínios, bem feitos. Largura só entra depois de profundidade, para evitar um produto raso.

## Riscos reconhecidos

- **Dispersão:** ampliar domínios cedo demais dilui a qualidade. Por isso o escopo inicial é um único domínio.
- **Custo de computação:** pipelines como o do ECHOLAS (GATK, joint genotyping) pedem máquinas fortes; a execução precisa suportar Docker local, HPC e nuvem.
- **Erro silencioso:** o agente pode escolher parâmetros inadequados e o resultado parecer plausível. O manifesto de cada pacote deve trazer validações e valores padrão seguros, e todo relatório deve registrar o que foi usado.

## Cofounders

XcaliburAI é co-fundado por dois cofounders, com o mesmo nível de gerência e autoridade de decisão:

| Cofounder | GitHub |
|-----------|--------|
| Bruno F. Omena | [@BrunoFOmena](https://github.com/BrunoFOmena) |
| MCamposDA | [@MCamposDA](https://github.com/MCamposDA) |

## Licença

A definir.
