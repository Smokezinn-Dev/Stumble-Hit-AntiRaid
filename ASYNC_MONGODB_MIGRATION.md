# Migração assíncrona do MongoDB — etapas 1 e 2

## Objetivo
Migrar as operações de banco de dados dos módulos do bot para Motor (`AsyncIOMotorClient`), sem alterar regras de moderação, limites, cooldowns, coleções ou o nome do banco `gt_bot_moderacao`.

## Etapa concluída nesta entrega

- `commands_automod.py`:
  - `AutomodManager.get_config()` e `get_config_fresh()` agora são assíncronos e usam `await collection.find_one(...)`.
  - `AutomodManager.update()` agora usa `await collection.update_one(...)`.
  - O registro de infrações usa `await collection.insert_one(...)`.
  - Listeners e comandos que chamam esses métodos foram atualizados para `await`.
- A mesma alteração foi aplicada às pastas **Shadow Bot** e **Stumble Hit AntiRaid**.
- Nenhum nome de coleção, chave de configuração, limite, cooldown ou regra de punição do AutoMod foi intencionalmente alterado.

## O que ainda NÃO foi migrado

- Os demais módulos ainda contêm chamadas ao caminho síncrono `get_connection()`/PyMongo.
- A camada de compatibilidade síncrona e o cliente `MongoClient` permanecem temporariamente para evitar quebrar módulos ainda não migrados.
- Portanto, esta entrega é uma etapa intermediária: ela reduz bloqueios no fluxo do AutoMod, mas **não elimina todos os bloqueios síncronos do event loop**.

## Próximas etapas obrigatórias

1. Migrar módulos pequenos e isolados, incluindo leitura/gravação, comandos e todos os chamadores.
2. Migrar módulos de proteção de alto risco (Anti-Nuke, Anti-Evade, Anti-Link, Join Risk e Anti-Escalation) com revisão de cada caminho de decisão e fail-closed.
3. Migrar operações de cursor (`find`, iteração, contagem, agregação) e transações, se existentes.
4. Remover `MongoClient` e a compatibilidade síncrona somente quando não houver chamadas síncronas restantes.
5. Executar testes de regressão com MongoDB de teste e dois servidores Discord de homologação antes de produção.

## Validação desta etapa

- Todos os 78 arquivos Python das duas versões foram analisados com `ast.parse`: sem erros de sintaxe.
- `commands_automod.py` nas duas versões foi analisado individualmente: sem chamadas `get_connection()` e com operações Motor explicitamente aguardadas.
- Não foi possível executar testes reais de integração nesta etapa: dependências/credenciais/instância MongoDB e aplicações Discord de teste não foram fornecidas ao ambiente de edição.

## Variáveis de ambiente e Railway

As variáveis reais devem ser cadastradas somente no painel de variáveis do serviço correspondente no Railway. Não coloque tokens, URI do MongoDB, IDs privados ou outros segredos em arquivos do ZIP, commits, logs ou mensagens. Os arquivos `.example` servem apenas como modelo e devem permanecer sem valores secretos.

Para a configuração de owner, preencher `OWNER_USER_ID` no ambiente de cada serviço. Ativar também **Server Members Intent** nas configurações de cada aplicação Discord.

## Estado

**Etapa 1 concluída — AutoMod assíncrono. Migração geral em andamento. Não classificar como produção até concluir as etapas e testes acima.**


# Etapa 2 — Cases, Notes, Appeals e Warns via Motor

## Módulos migrados nesta etapa

- `commands_cases.py`:
  - `_next_case_id()` e `_create_case()` usam `get_connection_async()` e aguardam as operações Motor.
  - Consultas de cases e appeals usam `await find_one(...)`.
  - Inserções e atualizações de cases, notas e appeals usam `await insert_one(...)` / `await update_one(...)`.
  - Listagens usam cursor Motor com `to_list(length=...)`, mantendo os limites anteriores (15 cases, 20 notas e 15 appeals).
- `commands_slash.py`:
  - O menu de contexto Warn e o slash command `/warn` gravam com Motor.
  - A contagem de warns usa `await count_documents(...)`.
- Aplicado igualmente às duas versões, sem mudar coleções, filtros, limites de listagem ou mensagens dos comandos.

## Validação da etapa 2

- `commands_cases.py` e `commands_slash.py` não mantêm chamadas a `get_connection()` síncrono.
- As operações Motor identificadas nesses dois módulos estão aguardadas com `await`; cursores são materializados com `to_list`.
- A lógica preexistente de geração sequencial de IDs de cases/appeals foi preservada. Ela continua sujeita à mesma condição de concorrência que já existia; não foi introduzida uma nova estratégia de IDs nesta etapa.
- A validação é estática (AST/sintaxe e inspeção de chamadas). Não foram executados testes de integração com MongoDB/Discord reais.

## Estado atualizado

**Etapa 1 + etapa 2 concluídas.** O AutoMod, Cases/Notes/Appeals e gravação/contagem de Warns usam Motor nos módulos citados. Os demais módulos ainda podem usar PyMongo síncrono; a compatibilidade permanece ativa e a migração geral não está concluída.


# Etapa 3 — Branding e configurações de Staff

## Módulos alterados nesta etapa

- `commands_branding.py`: leitura da configuração de branding, atualizações de nickname/cor/footer e reset agora usam `get_connection_async()` e operações Motor aguardadas. O método interno de leitura também passou a ser assíncrono e seu chamador foi atualizado.
- `commands_staff_config.py`: leitura e gravação das configurações/metas, configuração de cargos automáticos e exclusão de pontos/histórico usam Motor com `await`.
- As alterações foram aplicadas às duas versões.

## Limite desta etapa

O fluxo de pontuação delegado a `StaffManager` em `commands_staff.py` ainda usa chamadas síncronas e permanece pendente para uma etapa própria. Portanto, não considerar todo o sistema de Staff migrado.

## Validação da etapa 3

- `commands_branding.py` e as operações diretas de banco em `commands_staff_config.py` não usam mais `get_connection()` síncrono.
- A análise sintática AST foi executada em todos os arquivos Python das duas versões: sem erros.
- Restam 186 referências textuais a `get_connection()` em cada versão.
- Não foram executados testes de integração reais com MongoDB/Discord nesta etapa.

## Estado atualizado

**Etapas 1, 2 e 3 concluídas parcialmente conforme os módulos listados.** Ainda há operações síncronas em outros módulos e no `StaffManager`; a camada de compatibilidade não pode ser removida. Não classificar como pronto para produção.


# Etapa 4 — Sistema de Staff via Motor

- `commands_staff.py`: `StaffManager` e todos os seus chamadores foram convertidos para operações Motor aguardadas. Consultas de ranking e histórico usam cursores assíncronos e preservam os limites anteriores.
- `commands_staff_config.py`: concessão e remoção administrativa de pontos aguardam o registro assíncrono.
- As regras, filtros, coleções, limites e nomes de campos foram preservados.
- Comentários explicativos foram acrescentados aos fluxos alterados para documentar a sequência e o motivo das operações assíncronas.
- Validação sintática AST em todos os módulos Python das duas versões: sem erros. Não houve integração real com MongoDB/Discord.

**Etapa 4 concluída para o fluxo de Staff.** A migração geral permanece em andamento e o PyMongo síncrono ainda não pode ser removido.


# Etapa 5 — Permissões por categoria e gráficos de estatísticas

- `commands_perm_categories.py`: leitura, gravação e reset de regras migrados para Motor. Todos os chamadores do manager aguardam os resultados.
- `commands_stats_graph.py`: configurações e consultas das três coleções de estatísticas migradas para Motor; cursores são materializados com `to_list(length=None)`.
- As alterações foram aplicadas nas duas versões com a identidade visual de cada bot preservada.
- Comentários em português explicam a conexão assíncrona, os `await`, os cursores e a invalidação de cache.
- Restam 174 referências a `get_connection()` síncrono por versão. Não remover a camada de compatibilidade.
- Validação sintática estática concluída; sem testes reais de integração. Consulte `AUDIT_ASYNC_MONGODB_ETAPA5.md`.

**Etapa 5 concluída para os dois módulos; migração geral ainda em andamento.**


# Etapa 6 — Logs de eventos via Motor

- `commands_event_logs.py`: leitura e atualização de configurações, listeners e comandos administrativos migrados para Motor. O reset também aguarda `delete_one()` antes de invalidar o cache e confirmar a operação.
- As categorias, filtros, valores padrão, listas de ignorados, permissões e mensagens foram preservados.
- Comentários em português documentam o fluxo assíncrono, a ordem de persistência/invalidação e os pontos de chamada.
- Restam 171 referências a `get_connection()` síncrono por versão. A compatibilidade PyMongo continua necessária.
- Validação sintática estática concluída; sem integração real com MongoDB/Discord. Consulte `AUDIT_ASYNC_MONGODB_ETAPA6.md`.

**Etapa 6 concluída para os logs de eventos; migração geral ainda em andamento.**

## Etapa 7 — sistema de logs
O `LogsManager` e seus consumidores foram migrados para Motor/`await`; ver `AUDIT_ASYNC_MONGODB_ETAPA7.md`.

## Etapa 8 — proteção de bots na whitelist
- `BotWhitelistManager` migrado para Motor; consumidores aguardam as operações.


## Etapa 9 — sistema de avisos via Motor
- `commands_warn_system.py`: `WarnManager`, cleanup, aplicação e consulta/remoção de avisos e registros de punição migrados para Motor.
- Todos os chamadores locais de `WarnManager` usam `await`; o comando de listagem materializa o cursor com limite de 20.
- Regras, limites, coleções, filtros, mensagens e progressão de punições foram preservados.
- Restam **153 referências textuais** a `get_connection()` síncrono por distribuição (162 na etapa 8).
- Consulte `AUDIT_ASYNC_MONGODB_ETAPA9.md`. A validação continua estática, sem integração real.

**Etapa 9 concluída; migração global ainda em andamento.**


## Etapa 10 — sistema de backups

- `commands_backup.py` migrado para Motor, incluindo cache de configuração, criação/listagem/detalhes/exclusão e restauração de dados.
- `BackupConfigCache.get()` agora é assíncrono; todos os consumidores locais aguardam a leitura.
- Referências sintáticas restantes a `get_connection()`: 144 por distribuição.

## Etapa 11 — commands_moderation_extra.py

Migradas para Motor as operações de purge logs, templocks e role persist. Mantidos comandos, aliases, permissões, limites, filtros e listener de reatribuição de cargos. Consulte `AUDIT_ASYNC_MONGODB_ETAPA11.md`.


## Etapa 13 — confirmação de ações na whitelist
- `commands_whitelist_confirm.py`: manager de configuração, leitura, gravação e todos os consumidores migrados para Motor com `await`. Cache, confirmação por DM, expiração, votação, ações protegidas, comandos e permissões preservados.
- Comentários em português adicionados nos fluxos alterados. Restam 119 chamadas executáveis síncronas por distribuição. Consulte `AUDIT_ASYNC_MONGODB_ETAPA13.md`.


## Etapa 14 — Sistema de verificação

`commands_verification.py` foi migrado para Motor. Configuração, logs e estatísticas agora aguardam as operações assíncronas. Chamadores do manager foram atualizados; comandos, aliases e regras existentes foram preservados. Consulte `AUDIT_ASYNC_MONGODB_ETAPA14.md`.


## Etapa 16 — Detector de suspeitos de raid

- `commands_raid_suspects.py` migrado integralmente para Motor nas operações de banco.
- Configurações, gravação/recuperação de suspeitos, registro de detecções e estatísticas agora usam `await`.
- Comandos, aliases, filtros, limites e regras preservados; comentários explicativos em português adicionados.
- Auditoria: `AUDIT_ASYNC_MONGODB_ETAPA16.md`.


## Etapa 18 — Anti-Escalation e controle global de proteção

- `commands_antiescalation.py` e `commands_protection_toggle.py` migrados para Motor nas duas distribuições.
- Managers e consumidores locais agora aguardam as operações assíncronas; cursor de toggles percorre com `async for`.
- Persistência antes de desligar e restauração antes de apagar o estado foram preservadas.
- Comandos, aliases, permissões, modos, durações e notificações mantidos. Comentários em português adicionados.
- Restam 89 chamadas executáveis síncronas por distribuição (98 na Etapa 17). Consulte `AUDIT_ASYNC_MONGODB_ETAPA18.md`.
- Validação estática sem erros; integração real ainda pendente.
