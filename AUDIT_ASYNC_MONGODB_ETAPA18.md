# Auditoria MongoDB assíncrono — Etapa 18

## Escopo

Foram migrados para Motor os módulos `commands_antiescalation.py` e `commands_protection_toggle.py` nas duas distribuições.

- `AntiEscalationManager.get_config`, `get_config_fresh` e `update` agora são assíncronos.
- As leituras, gravações, contagens e agregações do Anti-Escalation aguardam as operações Motor.
- `ProtectionToggleManager.get_config` e `update` agora são assíncronos.
- A recuperação de toggles persistidos percorre o cursor com `async for`.
- Desativação e restauração aguardam persistência, logs e remoção do estado antes de concluir.
- Os consumidores locais do `AntiEscalationManager` e do `AutomodManager` foram atualizados para `await`.
- As chamadas ao `AntiNukeManager` permanecem síncronas nesta etapa porque o manager do Anti-Nuke ainda não foi migrado; sua interface e comportamento não foram alterados.

## Preservação de funcionalidades

- Foram preservados os métodos, comandos, aliases, decorators, permissões, mensagens, módulos alternáveis, modos manual/automático, duração, notificações, logs e sequência de restauração.
- Comparação AST com a distribuição anterior: `commands_antiescalation.py` manteve os 2 métodos decorados; `commands_protection_toggle.py` manteve os 10 métodos decorados.
- Comentários em português explicam o uso de Motor, o cursor assíncrono e a ordem de persistência/restauração.
- A única diferença entre as duas cópias de `commands_antiescalation.py` é o texto de branding no rodapé (Shadow Bot / Stumble Hit AntiRaid). `commands_protection_toggle.py` permanece idêntico.

## Validação estática

- 39 arquivos Python analisados por distribuição.
- `ast.parse` e `py_compile`: 0 erros.
- `commands_antiescalation.py`: 0 chamadas executáveis a `get_connection()` síncrono.
- `commands_protection_toggle.py`: 0 chamadas executáveis a `get_connection()` síncrono.
- Chamadas de manager e CRUD Motor verificadas: nenhuma chamada não aguardada nos dois módulos.
- Chamadas executáveis síncronas restantes no projeto: **89 por distribuição**, contra 98 na Etapa 17 (9 eliminadas nesta etapa).
- Referência inicial: 211 chamadas executáveis. Total eliminado: 122; progresso aritmético: **57,8%**.

## Limitações

Esta auditoria é estática. Não foram executados testes com tokens reais, servidores Discord, instância MongoDB real, hierarquia de cargos, concorrência, limites da API ou reinicialização de processo. Não considerar produção certificada. A compatibilidade PyMongo em `database.py` permanece necessária enquanto houver chamadas síncronas no projeto.
