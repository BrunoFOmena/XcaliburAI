# Versionamento

XcaliburAI segue o [Versionamento Semântico 2.0.0](https://semver.org/lang/pt-BR/): **`MAJOR.MINOR.PATCH`**. O projeto começa em **`0.0.0`**.

| Campo | Quando incrementar | Exemplo |
|---|---|---|
| **MAJOR** | Alterações incompatíveis com versões anteriores (breaking changes) | `1.0.0` → `2.0.0` |
| **MINOR** | Novas funcionalidades compatíveis com versões anteriores | `1.1.0` → `1.2.0` |
| **PATCH** | Correções de bugs e pequenos ajustes compatíveis | `1.0.1` → `1.0.2` |

Ao incrementar um campo, os campos à direita voltam a zero (`1.4.7` → `1.5.0` → `2.0.0`).

## O que conta como "API pública"

Para decidir se uma mudança é breaking, considere pública a superfície que outras pessoas usam:

- comandos, flags e códigos de saída da CLI `xcalibur`;
- o formato do `manifest.yaml` (`schema_version`) e o contrato `DomainPlugin`;
- o grupo de entry points `xcalibur.domains`;
- o formato do registro de proveniência (`run-<id>.json`);
- as interfaces `Agent`, `LLMProvider` e `WorkflowEngine`.

Mudanças internas não listadas não exigem MAJOR.

## Fase 0.x (antes da 1.0.0)

Enquanto a versão for `0.y.z`, a API ainda não é estável (SemVer, item 4). Neste projeto, durante a fase 0.x:

- **MINOR** (`0.1.0` → `0.2.0`) pode conter breaking changes, sempre listadas no CHANGELOG;
- **PATCH** (`0.1.0` → `0.1.1`) contém apenas correções compatíveis;
- a `1.0.0` marca a primeira API considerada estável.

## Marcos (milestones)

| Versão | Conteúdo |
|---|---|
| `0.0.0` | Fundação do repositório e esqueleto do núcleo |
| `0.1.0` | MVP: conversar, rodar o ECHOLAS e interpretar o resultado (issues **P0**) |
| `0.2.0` | Relatórios, aquisição de dados, sessões, HPC e mais provedores (issues **P1**) |

## Fonte única da versão

A versão vive em **um só lugar**: [`src/xcalibur/version.py`](../src/xcalibur/version.py). O `pyproject.toml` a lê dinamicamente. Os pacotes de domínio mantêm a mesma versão do núcleo enquanto o projeto estiver na fase 0.x, e o teste `tests/test_versioning.py` garante a consistência.

## Como publicar uma versão

1. Em uma branch cortada de `homolog`, atualize `version.py` (e a versão dos pacotes de domínio e do `manifest.yaml`).
2. Mova as entradas de `Unreleased` para uma nova seção `## [X.Y.Z] - AAAA-MM-DD` no [CHANGELOG](../CHANGELOG.md).
3. Abra o PR para `homolog`; após aprovação, merge e teste, abra o PR `homolog` → `main` e faça o merge com merge commit (ver [CONTRIBUTING](../CONTRIBUTING.md)).
4. Com o merge em `main`, crie a tag anotada `vX.Y.Z` nesse commit e publique o release no GitHub.

Tags são sempre criadas a partir da `main` e nunca movidas ou apagadas.

## Qual incremento escolher? (guia rápido)

- Corrigi um bug sem mudar o comportamento documentado → **PATCH**.
- Adicionei comando, flag, agente, domínio ou parâmetro opcional → **MINOR**.
- Removi ou renomeei algo público, mudei o `schema_version` do manifesto ou o formato da proveniência → **MAJOR** (ou **MINOR** na fase 0.x, com aviso no CHANGELOG).
- Só documentação, testes ou CI → não altera a versão.
