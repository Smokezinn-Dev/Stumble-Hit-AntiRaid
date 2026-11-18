# Auditoria MongoDB assíncrono — etapa 10

## Escopo

Migração completa do módulo `commands_backup.py` para Motor nas duas distribuições.

## Alterações

- `BackupConfigCache.get()` passou a ser assíncrono e consulta `backup_config` via `get_connection_async()`; leituras em cache continuam sem acesso ao banco.
- Criação de backup agora aguarda leituras das configurações, inserções de backup/textos, contagem de documentos e exclusão dos backups mais antigos.
- Cursores de backups antigos e de listagens/detalhes são materializados com `to_list()`, preservando filtros, ordenações e limites existentes.
- Comandos de criação, listagem, detalhes e exclusão agora usam operações Motor aguardadas.
- Configuração dos canais de texto e do limite de mensagens agora persiste com `await update_one()` antes de invalidar o cache.
- Restauração de configurações e leitura dos textos salvos agora usam operações assíncronas.
- Todos os consumidores locais de `BackupConfigCache.get()` foram atualizados para `await`.
- Foram adicionados comentários em português explicando a conexão Motor, a materialização de cursores e a ordem das operações de persistência/cache.

## Comportamento preservado

Foram mantidos os nomes das coleções, filtros, campos, limite de 20 itens na listagem, ordenação por data, limite de backups por servidor, configuração de canais de texto, limite de mensagens, modos de restauração, permissões, mensagens e fluxo de confirmação. Não foram intencionalmente alteradas regras de backup/restauração nem operações do Discord.

## Verificações estáticas

- 39 arquivos Python analisados por distribuição; nenhum erro de sintaxe via `ast.parse`.
- `commands_backup.py`: zero chamadas `get_connection()` síncronas.
- Todas as chamadas locais a `BackupConfigCache.get()` estão aguardadas.
- Referências sintáticas `get_connection()` restantes no projeto: **144 por distribuição**, contra 153 na etapa 9 (redução de 9).
- As duas cópias de `commands_backup.py` são idênticas.
- Nenhum arquivo `.env` foi incluído no pacote.

## Limitações

A validação é estática. Não foram executados testes reais com MongoDB, Discord, permissões, limites de API, concorrência ou persistência em homologação. O projeto não está certificado para produção. A compatibilidade PyMongo em `database.py` deve permanecer até a migração global e os testes de regressão serem concluídos.
