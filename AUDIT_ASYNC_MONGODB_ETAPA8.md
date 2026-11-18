# Auditoria da migração assíncrona — Etapa 8

## Escopo
- `commands_bot_whitelist_protection.py`: `BotWhitelistManager.get_config`, `get_config_fresh` e `update` migrados para Motor.
- Todas as leituras e gravações da coleção `bot_whitelist_protection_config` agora são aguardadas.
- Consumidor externo em `commands_antinuke.py` atualizado para aguardar a leitura fresca.

## Comportamento preservado
- Cache TTL, defaults, filtro por `guild_id` e formato dos documentos mantidos.
- `get_config_fresh` continua ignorando o cache para decisões sensíveis de proteção.
- A escrita termina antes da invalidação do cache. Nenhuma regra, limiar ou permissão foi alterada intencionalmente.

## Validação e limitações
- AST/compilação sintática verificada nas duas versões.
- Ainda são necessários testes de integração com Discord e MongoDB reais; esta etapa não é certificação de produção.
